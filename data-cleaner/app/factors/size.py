"""规模类因子 SIZE_（市值）

依赖迁移 006 的 factor.daily_basic 提供的 total_mv / circ_mv（万元）。
市值天然右偏，统一取对数（ln）后再做截面 z-score，更符合规模因子定义。
数据缺失（未刷新 daily_basic）时返回 NaN，流水线不受影响。
"""
import numpy as np
import pandas as pd

from app.factors.base import Factor
from app.factors.registry import register


@register
class MarketCapTotal(Factor):
    code = "SIZE_MKT_CAP"
    name = "总市值(ln)"
    category = "size"
    frequency = "Daily"
    data_sources = ["total_mv"]

    def compute(self, df):
        if "total_mv" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        mv = df["total_mv"].where(df["total_mv"] > 0)
        return np.log(mv)


@register
class MarketCapCirc(Factor):
    code = "SIZE_MKT_CAP_CIRC"
    name = "流通市值(ln)"
    category = "size"
    frequency = "Daily"
    data_sources = ["circ_mv"]

    def compute(self, df):
        if "circ_mv" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        mv = df["circ_mv"].where(df["circ_mv"] > 0)
        return np.log(mv)
