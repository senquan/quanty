"""波动率类因子 VOL_"""
import numpy as np
import pandas as pd

from app.factors.base import Factor, group_apply
from app.factors.registry import register


@register
class ReturnStd20(Factor):
    code = "VOL_STD_20"
    name = "20日收益率标准差"
    category = "volatility"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _std(g):
            ret = g["adj_close"].pct_change()
            return ret.rolling(20, min_periods=5).std()

        return group_apply(df, "symbol", _std)


@register
class Atr14(Factor):
    code = "VOL_ATR_14"
    name = "14日ATR"
    category = "volatility"
    frequency = "Daily"
    data_sources = ["adj_high", "adj_low", "adj_close"]

    def compute(self, df):
        def _atr(g):
            high, low, close = g["adj_high"], g["adj_low"], g["adj_close"]
            prev_close = close.shift(1)
            tr = pd.concat([
                (high - low),
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ], axis=1).max(axis=1)
            return tr.rolling(14, min_periods=5).mean()

        return group_apply(df, "symbol", _atr)


@register
class Parkinson20(Factor):
    code = "VOL_PARKINSON_20"
    name = "20日Parkinson波动率"
    category = "volatility"
    frequency = "Daily"
    data_sources = ["adj_high", "adj_low"]

    def compute(self, df):
        def _park(g):
            hl = np.log(g["adj_high"] / g["adj_low"]) ** 2
            var = hl.rolling(20, min_periods=5).mean() / (4 * np.log(2))
            return var ** 0.5

        return group_apply(df, "symbol", _park)


@register
class ReturnSkew60(Factor):
    code = "VOL_SKEW_60"
    name = "60日收益偏度"
    category = "volatility"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _skew(g):
            ret = g["adj_close"].pct_change()
            return ret.rolling(60, min_periods=20).skew()

        return group_apply(df, "symbol", _skew)
