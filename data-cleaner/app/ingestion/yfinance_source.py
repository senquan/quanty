"""Yahoo Finance 数据源适配器（股票 / ETF / 指数）"""

import pandas as pd

from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource
from app.ingestion.schemas import RawBar

logger = get_logger(__name__)


class YFinanceSource(BaseSource):
    name = "yfinance"

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
    ) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as e:
            raise IngestionError("未安装 yfinance，请执行 pip install yfinance") from e

        try:
            raw = yf.download(
                symbol,
                start=start,
                end=end,
                interval="1d" if freq == "1d" else freq,
                auto_adjust=False,
                progress=False,
                group_by="column",
            )
        except Exception as e:  # 网络/限流/无效代码
            raise IngestionError(f"yfinance 拉取 {symbol} 失败: {e}") from e

        if raw is None or raw.empty:
            raise IngestionError(f"yfinance 返回空数据: {symbol}")

        # yfinance 多列层级索引，统一降维
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.droplevel(1, axis=1)

        rows: list[RawBar] = []
        for ts, row in raw.iterrows():
            rows.append(
                RawBar(
                    symbol=symbol,
                    timestamp=pd.Timestamp(ts).to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                    source=self.name,
                    freq=freq,
                )
            )
        logger.info(
            "yfinance 拉取完成",
            extra={"task": "ingest", "symbol_count": len(rows)},
        )
        return self._to_dataframe(rows)
