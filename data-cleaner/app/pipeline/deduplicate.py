"""步骤1：去重"""
import pandas as pd

from app.pipeline.base import Transformer


class DeduplicateTransformer(Transformer):
    name = "deduplicate"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        # 按标的+时间+频率去重，保留最后出现的记录（覆盖策略）
        return df.drop_duplicates(
            subset=["symbol", "timestamp", "freq"], keep="last"
        ).reset_index(drop=True)
