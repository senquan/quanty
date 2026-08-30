"""Tushare A 股日线行情数据源适配器

支持 A 股（含指数/ETF）日线行情接入：前复权收盘价直接作为 close，
使清洗流水线的 adjust 步骤（adj_close = close）天然得到真实前复权价，
从而技术因子（如 RSI）可基于真实 A 股数据计算。

代码格式：600519.SH / 000001.SZ / 600519.SH
依赖配置：TUSHARE_TOKEN（见 app.core.config.settings）
"""
from datetime import datetime

import pandas as pd

from app.core.config import settings
from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource
from app.ingestion.schemas import RawBar

logger = get_logger(__name__)


class TushareSource(BaseSource):
    name = "tushare"

    def _client(self):
        try:
            import tushare as ts
        except ImportError as e:
            raise IngestionError("未安装 tushare，请执行 pip install tushare") from e
        token = getattr(settings, "TUSHARE_TOKEN", None)
        if not token:
            raise IngestionError("缺少 TUSHARE_TOKEN，无法访问 Tushare")
        ts.set_token(token)
        return ts

    @staticmethod
    def _normalize_date(d: str) -> str:
        """将 2020-01-01 规整为 20200101"""
        return d.replace("-", "").replace("/", "")

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
    ) -> pd.DataFrame:
        ts = self._client()
        # 频率映射：当前仅支持日线（A 股因子多为日频）
        freq_map = {"1d": "D", "1h": "60", "1m": "1"}
        tushare_freq = freq_map.get(freq, "D")
        if tushare_freq != "D":
            raise IngestionError(f"Tushare 行情源暂仅支持日线(1d)，收到: {freq}")

        start_s = self._normalize_date(start)
        end_s = self._normalize_date(end)

        # 主路径：基础日线接口（免费、稳定、不限频于 qfq），不复权，拿原始 OHLC
        try:
            df = ts.pro_bar(
                ts_code=symbol,
                start_date=start_s,
                end_date=end_s,
                freq=tushare_freq,
                adj=None,
            )
        except Exception as e:  # Tushare 限流/网络/权限
            raise IngestionError(f"Tushare pro_bar({symbol}) 失败: {e}") from e

        if df is None or df.empty:
            raise IngestionError(f"Tushare 返回空数据: {symbol}")

        # 复权因子：拉取「全历史」adj_factor，取全局 f_latest/f_first 作为归一化基准，
        # 使 close 为全局一致的前复权价（qfq），避免多次增量窗口各自基准不一致导致
        # 跨越分红/送转日的收益、动量误差。adj_factor 有 1 次/分钟限频，失败则降级
        # 不复权（close 退化为原始价，adj_factor/hfq_close 置空）。
        f_latest = 1.0
        f_first = 1.0
        f_map = None
        try:
            adj = ts.pro_api().query(
                "adj_factor", ts_code=symbol, start_date="19900101", end_date=end_s
            )
            if adj is not None and not adj.empty:
                adj = adj.sort_values("trade_date")
                f_latest = float(adj["adj_factor"].max())
                f_first = float(adj["adj_factor"].min())
                f_map = adj.set_index("trade_date")["adj_factor"].astype(float)
                logger.info("Tushare adj_factor 获取完成", extra={"symbol": symbol})
        except Exception as e:
            logger.warning(
                "Tushare adj_factor 获取失败（降级不复权基准=1，含权收益可能有偏）",
                extra={"symbol": symbol, "reason": str(e)[:120]},
            )

        df = df.copy()
        if f_map is not None:
            f = df["trade_date"].map(f_map).astype(float)
        else:
            f = pd.Series(1.0, index=df.index)
        f = f.fillna(f_latest)
        scale = f / f_latest  # qfq 相对基准（最新一日 = 原始价）
        df["open"] = df["open"] * scale
        df["high"] = df["high"] * scale
        df["low"] = df["low"] * scale
        df["close"] = df["close"] * scale
        # 无复权因子（降级）时 adj_factor/hfq_close 置空，标记下游不可用
        df["adj_factor"] = f if f_map is not None else pd.NA
        df["hfq_close"] = (
            (df["close"] * (f_latest / f_first)) if (f_map is not None and f_first) else pd.NA
        )

        # 剔除关键价格为空的行（停牌等）
        df = df.dropna(subset=["open", "high", "low", "close"])
        # 剔除基础异常行（high<low、非正价格），避免污染清洗流水线
        df = df[(df["high"] >= df["low"]) & (df["close"] > 0) & (df["open"] > 0)]
        if df.empty:
            raise IngestionError(f"Tushare 有效数据为空: {symbol}")

        df = df.sort_values("trade_date").reset_index(drop=True)
        rows: list[RawBar] = []
        for _, row in df.iterrows():
            ts_dt = datetime.strptime(str(row["trade_date"]), "%Y%m%d")
            af = row.get("adj_factor")
            hf = row.get("hfq_close")
            rows.append(
                RawBar(
                    symbol=symbol,
                    timestamp=ts_dt,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("vol", row.get("volume", 0)) or 0),
                    source=self.name,
                    freq=freq,
                    adj_factor=None if pd.isna(af) else float(af),
                    hfq_close=None if pd.isna(hf) else float(hf),
                )
            )
        logger.info(
            "Tushare 拉取完成",
            extra={"task": "ingest", "symbol": symbol, "rows": len(rows)},
        )
        return self._to_dataframe(rows)
