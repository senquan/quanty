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
    data_sources = ["adj_close", "eps_ttm"]

    def compute(self, df):
        # 优先用 价 / TTM每股收益 推导：全历史可用（daily_basic.pe_ttm 仅约 3 日历史）。
        # 价用前复权 adj_close，得到横截面可比的「调整 PE」；亏损股(eps_ttm<=0) PE 无意义→NaN。
        if ("eps_ttm" in df.columns and df["eps_ttm"].notna().any()
                and "adj_close" in df.columns):
            base = df["eps_ttm"].replace(0, pd.NA)
            pe = df["adj_close"] / base
            pe = pe.where(base > 0, other=pd.NA)
            pe = pe.clip(lower=0, upper=1000)
            if pe.notna().any():
                return pe
        if "pe_ttm" in df.columns:
            return df["pe_ttm"]
        return pd.Series(float("nan"), index=df.index)


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
    data_sources = ["dividend_ttm", "adj_close", "div_yield"]

    def compute(self, df):
        # 优先用 近12月每股分红 / 价 推导（全历史；daily_basic.dv_ttm 仅约 3 日历史）。
        if ("dividend_ttm" in df.columns and df["dividend_ttm"].notna().any()
                and "adj_close" in df.columns):
            dy = df["dividend_ttm"] / df["adj_close"]
            dy = dy.clip(lower=0, upper=0.2)  # 股息率 >20% 视为异常
            if dy.notna().any():
                return dy
        if "div_yield" in df.columns:
            return df["div_yield"]
        return pd.Series(float("nan"), index=df.index)


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
        # 同比可能为极端值（低基数/一次性损益），clip 到 [-100%, +500%]
        # 避免单只标的在截面 z-score 中 dominating；不改变排序方向。
        return df["rev_growth_yoy"].clip(-1.0, 5.0)


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
        # 同 GRO_REV_GROWTH_YOY：clip 到 [-100%, +500%] 稳健化（真正生效于截面）。
        return df["eps_growth_yoy"].clip(-1.0, 5.0)


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


# ---------- 基本面质量 FND_ ----------

@register
class FundamentalRoe(Factor):
    code = "FND_ROE"
    name = "净资产收益率(%)"
    category = "fundamental"
    frequency = "Daily"
    data_sources = ["roe"]

    def compute(self, df):
        if "roe" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        # 单位：百分点（akshare 直供，如茅台 17.72 表示 17.72%）。
        # 困境股净资产近零/为负会算出极端值（实测出现过 -80000%），
        # 在截面 z-score 中会 dominate，clip 到 [-100, 100] 稳健化（不改排序方向）。
        return df["roe"].clip(-100.0, 100.0)


@register
class FundamentalDebtRatio(Factor):
    code = "FND_DEBT_RATIO"
    name = "资产负债率(%)"
    category = "fundamental"
    frequency = "Daily"
    data_sources = ["debt_ratio"]

    def compute(self, df):
        if "debt_ratio" not in df.columns:
            return pd.Series(float("nan"), index=df.index)
        # 单位：百分点（如茅台 15.19 表示 15.19%）。资不抵债（负资产）情形
        # clip 到 100 仍标记为高杠杆，便于「低负债」硬过滤。
        return df["debt_ratio"].clip(0.0, 100.0)
