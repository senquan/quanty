"""技术类因子 TECH_（复用 backend 的 TA-Lib 指标思路）"""

from app.factors.base import Factor, group_apply
from app.factors.registry import register


@register
class Rsi14(Factor):
    code = "TECH_RSI_14"
    name = "14日RSI"
    category = "technical"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _rsi(g):
            delta = g["adj_close"].diff()
            gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
            loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
            rs = gain / (loss + 1e-9)
            return 100 - 100 / (1 + rs)

        return group_apply(df, "symbol", _rsi)


@register
class MacdDif(Factor):
    code = "TECH_MACD_DIF"
    name = "MACD DIF"
    category = "technical"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _macd(g):
            ema12 = g["adj_close"].ewm(span=12, adjust=False).mean()
            ema26 = g["adj_close"].ewm(span=26, adjust=False).mean()
            return ema12 - ema26

        return group_apply(df, "symbol", _macd)


@register
class MacdDea(Factor):
    code = "TECH_MACD_DEA"
    name = "MACD DEA"
    category = "technical"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _dea(g):
            ema12 = g["adj_close"].ewm(span=12, adjust=False).mean()
            ema26 = g["adj_close"].ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            return dif.ewm(span=9, adjust=False).mean()

        return group_apply(df, "symbol", _dea)


@register
class MacdHist(Factor):
    code = "TECH_MACD_HIST"
    name = "MACD 柱"
    category = "technical"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _hist(g):
            ema12 = g["adj_close"].ewm(span=12, adjust=False).mean()
            ema26 = g["adj_close"].ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            return (dif - dea) * 2

        return group_apply(df, "symbol", _hist)


@register
class BollingerPosition(Factor):
    code = "TECH_BB_POS"
    name = "布林带位置"
    category = "technical"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _bb(g):
            mid = g["adj_close"].rolling(20, min_periods=5).mean()
            std = g["adj_close"].rolling(20, min_periods=5).std()
            upper = mid + 2 * std
            lower = mid - 2 * std
            return (g["adj_close"] - lower) / (upper - lower + 1e-9)

        return group_apply(df, "symbol", _bb)


@register
class MaBias20(Factor):
    code = "TECH_MA_BIAS_20"
    name = "20日均线乖离率"
    category = "technical"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        def _bias(g):
            ma20 = g["adj_close"].rolling(20, min_periods=5).mean()
            return (g["adj_close"] - ma20) / (ma20 + 1e-9)

        return group_apply(df, "symbol", _bias)
