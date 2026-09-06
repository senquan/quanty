"""最小复现：验证 BacktestEngine 的撮合是否按 bar 时间生效。

不联网。用假数据 + 最朴素的双均线策略，直接调 execute_strategy。
"""
import sys
from pathlib import Path

BACKEND = Path(r"E:\Workspace\lab.Quant\backend")
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.services.backtest_engine import BacktestEngine

# 2023 全年工作日，价格走正弦波（有金叉死叉，保证策略会发信号）
idx = pd.date_range("2023-01-01", "2023-12-31", freq="B")
n = len(idx)
t = np.arange(n)
# 100 ~ 140 之间来回震荡，周期约 60 个交易日，必然产生多次金叉死叉
close = 120.0 + 20.0 * np.sin(2 * np.pi * t / 60.0)
df = pd.DataFrame(
    {
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(n, 1_000_000.0),
    },
    index=idx,
)

CODE = """
data['ma5'] = data['close'].rolling(5).mean()
data['ma20'] = data['close'].rolling(20).mean()
for i in range(20, len(data)):
    if data['ma5'].iloc[i-1] <= data['ma20'].iloc[i-1] and data['ma5'].iloc[i] > data['ma20'].iloc[i]:
        buy(data['close'].iloc[i])
    elif data['ma5'].iloc[i-1] >= data['ma20'].iloc[i-1] and data['ma5'].iloc[i] < data['ma20'].iloc[i]:
        if get_position() > 0:
            sell(data['close'].iloc[i], get_position())
""".strip()

eng = BacktestEngine(initial_capital=100_000)
res = eng.execute_strategy(CODE, df)

print("=" * 60)
print("bar 区间 :", df.index[0].date(), "->", df.index[-1].date(), f"({n} bars)")
print("交易笔数 :", len(res["trades"]))
print("末笔交易 :", res["trades"][-1] if res["trades"] else "无")
print("组合价值 前3:", [round(v, 2) for v in res["portfolio_values"][:3]])
print("组合价值 后3:", [round(v, 2) for v in res["portfolio_values"][-3:]])
print("期末现金 :", round(res["final_capital"], 2))
print("指标     :", eng.calculate_metrics(res))
print("=" * 60)
print("参照：期末价/期初价 = %.4f （价格震荡，用于判断组合价值是否随持仓变动）" % (df["close"].iloc[-1] / df["close"].iloc[0]))
