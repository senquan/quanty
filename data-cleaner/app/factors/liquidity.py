"""流动性类因子 LIQ_"""
import pandas as pd

from app.factors.base import Factor, group_apply
from app.factors.registry import register


@register
class Amount20(Factor):
    code = "LIQ_AMOUNT_20"
    name = "20日均成交额(元)"
    category = "liquidity"
    frequency = "Daily"
    data_sources = ["amount"]

    def compute(self, df):
        if "amount" not in df.columns:
            return pd.Series(float("nan"), index=df.index)

        def _m(g):
            return g["amount"].rolling(20, min_periods=5).mean()

        return group_apply(df, "symbol", _m)
