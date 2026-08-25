"""清洗 Transformers 基类

每个清洗步骤实现为独立、可插拔、可单测的 Transformer，
输入/输出统一为 RawBar DataFrame（含列:
symbol, timestamp, open, high, low, close, volume, source, freq）。
"""
from abc import ABC, abstractmethod

import pandas as pd


def group_apply(df: pd.DataFrame, by: str, func) -> pd.DataFrame:
    """兼容各 pandas 版本的按组分批处理

    避免 groupby().apply() 在不同版本下分组列行为不一致的问题：
    显式循环各分组、处理、再拼接。
    """
    parts = []
    for key, g in df.groupby(by, sort=False):
        g = g.copy()
        g[by] = key
        parts.append(func(g))
    if not parts:
        return df.iloc[0:0]
    return pd.concat(parts, ignore_index=True)


class Transformer(ABC):
    name: str  # 步骤名称，用于运行时报告

    @abstractmethod
    def transform(self, df: pd.DataFrame) -> pd.DataFrame: ...

    def _report(self, df_in: pd.DataFrame, df_out: pd.DataFrame) -> dict:
        """生成单步统计（行数变化），供编排器汇总到 pipeline_runs.report"""
        return {
            "step": self.name,
            "rows_in": int(len(df_in)),
            "rows_out": int(len(df_out)),
        }
