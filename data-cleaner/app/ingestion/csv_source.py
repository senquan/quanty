"""CSV 本地上传数据源适配器

支持用户导入自定义行情文件，列名需包含:
symbol, timestamp, open, high, low, close, volume
"""
import pandas as pd

from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource

logger = get_logger(__name__)

_REQUIRED = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]


class CsvSource(BaseSource):
    name = "csv"

    def fetch(
        self,
        symbol: str = "",
        start: str = "",
        end: str = "",
        freq: str = "1d",
        *,  # 以下为 CSV 专用参数
        path: str | None = None,
        dataframe: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """从文件路径或已加载的 DataFrame 读取行情

        CSV 模式忽略 symbol/start/end，统一使用文件内 `symbol` 列。
        """
        if dataframe is not None:
            df = dataframe.copy()
        elif path:
            try:
                df = pd.read_csv(path)
            except Exception as e:
                raise IngestionError(f"读取 CSV 失败: {e}") from e
        else:
            raise IngestionError("CsvSource 需要 path 或 dataframe 参数")

        missing = [c for c in _REQUIRED if c not in df.columns]
        if missing:
            raise IngestionError(f"CSV 缺少必要列: {missing}")

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["source"] = self.name
        df["freq"] = freq
        # 仅保留 RawBar 字段，避免多余列进入流水线
        out = df[_REQUIRED + ["source", "freq"]].rename(columns={"symbol": "symbol"})
        logger.info(
            "CSV 加载完成",
            extra={"task": "ingest", "symbol_count": int(out["symbol"].nunique())},
        )
        return out
