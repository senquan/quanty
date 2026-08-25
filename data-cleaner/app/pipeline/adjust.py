"""步骤4：复权处理

加载分红拆股事件后生成前复权价 adj_* 列。
无事件数据时 adj_* 等于原始值（简化实现：默认前复权=close）。

真实复权表（corporate actions）在 Phase 2 财务数据源接入后补全。
"""
import pandas as pd

from app.pipeline.base import Transformer


class AdjustTransformer(Transformer):
    name = "adjust"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.copy()
        # 占位：前复权价格，后续接入事件表后改为真实复权计算
        df["adj_open"] = df["open"]
        df["adj_high"] = df["high"]
        df["adj_low"] = df["low"]
        df["adj_close"] = df["close"]
        return df
