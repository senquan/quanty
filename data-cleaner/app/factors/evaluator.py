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

        factor_values / forward_returns 为同长度序列（按时间对齐）。
        接口接收扁平列表，无截面日期维度，因此：
          - IC 使用 factor 与 forward_return 的时序 Spearman 秩相关
          - Sharpe / 回撤 / 胜率基于 forward_return 序列本身（因子预测收益）
        """
        df = pd.concat([factor_values, forward_returns], axis=1).dropna()
        df.columns = ["factor", "fwd"]
        if len(df) < 30:
            return self._empty()

        fwd = df["fwd"].reset_index(drop=True)
        factor = df["factor"].reset_index(drop=True)

        # IC: factor 与未来收益的时序 Spearman 相关
        ic = factor.corr(fwd, method="spearman")
        ic_mean = float(ic) if pd.notna(ic) else 0.0
        ic_std = 0.0  # 单序列无截面，IC 标准差置 0
        ir = 0.0 if ic_std == 0 else ic_mean / ic_std

        # 分层多空：十分位最高组 - 最低组的平均未来收益（单点估计）
        try:
            groups = pd.qcut(factor.rank(method="first"), 10, labels=False)
            long_ret = fwd[groups == 9].mean()
            short_ret = fwd[groups == 0].mean()
            strat_mean = float(long_ret - short_ret)
        except Exception:
            strat_mean = 0.0

        # Sharpe / 回撤 / 胜率基于 forward_return 序列（真实收益路径）
        sharpe = self._sharpe(fwd)
        mdd = self._max_drawdown(fwd)
        win = float((fwd > 0).mean())

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
        std = returns.std()
        if std is None or std < 1e-12:
            return 0.0
        return float(returns.mean() / std * (252 ** 0.5))

    @staticmethod
    def _max_drawdown(returns: pd.Series) -> float:
        if returns.empty:
            return 0.0
        cum = (1 + returns).cumprod()
        peak = cum.cummax()
        # 保护：净值趋近 0 时（极端负收益）回撤无意义，钳制为 -1.0
        if peak.iloc[-1] < 1e-9:
            return -1.0
        dd = ((cum - peak) / peak).min()
        return float(dd)

    @staticmethod
    def _empty() -> dict:
        return {
            "icMean": 0.0, "icStd": 0.0, "ir": 0.0,
            "sharpeRatio": 0.0, "maxDrawdown": 0.0, "winRate": 0.0,
        }
