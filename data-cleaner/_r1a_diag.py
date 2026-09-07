"""诊断：为何部分标的 adjust='pre' 后除权跳空未被抹平。

对问题标的打印除权日前后若干日的「不复权 / 前复权」逐日对照，
并算出每日 前复权/不复权 比值 —— 标准前复权下该比值应在除权日发生**跳变**，
除权日之后恒为 1.0。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from app.ingestion.pandadata_source import PandadataSource

CASES = [
    ("688615.SH", "2024-10-09", 4),
    ("603395.SH", "2024-11-27", 4),
    ("002594.SZ", "2025-07-29", 3),   # 成功案例，作对照
]

START, END = "2024-01-01", "2026-09-04"


def main() -> None:
    src = PandadataSource()
    for sym, ex_date, span in CASES:
        print("=" * 72)
        print(f"{sym}   除权日 {ex_date}")
        print("=" * 72)
        raw = src.fetch_daily([sym], START, END, adjust=None).set_index("timestamp").sort_index()
        pre = src.fetch_daily([sym], START, END, adjust="pre").set_index("timestamp").sort_index()
        common = raw.index.intersection(pre.index).sort_values()

        d = pd.Timestamp(ex_date)
        pos = list(common).index(d) if d in common else None
        if pos is None:
            print(f"  除权日 {ex_date} 不在序列中")
            continue
        window = common[max(0, pos - span): pos + span + 1]

        print(f"  {'日期':<12s} {'不复权':>10s} {'前复权':>10s} {'比值':>8s} {'不复权涨跌':>11s} {'前复权涨跌':>11s}")
        prev_r = prev_p = None
        for t in window:
            r, p = float(raw.loc[t, "close"]), float(pre.loc[t, "close"])
            ratio = p / r if r else float("nan")
            cr = f"{(r/prev_r-1)*100:+10.2f}%" if prev_r else " " * 11
            cp = f"{(p/prev_p-1)*100:+10.2f}%" if prev_p else " " * 11
            mark = "  <<< 除权日" if t == d else ""
            print(f"  {t.date()!s:<12s} {r:>10.2f} {p:>10.2f} {ratio:>8.4f} {cr} {cp}{mark}")
            prev_r, prev_p = r, p

        # 全序列比值：标准前复权应为「阶梯状」，除权日后恒为 1.0
        ratios = (pre.loc[common, "close"] / raw.loc[common, "close"]).round(6)
        uniq = sorted(ratios.unique())
        print(f"\n  全序列比值取值数: {len(uniq)}  最小={min(uniq):.6f} 最大={max(uniq):.6f}")
        print(f"  尾段(最后5日)比值: {list(ratios.tail(5))}")
        print(f"  首段(前5日)比值  : {list(ratios.head(5))}")
        print()


if __name__ == "__main__":
    main()
