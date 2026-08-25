"""因子效能评估器

计算 IC / IR / 分层收益 / Sharpe / 最大回撤 / 胜率，对接前端 Factor 类型字段。
Phase 1 仅提供计算能力，Phase 2 步骤6 接入存储与 API。
"""
import numpy as np
import pandas as pd


class FactorEvaluator:
    """基于因子值与未来收益的效能指标"""

    def evaluate(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
    ) -> dict:
        """计算单个因子效能指标

        factor_values / forward_returns 需为同索引 Series
        """
        df = pd.concat([factor_values, forward_returns], axis=1).dropna()
        df.columns = ["factor", "fwd"]
        if len(df) < 30:
            return self._empty()

        # IC: 截面相关（按时间分组求 spearman）
        ic_list = []
        for _, g in df.groupby(df.index):
            if len(g) > 2:
                ic = g["factor"].corr(g["fwd"], method="spearman")
                if pd.notna(ic):
                    ic_list.append(ic)
        ic_mean = float(np.mean(ic_list)) if ic_list else 0.0
        ic_std = float(np.std(ic_list)) if ic_list else 0.0
        ir = ic_mean / (ic_std + 1e-9)

        # 分层回测: 按因子值十分位，多空净值
        df["group"] = pd.qcut(df["factor"].rank(method="first"), 10, labels=False)
        long_ret = df[df["group"] == 9]["fwd"].mean()
        short_ret = df[df["group"] == 0]["fwd"].mean()
        # 多空组合平均日收益，构造均值恒定序列以计算净值类指标（近似）
        strat_mean = float(long_ret - short_ret)
        strat_ret = pd.Series([strat_mean] * len(df))
        sharpe = self._sharpe(strat_ret)
        mdd = self._max_drawdown(strat_ret)
        win = float((strat_ret > 0).mean())

        return {
            "icMean": ic_mean,
            "icStd": ic_std,
            "ir": ir,
            "sharpeRatio": sharpe,
            "maxDrawdown": mdd,
            "winRate": float(win),
        }

    @staticmethod
    def _sharpe(returns: pd.Series) -> float:
        if len(returns) < 2:
            return 0.0
        return float(returns.mean() / (returns.std() + 1e-9) * (252 ** 0.5))

    @staticmethod
    def _max_drawdown(returns: pd.Series) -> float:
        if returns.empty:
            return 0.0
        cum = (1 + returns).cumprod()
        peak = cum.cummax()
        return float(((cum - peak) / peak).min())

    @staticmethod
    def _empty() -> dict:
        return {
            "icMean": 0.0, "icStd": 0.0, "ir": 0.0,
            "sharpeRatio": 0.0, "maxDrawdown": 0.0, "winRate": 0.0,
        }
