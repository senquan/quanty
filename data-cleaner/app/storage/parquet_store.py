"""Parquet 因子值矩阵存储

按 category/日期 分区存储因子值：
data/factors/{category}/{date}.parquet
行=symbol(+timestamp 索引)，列=各因子代码。
"""
from pathlib import Path

import pandas as pd

from app.core.config import settings


class ParquetStore:
    """因子值矩阵读写（Parquet）"""

    def _path(self, category: str, date: str) -> Path:
        p = settings.factor_data_path / category
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{date}.parquet"

    def save(self, category: str, date: str, df: pd.DataFrame) -> None:
        """保存某类别某日期因子值矩阵"""
        df.to_parquet(self._path(category, date), index=True)

    def load(self, category: str, date: str) -> pd.DataFrame | None:
        path = self._path(category, date)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    def load_latest(self, category: str) -> pd.DataFrame | None:
        """加载该类别最新可用日期的因子矩阵"""
        base = settings.factor_data_path / category
        if not base.exists():
            return None
        files = sorted(base.glob("*.parquet"), reverse=True)
        return pd.read_parquet(files[0]) if files else None


parquet_store = ParquetStore()
