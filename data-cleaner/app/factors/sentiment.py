"""情绪类因子 SENT_"""
import pandas as pd

from app.factors.base import Factor, group_apply
from app.factors.registry import register


@register
class VolumeRatio5(Factor):
    code = "SENT_VOL_RATIO_5"
    name = "5日量比"
    category = "sentiment"
    frequency = "Daily"
    data_sources = ["volume"]

    def compute(self, df):
        def _ratio(g):
            v5 = g["volume"].rolling(5, min_periods=3).mean()
            v20 = g["volume"].rolling(20, min_periods=5).mean()
            return v5 / (v20 + 1e-9)

        return group_apply(df, "symbol", _ratio)


@register
class TurnoverRate(Factor):
    code = "TURNOVER_RATE"
    name = "换手率(真实)"
    category = "sentiment"
    frequency = "Daily"
    data_sources = ["turnover_rate"]

    def compute(self, df):
        # 真实换手率 = 成交量/流通股本(%)，来自迁移 006 daily_basic。
        if "turnover_rate" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["turnover_rate"]


@register
class TurnoverRateFree(Factor):
    code = "TURNOVER_RATE_F"
    name = "自由流通换手率"
    category = "sentiment"
    frequency = "Daily"
    data_sources = ["turnover_rate_f"]

    def compute(self, df):
        if "turnover_rate_f" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["turnover_rate_f"]


@register
class AmountRank(Factor):
    code = "SENT_AMOUNT_RANK"
    name = "成交额市场分位"
    category = "sentiment"
    frequency = "Daily"
    data_sources = ["volume", "adj_close"]

    def compute(self, df):
        df = df.copy()
        df["_amount"] = df["volume"] * df["adj_close"]
        return df.groupby("timestamp", group_keys=False)["_amount"].rank(pct=True)


# 因子效能评估器（对接前端 Factor 类型需要的 icMean/ir/sharpeRatio 等）
from app.factors.evaluator import FactorEvaluator  # noqa: E402,F401
