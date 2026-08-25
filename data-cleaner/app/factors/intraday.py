"""分钟级 / 高频因子（Phase 3 步骤11，实时化准备）

这些因子在 freq=1m / 1h 数据上更有意义，但统一接口下对日线也能计算
（窗口按 bar 计）。用于后续流式/实时计算的基础。
"""

from app.factors.base import Factor, group_apply
from app.factors.registry import register


@register
class IntradayVolatility(Factor):
    code = "INTRADAY_VOL_20"
    name = "近20根K线收益率波动率"
    category = "volatility"
    frequency = "Intraday"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _vol(g):
            ret = g["adj_close"].pct_change()
            return ret.rolling(20, min_periods=5).std()

        return group_apply(df, "symbol", _vol)


@register
class IntradayRange(Factor):
    code = "INTRADAY_RANGE_20"
    name = "近20根K线振幅均值"
    category = "volatility"
    frequency = "Intraday"
    data_sources = ["adj_high", "adj_low", "adj_close"]

    def compute(self, df):
        def _rng(g):
            amp = (g["adj_high"] - g["adj_low"]) / (g["adj_close"] + 1e-9)
            return amp.rolling(20, min_periods=5).mean()

        return group_apply(df, "symbol", _rng)


@register
class IntradayMomentum(Factor):
    code = "INTRADAY_MOM_10"
    name = "近10根K线动量"
    category = "momentum"
    frequency = "Intraday"
    data_sources = ["adj_close"]

    def compute(self, df):
        return df.groupby("symbol", group_keys=False)["adj_close"].pct_change(10)
