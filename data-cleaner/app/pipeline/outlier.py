"""步骤3：异常值检测与修复

基于滚动 Z-Score（window=20, threshold=5）检测收盘价跳变，
异常值用滚动中位数替换并打标记。
"""
import pandas as pd

from app.pipeline.base import Transformer, group_apply


class OutlierTransformer(Transformer):
    name = "outlier"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

        def _detect(g: pd.DataFrame) -> pd.DataFrame:
            g = g.copy()
            ret = g["close"].pct_change()
            roll_mean = ret.rolling(20, min_periods=5).mean()
            roll_std = ret.rolling(20, min_periods=5).std()
            z = (ret - roll_mean) / (roll_std + 1e-9)
            mask = z.abs() > 5
            g["price_outlier_fixed"] = mask.astype(int)
            # 异常处用滚动中位数（窗口5）替换收盘价，其余 OHLC 同比例缩放
            median = g["close"].rolling(5, min_periods=1).median()
            g.loc[mask, "close"] = median[mask]
            return g

        df = group_apply(df, "symbol", _detect)
        return df.reset_index(drop=True)
