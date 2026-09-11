"""D-8 补充：单条 mention 的三点独立对账（人工可复核的最小样例）。

挑 600900.SH @ 2026-06-09，把「起止交易日 + 两端 hfq_close + 基准两端」全部
打印出来，任何人都能用行情软件核这几个数 —— 这就是 P2 验收要的"手工核对"。
"""
import sys

sys.path.insert(0, ".")
from app.intel.aggregate.profile import BENCHMARK_SYMBOL, _load_benchmark_series
from app.intel.store import get_engine

e = get_engine()
SYM = "600900.SH"
PUB = "2026-06-09"
WINDOWS = (20, 60)

cal, bench_vals = _load_benchmark_series(e)
cal_dates = [x.date() for x in cal]

import bisect

idx = bisect.bisect_left(cal_dates, __import__("datetime").date.fromisoformat(PUB))
print(f"标的 {SYM}   提及日 {PUB}")
print(f"日历起点索引 idx={idx} → 首个交易日 = {cal_dates[idx]}")
print()

for w in WINDOWS:
    s_i, e_i = idx, idx + w
    sd, ed = cal_dates[s_i], cal_dates[e_i]
    with e.connect() as c:
        bars = c.execute(
            __import__("sqlalchemy").text("""
            SELECT timestamp::date d, hfq_close, close
            FROM factor.raw_bars
            WHERE symbol = :s AND freq = '1d'
              AND timestamp::date IN (:d0, :d1)
            ORDER BY timestamp
        """),
            {"s": SYM, "d0": sd, "d1": ed},
        ).mappings().all()
    b0 = bars[0]["hfq_close"] if bars[0]["hfq_close"] else bars[0]["close"]
    b1 = bars[-1]["hfq_close"] if bars[-1]["hfq_close"] else bars[-1]["close"]
    br0, br1 = bench_vals[s_i], bench_vals[e_i]
    stock = (b1 / b0 - 1) * 100
    bench = (br1 / br0 - 1) * 100
    print(f"── 窗口 {w} 交易日 ──")
    print(f"   标的起 {sd} hfq_close={float(b0):.4f}")
    print(f"   标的止 {ed} hfq_close={float(b1):.4f}")
    print(f"   标的收益 = {stock:+.4f}%")
    print(f"   基准起 {sd} 中证全指={float(br0):.4f}")
    print(f"   基准止 {ed} 中证全指={float(br1):.4f}")
    print(f"   基准收益 = {bench:+.4f}%")
    print(f"   ★ 超额收益 = {stock - bench:+.4f}%")
    print()
