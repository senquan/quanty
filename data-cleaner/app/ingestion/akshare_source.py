"""Akshare A 股日线数据源适配器（北交所主力源 / 全市场备用源）

## 为什么需要它

| 场景 | 问题 |
|---|---|
| **北交所（.BJ）** | pandadata SDK 明确不支持（报「后缀必须为SH或SZ」），库里 341 只北交所标的**只能**走 akshare |
| **前/后复权** | alphafeed 的 `ex-factors` 端点返回 **403 No permission**（套餐不含），tushare `adj_factor` 限频 1 次/分钟 —— **akshare 是当前唯一能稳定提供全历史复权序列的源**（R1b 实证 6/6 通过） |

## 能力

- 覆盖 SH / SZ / BJ（含科创板、创业板）
- `adjust`: `''`=不复权 / `'qfq'`=前复权 / `'hfq'`=后复权
- **默认 qfq**：R1a 已实证漏传 adjust 会写入不复权价，产生除权假跳空
- 成交量单位本就是「手」，与 raw_bars 既有约定一致，无需折算

## 注意

- 东财源在中国大陆境外/代理环境下易 ProxyError，本适配器走**新浪源**
  （`stock_zh_a_daily`），BJ 前缀为 `bj`
- 单只约 1.2s，全市场 5555 只串行约 111 分钟；批量补数请用 `backfill_hfq.py`（并发）
- 高频调用会被限流，批量场景请自行节流（回补脚本用 0.35s）

代码格式：600519.SH / 000001.SZ / 920808.BJ
"""
from datetime import datetime

import pandas as pd

from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource
from app.ingestion.schemas import RawBar

logger = get_logger(__name__)

# 交易所后缀 -> akshare 前缀
_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


class AkshareSource(BaseSource):
    name = "akshare"

    _SUPPORTED_FREQ = {"1d"}

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if freq not in self._SUPPORTED_FREQ:
            raise IngestionError(f"Akshare 行情源暂仅支持日线(1d)，收到: {freq}")

        try:
            import akshare as ak
        except ImportError as e:
            raise IngestionError("未安装 akshare，请执行 pip install akshare") from e

        suffix = symbol.rsplit(".", 1)[-1].upper() if "." in symbol else ""
        prefix = _PREFIX.get(suffix)
        if prefix is None:
            raise IngestionError(
                f"Akshare 无法识别标的代码后缀: {symbol}（期望 .SH/.SZ/.BJ）"
            )
        code = symbol.rsplit(".", 1)[0]

        try:
            df = ak.stock_zh_a_daily(symbol=prefix + code, adjust=adjust)
        except Exception as e:  # 网络 / 限流 / 退市
            raise IngestionError(
                f"Akshare stock_zh_a_daily({symbol}, adjust={adjust!r}) 失败: {e}"
            ) from e

        if df is None or df.empty:
            # 停牌 / 退市 / 未上市属正常，返回空而非抛错，避免淹没真实网络失败
            logger.info(
                "Akshare 无数据",
                extra={"task": "ingest", "symbol": symbol},
            )
            return self._to_dataframe([])

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        # 区间裁剪（akshare 返回全历史，增量场景只需窗口内）
        s = pd.Timestamp(start)
        e = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        df = df[(df["date"] >= s) & (df["date"] <= e)]
        if df.empty:
            return self._to_dataframe([])

        df = df.sort_values("date")
        rows: list[RawBar] = []
        for _, r in df.iterrows():
            o, h, l, c = (
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
            )
            if h < l or c <= 0 or o <= 0:
                continue  # 剔除基础异常行
            rows.append(
                RawBar(
                    symbol=symbol,
                    timestamp=datetime(r["date"].year, r["date"].month,
                                       r["date"].day),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=float(r.get("volume") or 0),
                    source=self.name,
                    freq=freq,
                )
            )

        if not rows:
            return self._to_dataframe([])

        logger.info(
            "Akshare 拉取完成",
            extra={"task": "ingest", "symbol": symbol, "rows": len(rows)},
        )
        return self._to_dataframe(rows)
