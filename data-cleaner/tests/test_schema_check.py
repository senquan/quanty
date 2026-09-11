"""pandera 清洗输出校验测试（§10 工程规范）"""
import pandas as pd
import pytest

from app.core.exceptions import PipelineValidationError
from app.pipeline.schema_check import validate_cleaned


def _good_df():
    return pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "timestamp": pd.to_datetime(["2023-01-01", "2023-01-02"]),
            "open": [10.0, 11.0],
            "high": [12.0, 12.5],
            "low": [9.0, 10.0],
            "close": [11.0, 11.5],
            "volume": [100.0, 120.0],
            "adj_open": [10.0, 11.0],
            "adj_high": [12.0, 12.5],
            "adj_low": [9.0, 10.0],
            "adj_close": [11.0, 11.5],
        }
    )


def test_validate_good_passes():
    df = validate_cleaned(_good_df())
    assert len(df) == 2


def test_validate_rejects_high_lt_low():
    df = _good_df()
    df.loc[0, "high"] = 5.0  # 制造 high < low
    with pytest.raises(PipelineValidationError):
        validate_cleaned(df)


def test_validate_rejects_missing_column():
    df = _good_df().drop(columns=["volume"])
    with pytest.raises(PipelineValidationError):
        validate_cleaned(df)


def test_validate_rejects_negative_price():
    df = _good_df()
    df.loc[0, "close"] = -1.0
    with pytest.raises(PipelineValidationError):
        validate_cleaned(df)
