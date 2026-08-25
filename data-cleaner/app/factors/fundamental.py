"""价值类因子 VAL_ 与成长类因子 GRO_

价值/成长因子依赖基本面字段（PE/PB/PS/股息率/营收同比/净利润同比）。
这些字段由 FundamentalSource 提供；无凭证/无网络时返回 NaN，流水线仍可用。
其中若干"代理因子"仅依赖行情即可计算（估值历史分位、相对估值乖离），无需外部财务。
"""
import pandas as pd

from app.factors.base import Factor, group_apply
from app.factors.registry import register

# ---------- 价值类 VAL_ ----------

@register
class ValuePE(Factor):
    code = "VAL_PE_TTM"
    name = "市盈率(TTM)"
    category = "value"
    frequency = "Daily"
    data_sources = ["pe_ttm"]

    def compute(self, df):
        if "pe_ttm" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["pe_ttm"]


@register
class ValuePB(Factor):
    code = "VAL_PB"
    name = "市净率"
    category = "value"
    frequency = "Daily"
    data_sources = ["pb"]

    def compute(self, df):
        if "pb" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["pb"]


@register
class ValuePS(Factor):
    code = "VAL_PS_TTM"
    name = "市销率(TTM)"
    category = "value"
    frequency = "Daily"
    data_sources = ["ps_ttm"]

    def compute(self, df):
        if "ps_ttm" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["ps_ttm"]


@register
class ValueDivYield(Factor):
    code = "VAL_DIV_YIELD"
    name = "股息率"
    category = "value"
    frequency = "Daily"
    data_sources = ["div_yield"]

    def compute(self, df):
        if "div_yield" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["div_yield"]


@register
class ValuePEPercentile(Factor):
    code = "VAL_PE_PERCENTILE"
    name = "PE历史分位(3年)"
    category = "value"
    frequency = "Daily"
    data_sources = ["pe_ttm"]

    def compute(self, df):
        """PE 在过去 3 年（~756 交易日）内的分位；无 PE 时回退到价格分位代理"""
        if "pe_ttm" in df.columns and df["pe_ttm"].notna().any():
            base = df["pe_ttm"]
        else:
            base = df["adj_close"]
        return group_apply(
            df.assign(_base=base), "symbol",
            lambda g: g["_base"].rolling(756, min_periods=20).apply(
                lambda x: (x[-1] >= x).mean(), raw=True
            ),
        )


# ---------- 成长类 GRO_ ----------

@register
class GrowthRevenueYoy(Factor):
    code = "GRO_REV_GROWTH_YOY"
    name = "营收同比增长率"
    category = "growth"
    frequency = "Daily"
    data_sources = ["rev_growth_yoy"]

    def compute(self, df):
        if "rev_growth_yoy" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["rev_growth_yoy"]


@register
class GrowthEpsYoy(Factor):
    code = "GRO_EPS_GROWTH_YOY"
    name = "净利润同比增长率"
    category = "growth"
    frequency = "Daily"
    data_sources = ["eps_growth_yoy"]

    def compute(self, df):
        if "eps_growth_yoy" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        return df["eps_growth_yoy"]


@register
class GrowthPriceMomentum(Factor):
    code = "GRO_PRICE_MOMENTUM"
    name = "价格成长动量(60日/250日)"
    category = "growth"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        """用价格动量近似成长势能：60日收益相对250日收益的强度"""
        def _g(g):
            r60 = g["adj_close"].pct_change(60)
            r250 = g["adj_close"].pct_change(250)
            return r60 / (r250.abs() + 1e-9)

        return group_apply(df, "symbol", _g)
