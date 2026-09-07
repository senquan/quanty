"""R2 冒烟：用真实库数据跑一次脚本策略回测。

用法：.venv/Scripts/python.exe _r2_smoke.py [symbol] [start] [end]
"""
import json
import sys

from app.backtest.service import BacktestRefused, ScriptBacktestRequest, run_script_backtest
from app.backtest.data import BacktestDataError

MA_CROSS = """
def on_data(data, context):
    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()
    for i in range(50, len(close)):
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            pos = get_position()
            if pos > 0:
                sell(close.iloc[i], pos)
""".strip()


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "600519.SH"
    start = sys.argv[2] if len(sys.argv) > 2 else "2021-10-08"
    end = sys.argv[3] if len(sys.argv) > 3 else "2026-09-04"

    for pf in ("qfq", "hfq"):
        print("=" * 72)
        print(f"{symbol}  {start} ~ {end}  price_field={pf}")
        try:
            res = run_script_backtest(ScriptBacktestRequest(
                symbol=symbol, start=start, end=end, code=MA_CROSS,
                style="swing", initial_capital=1_000_000.0, price_field=pf,
            ))
        except BacktestRefused as e:
            print("  闸口拒绝:", json.dumps(e.as_dict(), ensure_ascii=False))
            continue
        except BacktestDataError as e:
            print("  取数失败:", json.dumps(e.as_dict(), ensure_ascii=False))
            continue

        print(f"  bars={res['data']['bars']}  {res['data']['first_date']} ~ {res['data']['last_date']}"
              f"  hfq_k={res['data']['hfq_factor']}")
        print(f"  总收益={res['total_return']}%  夏普={res['sharpe_ratio']}  "
              f"回撤={res['max_drawdown']}%  胜率={res['win_rate']}%  "
              f"成交={res['total_trades']}笔  费用={res['total_fees']}")
        print(f"  期末: 组合={res['final_capital']}  现金={res['cash']}  持仓={res['final_position']}股")
        if res["rejections"]:
            by_reason = {}
            for r in res["rejections"]:
                by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
            print("  拒单:", by_reason)
        for w in res["warnings"]:
            print("  !", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
