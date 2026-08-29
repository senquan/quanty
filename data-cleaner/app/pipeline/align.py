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
            # 归一化时间戳到自然日，避免 reindex 因精度/时区不匹配而全 NaN
            ts = pd.to_datetime(g["timestamp"]).dt.normalize()
            # A 股等使用工作日频率（B）补齐缺失交易日，而非含周末的日频
            cal_freq = "B" if freq == "1d" else freq
            idx = pd.date_range(ts.min(), ts.max(), freq=cal_freq)
            # 以归一化时间戳为索引重对齐；丢弃原 timestamp 列避免残 NaN
            g = g.drop(columns=["timestamp"]).assign(_ts=ts).set_index("_ts").reindex(idx)
            out = g
            out["symbol"] = g["symbol"].iloc[0] if not g.empty else None
            out["freq"] = freq
            out["source"] = g["source"].iloc[0] if not g.empty else None
            # 对齐补齐的缺失时段（停牌/非交易日）沿用最近交易日价格，避免产生 NaN 行
            price_cols = ["open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close"]
            out[price_cols] = out[price_cols].ffill()
            out["volume"] = out["volume"].fillna(0.0)
            out["is_aligned_fill"] = out["close"].isna().astype(int)
            out = out.dropna(subset=price_cols)
            # reindex 后索引名被置空，reset_index 产生列名 'index'，重命名为 timestamp
            return out.reset_index().rename(columns={"index": "timestamp"})

        df = group_apply(df, "symbol", _align)
        return df.reset_index(drop=True)
