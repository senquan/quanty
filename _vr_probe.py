"""回测引擎验收脚本（不联网，假数据，直接调 BacktestEngine）。

覆盖两种策略写法：
  Case A —— 模块级代码（exec 时即执行，自己 for 循环全历史）
  Case B —— def on_data(data, context) 形式（前端模板 edit.vue:56/81/103 生成的写法）

运行：PYTHONUTF8=1 D:/Python/Python312/python.exe _vr_probe.py
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

# Case A：模块级策略
CODE_A = """
data['ma5'] = data['close'].rolling(5).mean()
data['ma20'] = data['close'].rolling(20).mean()
for i in range(20, len(data)):
    if data['ma5'].iloc[i-1] <= data['ma20'].iloc[i-1] and data['ma5'].iloc[i] > data['ma20'].iloc[i]:
        buy(data['close'].iloc[i])
    elif data['ma5'].iloc[i-1] >= data['ma20'].iloc[i-1] and data['ma5'].iloc[i] < data['ma20'].iloc[i]:
        if get_position() > 0:
            sell(data['close'].iloc[i], get_position())
""".strip()

# Case B：前端模板生成的写法（edit.vue:81-99 双均线策略，原样照搬）
CODE_B = """
# 双均线交叉策略
def on_data(data, context):
    import pandas as pd

    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()

    for i in range(50, len(close)):
        # 金叉买入
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])

        # 死叉卖出
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
""".strip()


def run_case(label: str, code: str) -> None:
    print("=" * 68)
    print(f"【{label}】")
    eng = BacktestEngine(initial_capital=100_000)
    res = eng.execute_strategy(code, df)
    trades = res["trades"]
    pv = res["portfolio_values"]

    print(f"  bar 区间   : {df.index[0].date()} -> {df.index[-1].date()} ({n} bars)")
    print(f"  交易笔数   : {len(trades)}")
    if trades:
        ts = trades[0]["timestamp"]
        ts_end = trades[-1]["timestamp"]
        print(f"  首末笔时间 : {ts}  ->  {ts_end}")
        print(f"  末笔       : {trades[-1]}")
    print(f"  组合价值   : 前3 {[round(v, 2) for v in pv[:3]]}  后3 {[round(v, 2) for v in pv[-3:]]}")
    print(f"  组合价值   : 唯一值个数 = {len(set(round(float(v), 2) for v in pv))}  (1 表示全程未变动)")
    print(f"  期末现金   : {round(res['final_capital'], 2)}")
    print(f"  指标       : {eng.calculate_metrics(res)}")

    ok_ts = bool(trades) and all(
        df.index[0] <= t["timestamp"] <= df.index[-1] for t in trades
    )
    ok_pv = len(set(round(float(v), 2) for v in pv)) > 1
    print(f"  >> 判定    : 时间落在回测区间内 = {ok_ts} | 组合价值随持仓变动 = {ok_pv}")

    # 完整链路：引擎 → 指标 → PerformanceAnalyzer（quant.py:231-264 的等价流程）
    from app.services.performance_analyzer import PerformanceAnalyzer

    snapshot = [dict(t) for t in trades]
    analyzer = PerformanceAnalyzer(portfolio_values=pv, trades=trades)
    analysis = analyzer.comprehensive_analysis()
    # comprehensive_analysis() 返回扁平 dict，交易指标直接挂在顶层
    print(f"  分析器     : win_rate={analysis.get('win_rate')} pairs={analysis.get('total_trades')} "
          f"profit_factor={analysis.get('profit_factor')} avg_hold={analysis.get('avg_trade_duration')}天")
    unchanged = snapshot == [dict(t) for t in trades]
    print(f"  >> 判定    : 分析器未污染 trades = {unchanged}")
    for w in res.get("warnings", []):
        print(f"  前提声明   : {w}")


run_case("Case A 模块级策略", CODE_A)
run_case("Case B def on_data（前端模板写法）", CODE_B)
print("=" * 68)
print("说明：Case B 若交易笔数为 0，说明 on_data 定义了但从未被调用。")
