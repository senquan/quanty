"""情绪类因子 SENT_"""

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
class Turnover20(Factor):
    code = "SENT_TURNOVER_20"
    name = "20日换手率"
    category = "sentiment"
    frequency = "Daily"
    data_sources = ["volume"]

    def compute(self, df):
        # 简化：以成交量相对20日均量的偏移作为换手代理指标
        def _to(g):
            v20 = g["volume"].rolling(20, min_periods=5).mean()
            return g["volume"] / (v20 + 1e-9)

        return group_apply(df, "symbol", _to)


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
