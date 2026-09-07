"""步骤4：复权处理（透传 + hfq 派生）

前复权价（qfq）已在接入层按「全历史 adj_factor」归一化后写入 raw_bars.close，
因此本步骤对 adj_* 直接 `adj_* = *`（透传），并保留 adj_factor / hfq_close 列透传给下游。

## 两套口径并存（R1b, 2026-09-06）

| 列 | 口径 | 锚点 | 历史值 | 适用 |
|---|---|---|---|---|
| `adj_*`（= qfq） | 前复权 | **最新一日** | **每次除权都变** | 估值类（PE/PB/股息率）—— 分子分母需同为真实价口径 |
| `hfq_*` | 后复权 | **最早一日** | **永不改变** | **时序类（动量/波动/技术）—— 序列稳定可复现** |

### 为什么不能一刀切把 adj_close 换成 hfq

估值因子 `PE = adj_close / eps_ttm`：eps 是真实价口径，分子若用 hfq（被放大
f_latest/f_first 倍，茅台约 150 倍）会得出荒谬的 PE。
故 **adj_close 保持 qfq**；时序因子要后复权就显式写 `hfq_close`。

### qfq 漂移与 hfq 的价值

qfq 以最新日为锚 ⇒ 标的每除权一次，其**全部历史行**都要重算；而增量写入只写
当天、从不回头 ⇒ 历史必然腐化，只能周期性全量重拉（R1a 实测单次约 23 分钟）。
hfq 锚在最早日 ⇒ 历史值恒定 ⇒ 回测/时序因子改用 hfq 即可摆脱该包袱。

`hfq_close` 由 `backfill_hfq.py` 用 akshare 补全（alphafeed ex-factors 403 无权限、
tushare adj_factor 限频 1 次/分钟，均不适用）。缺失时本步骤降级：不产出 hfq_*。
"""
import pandas as pd

from app.pipeline.base import Transformer


class AdjustTransformer(Transformer):
    name = "adjust"

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.copy()
        # qfq：前复权，全局一致（最新日为锚）
        df["adj_open"] = df["open"]
        df["adj_high"] = df["high"]
        df["adj_low"] = df["low"]
        df["adj_close"] = df["close"]

        # hfq：后复权派生。hfq = qfq * k，k 为每标的常数（f_latest / f_first）。
        # 逐行比值取中位数而非取单点，抗源端精度噪声、避免某日缺失造成偏差。
        if "hfq_close" in df.columns:
            hfq = pd.to_numeric(df["hfq_close"], errors="coerce")
            close = pd.to_numeric(df["close"], errors="coerce")
            valid = hfq.notna() & close.notna() & (close > 0)
            k = float((hfq[valid] / close[valid]).median()) if valid.any() else 0.0
            if k > 0:
                df["hfq_open"] = df["open"] * k
                df["hfq_high"] = df["high"] * k
                df["hfq_low"] = df["low"] * k
                df["hfq_close"] = df["close"] * k
            else:
                df = df.drop(
                    columns=["hfq_open", "hfq_high", "hfq_low", "hfq_close"],
                    errors="ignore",
                )
        return df
