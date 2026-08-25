"""步骤6：输出校验

用声明式规则校验清洗后 DataFrame，不通过抛 PipelineValidationError。
"""
import pandas as pd

from app.core.exceptions import PipelineValidationError
from app.pipeline.base import Transformer


class ValidateTransformer(Transformer):
    name = "validate"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            raise PipelineValidationError("清洗后数据为空，请检查上游数据接入")

        required = [
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "source", "freq",
            "adj_open", "adj_high", "adj_low", "adj_close",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise PipelineValidationError(f"缺少必要列: {missing}")

        # 价格逻辑校验
        bad = (df["high"] < df["low"]) | (df["volume"] < 0)
        if bad.any():
            n = int(bad.sum())
            raise PipelineValidationError(f"存在 {n} 行高价<低价或成交量为负")

        # 时间单调递增（按 symbol）
        for sym, g in df.groupby("symbol"):
            if not g["timestamp"].is_monotonic_increasing:
                raise PipelineValidationError(f"{sym} 时间戳非单调递增")

        return df.reset_index(drop=True)
