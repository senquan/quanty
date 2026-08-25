"""清洗输出 DataFrame 的 pandera 结构校验（§10 工程规范）

清洗完成后对输出做 schema 校验，确保列类型/范围符合预期；失败抛 PipelineValidationError。
为兼容无 pandera 的安装（requirements 已声明），缺失时降级为轻量手工校验。
"""
import pandas as pd

from app.core.exceptions import PipelineValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "adj_open", "adj_high", "adj_low", "adj_close",
]


def validate_cleaned(df: pd.DataFrame) -> pd.DataFrame:
    """校验清洗后的 DataFrame；返回原样（校验通过）或抛异常"""
    if df is None or df.empty:
        raise PipelineValidationError("清洗结果为空")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise PipelineValidationError(f"清洗结果缺少必要列: {missing}")

    try:
        from pandera import Check, Column, DataFrameSchema

        schema = DataFrameSchema(
            {
                "symbol": Column(str),
                "timestamp": Column(pd.Timestamp),
                "open": Column(float, Check.ge(0)),
                "high": Column(float, Check.ge(0)),
                "low": Column(float, Check.ge(0)),
                "close": Column(float, Check.ge(0)),
                "volume": Column(float, Check.ge(0)),
                "adj_open": Column(float, Check.ge(0)),
                "adj_high": Column(float, Check.ge(0)),
                "adj_low": Column(float, Check.ge(0)),
                "adj_close": Column(float, Check.ge(0)),
            },
            checks=[
                Check(lambda d: (d["high"] >= d["low"]).all(), name="high>=low"),
                Check(lambda d: (d["adj_high"] >= d["adj_low"]).all(), name="adj_high>=adj_low"),
            ],
        )
        schema.validate(df, lazy=True)
    except ImportError:
        _fallback_validate(df)
    except Exception as e:
        raise PipelineValidationError(f"pandera 校验失败: {e}") from e
    return df


def _fallback_validate(df: pd.DataFrame) -> None:
    """pandera 不可用时的降级手工校验"""
    if not (df["high"] >= df["low"]).all():
        raise PipelineValidationError("存在 high < low 的脏数据")
    if not (df["adj_high"] >= df["adj_low"]).all():
        raise PipelineValidationError("存在 adj_high < adj_low 的脏数据")
    if (df[["open", "high", "low", "close"]] < 0).any().any():
        raise PipelineValidationError("存在负价格")
