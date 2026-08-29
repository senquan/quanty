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

        # 主路径：基础日线接口（免费、稳定、不限频于 qfq），不复权
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

        # 尝试叠加前复权因子（adj_factor 有 1 次/分钟限频，失败则降级不复权）
        try:
            adj = ts.pro_api().query(
                "adj_factor", ts_code=symbol, start_date=start_s, end_date=end_s
            )
            if adj is not None and not adj.empty:
                df = df.merge(adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
                last_factor = df["adj_factor"].iloc[-1]
                df["open"] = df["open"] * df["adj_factor"] / last_factor
                df["high"] = df["high"] * df["adj_factor"] / last_factor
                df["low"] = df["low"] * df["adj_factor"] / last_factor
                df["close"] = df["close"] * df["adj_factor"] / last_factor
                df["adj_factor"] = df["adj_factor"].astype(float)
                logger.info("Tushare 前复权完成", extra={"symbol": symbol})
        except Exception as e:
            logger.warning(
                "Tushare adj_factor 降级为不复权（限频/权限），RSI 等相对类因子不受影响",
                extra={"symbol": symbol, "reason": str(e)[:120]},
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
                )
            )
        logger.info(
            "Tushare 拉取完成",
            extra={"task": "ingest", "symbol": symbol, "rows": len(rows)},
        )
        return self._to_dataframe(rows)
