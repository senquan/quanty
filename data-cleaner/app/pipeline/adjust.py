"""步骤4：复权处理（透传）

前复权价（qfq）已在接入层按「全历史 adj_factor」归一化后写入 raw_bars.close，
因此本步骤直接 `adj_* = *`（透传），并保留 adj_factor / hfq_close 列透传给下游。

- 价格类因子（动量/波动/技术/情绪额）均读 `adj_close`，即全局一致 qfq，
  跨越分红/送转日的收益、动量误差已在接入层修正。
- `hfq_close`（后复权）与 `adj_factor` 由 raw_bars 透传，下游可按需用
  `hfq = close * f_latest / f_first` 反推，或直接使用落库的 hfq_close。
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
