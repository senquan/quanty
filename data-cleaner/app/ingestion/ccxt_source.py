"""CCXT 加密货币数据源适配器"""
from datetime import UTC, datetime

import pandas as pd

from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource
from app.ingestion.schemas import RawBar

logger = get_logger(__name__)

_FREQ_MS = {"1m": 60_000, "1h": 3_600_000, "1d": 86_400_000}


class CcxtSource(BaseSource):
    name = "ccxt"

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
    ) -> pd.DataFrame:
        try:
            import ccxt
        except ImportError as e:
            raise IngestionError("未安装 ccxt，请执行 pip install ccxt") from e

        try:
            exchange = ccxt.binance()
            since = int(pd.Timestamp(start).timestamp() * 1000)
            until = int(pd.Timestamp(end).timestamp() * 1000)
            tf_ms = _FREQ_MS.get(freq, _FREQ_MS["1d"])

            ohlcv: list[list] = []
            cursor = since
            while cursor < until:
                batch = exchange.fetch_ohlcv(symbol, freq, cursor, limit=1000)
                if not batch:
                    break
                ohlcv.extend(batch)
                cursor = batch[-1][0] + tf_ms
        except Exception as e:  # 限流/网络/代码无效
            raise IngestionError(f"ccxt 拉取 {symbol} 失败: {e}") from e

        rows: list[RawBar] = []
        for ts_ms, o, h, low, c, v in ohlcv:
            rows.append(
                RawBar(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=float(v),
                    source=self.name,
                    freq=freq,
                )
            )
        logger.info(
            "ccxt 拉取完成",
            extra={"task": "ingest", "symbol_count": len(rows)},
        )
        return self._to_dataframe(rows)
