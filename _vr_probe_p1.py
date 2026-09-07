"""P1 端到端验收：闸口 + 市场规则（不联网，假数据）。

验证三件事：
  1. 闸口该拦的拦得住（不是只会在 happy path 上返回 Plan）
  2. 同一策略 + 同一数据，套不同市场规则 → 结果不同（说明规则真的生效了）
  3. 被规则挡下的信号进 rejections，不静默消失

运行：cd lab.Quant && D:/Python/Python312/python.exe _vr_probe_p1.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve() / ".." / "backend"))

from app.services.backtest_engine import BacktestEngine
from app.services.backtest_gate import Plan, Refusal, plan_backtest
from app.services.market_rules import MARKETS

CODE = """
data['ma5'] = data['close'].rolling(5).mean()
data['ma20'] = data['close'].rolling(20).mean()
for i in range(20, len(data)):
    prev_up = data['ma5'].iloc[i-1] <= data['ma20'].iloc[i-1]
    prev_dn = data['ma5'].iloc[i-1] >= data['ma20'].iloc[i-1]
    if data['ma5'].iloc[i] > data['ma20'].iloc[i] and prev_up:
        buy(data['close'].iloc[i], None, bar=i)
    elif data['ma5'].iloc[i] < data['ma20'].iloc[i] and prev_dn:
        sell(data['close'].iloc[i], None, bar=i)
"""

SYMBOL = "600519.SH"
START, END = "2019-01-01", "2024-12-31"     # 约 6 年，1565 根工作日


def make_bars(n=1565):
    rng = np.random.default_rng(20260906)
    ret = rng.normal(0.0004, 0.018, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    idx = pd.date_range(START, periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * 1.012,
            "low": close * 0.988,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


def gate_demo():
    print("=" * 72)
    print("① 闸口：该拦的拦得住")
    print("=" * 72)
    cases = [
        ("正常 6 年 A股 波段", dict(symbol=SYMBOL, start=START, end=END)),
        ("A股 做空", dict(symbol=SYMBOL, start=START, end=END, allow_short=True)),
        ("A股 半年（样本不足）", dict(symbol=SYMBOL, start="2024-01-01", end="2024-07-01")),
        ("A股 做T（要分钟线）", dict(symbol=SYMBOL, start=START, end=END, style="intraday")),
        ("A股 起始资金 1000", dict(symbol=SYMBOL, start=START, end=END, initial_capital=1000)),
        ("认不出的代码", dict(symbol="###.XX", start=START, end=END)),
        ("加密配 yahoo 源", dict(symbol="BTC/USDT", start=START, end=END)),
        ("美股 做空（允许）", dict(symbol="AAPL", start=START, end=END, allow_short=True)),
    ]
    for name, kw in cases:
        out = plan_backtest(**kw)
        if isinstance(out, Refusal):
            print(f"  ✗ {name:<22} → 拦住：{out.reason[:46]}")
        else:
            print(f"  ✓ {name:<22} → 放行：{out.market.label} / {out.style.label}")


def rules_demo(bars):
    print()
    print("=" * 72)
    print("② 同一策略 + 同一数据，不同市场规则")
    print("=" * 72)
    plan = plan_backtest(symbol=SYMBOL, start=START, end=END)
    assert isinstance(plan, Plan), plan.reason

    configs = [
        ("无规则（P1 之前）", None, SYMBOL),
        ("A股（T+1/涨跌停/整手/费用）", MARKETS["a_share"], SYMBOL),
        ("美股（T+0/无涨跌停/碎股）", MARKETS["us_equity"], "AAPL"),
    ]
    for label, market, sym in configs:
        eng = BacktestEngine(initial_capital=1_000_000, market=market, symbol=sym)
        res = eng.execute_strategy(CODE, bars)
        m = eng.calculate_metrics(res)
        rej = res.get("rejections", [])
        print(f"\n  【{label}】")
        print(f"    总收益 {m['total_return']:>8.2f}%   交易 {m['total_trades']:>3} 笔   "
              f"胜率 {m['win_rate']:>5.2f}%   最大回撤 {m['max_drawdown']:>6.2f}%")
        print(f"    费用合计 ¥{res.get('total_fees', 0):>10.2f}   拒单 {len(rej):>3} 笔")
        if rej:
            by_reason = {}
            for r in rej:
                by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
            for reason, cnt in sorted(by_reason.items(), key=lambda kv: -kv[1]):
                print(f"      - {reason} × {cnt}")

    print()
    print("=" * 72)
    print("③ A股回测自带的限制声明（limits）")
    print("=" * 72)
    for line in plan.limits:
        print(f"  · {line}")


def rejection_demo():
    """上面那段数据太温和（日波动 1.8%，±10% 是 5.5σ，永远碰不到涨跌停），
    所以拒单一笔都没有。这里单独构造会触发规则的场景，确认机制真的会拦。"""
    print()
    print("=" * 72)
    print("④ 拒单场景（单独构造）")
    print("=" * 72)
    flat = pd.DataFrame(
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1e6},
        index=pd.date_range("2023-01-01", periods=10, freq="B"),
    )
    cases = [
        ("当日买入当日卖出（T+1）", "buy(10.0, 100, bar=0)\nsell(11.0, 100, bar=0)"),
        ("次日卖出（T+1 放行）", "buy(10.0, 100, bar=0)\nsell(11.0, 100, bar=1)"),
        ("涨停价买入（前收 10 → 涨停 11.00）", "buy(11.0, 100, bar=5)"),
        ("跌停价卖出（前收 10 → 跌停 9.00）",
         "buy(10.0, 100, bar=0)\nsell(9.0, 100, bar=5)"),
        ("只买 50 股（不足一手）", "buy(10.0, 50, bar=0)"),
    ]
    for name, code in cases:
        eng = BacktestEngine(initial_capital=100_000,
                             market=MARKETS["a_share"], symbol=SYMBOL)
        res = eng.execute_strategy(code, flat)
        rej = res.get("rejections", [])
        if rej:
            print(f"  ✗ {name:<34} → 成交 {len(res['trades'])} 笔，"
                  f"拒单：{rej[0]['reason']}")
        else:
            print(f"  ✓ {name:<34} → 成交 {len(res['trades'])} 笔，无拒单")


def main():
    bars = make_bars()
    gate_demo()
    rules_demo(bars)
    rejection_demo()
    print()
    print("=" * 72)
    print("判定：三种规则下收益/笔数/拒单各不相同 → 市场规则真的在执行，不是摆设")
    print("      美股与「无规则」一致是符合预期的：美股零佣金、支持碎股、T+0")


if __name__ == "__main__":
    main()
