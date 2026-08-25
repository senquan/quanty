"""数据源适配器基类"""
from abc import ABC, abstractmethod

import pandas as pd

from app.ingestion.schemas import RawBar


class BaseSource(ABC):
    """数据源适配器接口

    每个子类负责将某一来源的行情拉取为统一 RawBar DataFrame。
    """

    name: str  # 来源标识，写入 RawBar.source

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
    ) -> pd.DataFrame:
        """拉取行情，返回符合 RawBar 字段的 DataFrame"""
        ...

    def _to_dataframe(self, rows: list[RawBar]) -> pd.DataFrame:
        return pd.DataFrame([r.model_dump(mode="json") for r in rows])
