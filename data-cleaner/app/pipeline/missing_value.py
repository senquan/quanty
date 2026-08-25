"""步骤2：缺失值处理

- OHLC 缺失: 前值填充（最多 3 根），否则标记丢弃
- volume 缺失: 填 0 并打标记
- 连续缺失 >5 根时丢弃该 symbol 该段
"""
import pandas as pd

from app.pipeline.base import Transformer, group_apply


class MissingValueTransformer(Transformer):
    name = "missing_value"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

        def _fill_group(g: pd.DataFrame) -> pd.DataFrame:
            g = g.copy()
            price_cols = ["open", "high", "low", "close"]
            # 价格缺失：前值填充（limit=3）
            g[price_cols] = g[price_cols].ffill(limit=3)
            # volume 缺失：填 0 并标记
            g["volume_imputed"] = g["volume"].isna().astype(int)
            g["volume"] = g["volume"].fillna(0.0)
            return g

        df = group_apply(df, "symbol", _fill_group)

        # 丢弃价格仍缺失的行（连续缺失 >3 未修复的部分）
        price_cols = ["open", "high", "low", "close"]
        df = df.dropna(subset=price_cols).reset_index(drop=True)
        return df
