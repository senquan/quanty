"""因子基类

所有因子实现统一接口并自动注册（通过 @register 装饰器），
新增因子只需新增一个继承 Factor 的文件。
"""
from abc import ABC, abstractmethod

import pandas as pd


def group_apply(df: pd.DataFrame, by: str, func) -> pd.Series:
    """兼容各 pandas 版本的按组计算，返回与 df 同索引的 Series

    避免 groupby().apply() 分组列行为差异。函数需返回与输入分组
    等长的 Series（按原 index）。
    """
    out = []
    for key, g in df.groupby(by, sort=False):
        g = g.copy()
        g[by] = key
        res = func(g)
        res = pd.Series(res.values, index=g.index)
        out.append(res)
    if not out:
        return pd.Series(dtype=float, index=df.index)
    return pd.concat(out).reindex(df.index)


class Factor(ABC):
    code: str  # 因子代码，如 MOM_RET_20
    name: str  # 中文名
    category: str  # momentum / volatility / technical / sentiment
    frequency: str  # Daily / Weekly / Monthly
    data_sources: list[str]  # 依赖列，如 ["adj_close"]
    # 已知不可得因子：文档确认无历史数据源（如 PB/PS/股息率）。
    # 其覆盖率不应计入"可得因子覆盖率分母"，避免指标被永久拉低（docs/memo/2026-09-02 §2.4）。
    unavailable: bool = False
    unavailable_reason: str = ""

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.Series:
        """基于清洗后 DataFrame（含 adj_close 等列）计算该因子，返回 Series"""
        ...

    def get_metadata(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "frequency": self.frequency,
            "data_sources": self.data_sources,
            "unavailable": self.unavailable,
            "unavailable_reason": self.unavailable_reason,
        }
