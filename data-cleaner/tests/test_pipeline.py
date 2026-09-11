"""清洗流水线 + 因子计算集成测试（使用本地 fixture 数据，无需联网）"""
import numpy as np
import pandas as pd

from app.factors.registry import compute_factor, list_factors
from app.pipeline.runner import CleaningPipeline


def _make_dirty_df(n=120, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="1d")
    # 基础价格随机游走
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame(
        {
            "symbol": "TEST",
            "timestamp": dates,
            "open": close + rng.normal(0, 0.5, n),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": rng.integers(1000, 5000, n).astype(float),
            "source": "csv",
            "freq": "1d",
        }
    )
    # 人为制造脏数据：重复行、缺失、异常跳变
    df = pd.concat([df, df.iloc[[10, 20]]], ignore_index=True)  # 重复
    df.loc[30, "close"] = np.nan  # 缺失
    df.loc[60, "close"] = close[60] * 5  # 异常跳变
    return df


def test_cleaning_pipeline_runs():
    df = _make_dirty_df()
    cleaned, report = CleaningPipeline().run(df)
    assert report["rows_out"] > 0
    assert report["rows_in"] == len(df)
    # 校验列存在
    for col in ["adj_close", "price_outlier_fixed"]:
        assert col in cleaned.columns


def test_factor_computation_on_cleaned():
    df = _make_dirty_df()
    cleaned, _ = CleaningPipeline().run(df)
    for meta in list_factors():
        series = compute_factor(meta["code"], cleaned)
        assert len(series) == len(cleaned)
        # 不应存在非有限值（inf）
        assert np.isfinite(series.dropna()).all()
