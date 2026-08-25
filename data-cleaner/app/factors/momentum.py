"""动量类因子 MOM_"""

from app.factors.base import Factor
from app.factors.registry import register


@register
class MomentumReturn5(Factor):
    code = "MOM_RET_5"
    name = "5日动量"
    category = "momentum"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        return df.groupby("symbol", group_keys=False)["adj_close"].pct_change(5)


@register
class MomentumReturn20(Factor):
    code = "MOM_RET_20"
    name = "20日动量"
    category = "momentum"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        return df.groupby("symbol", group_keys=False)["adj_close"].pct_change(20)


@register
class MomentumReturn60(Factor):
    code = "MOM_RET_60"
    name = "60日动量"
    category = "momentum"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        return df.groupby("symbol", group_keys=False)["adj_close"].pct_change(60)


@register
class MomentumAccel(Factor):
    code = "MOM_ACCEL"
    name = "动量加速度"
    category = "momentum"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        g = df.groupby("symbol", group_keys=False)["adj_close"]
        r20 = g.pct_change(20)
        r10 = g.pct_change(10)
        return r20 - r10 * 2  # 加速度近似


@register
class RelativeStrength20(Factor):
    code = "REL_STR_20"
    name = "20日相对强度"
    category = "momentum"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        r = df.groupby("symbol", group_keys=False)["adj_close"].pct_change(20)
        # 横截面标准化（z-score）
        return r.groupby(df["timestamp"], group_keys=False).transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-9)
        )
