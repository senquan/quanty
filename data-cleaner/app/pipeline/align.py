"""步骤5：时间对齐

按交易日历补齐缺失交易时段为 NaN 行（供下游识别），
目前简化为按每个 symbol 的 min/max 时间戳生成规则频率日期索引。
"""
import pandas as pd

from app.pipeline.base import Transformer, group_apply


class TimeAlignTransformer(Transformer):
    name = "time_align"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

        def _align(g: pd.DataFrame) -> pd.DataFrame:
            g = g.copy()
            freq = g["freq"].iloc[0]
            idx = pd.date_range(g["timestamp"].min(), g["timestamp"].max(), freq=freq)
            out = g.set_index("timestamp").reindex(idx)
            out["symbol"] = g["symbol"].iloc[0]
            out["freq"] = freq
            out["source"] = g["source"].iloc[0]
            out["is_aligned_fill"] = out["close"].isna().astype(int)
            return out.reset_index().rename(columns={"index": "timestamp"})

        df = group_apply(df, "symbol", _align)
        return df.reset_index(drop=True)
