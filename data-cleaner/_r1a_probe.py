"""R1a 试跑：验证 pandadata 前复权(adjust='pre')确实能抹平除权跳空。

判据修正（第一版把新股上市前5日的真实波动误判成除权）：
  - 分板块涨跌停：主板 ±10% / 创业板·科创板 ±20% / 北交所 ±30%
  - 排除上市前 5 个交易日（科创板、创业板新股上市前5日无涨跌幅限制，
    北交所上市首日无限制）—— 那段时间的暴跌是真实炒作崩盘，不是除权

流程：
  1. 从 factor.raw_bars 查出「真·除权跳空」案例（超板块跌停幅度，且非上市初期）
  2. 对每只分别拉 adjust=None（不复权，现役口径）与 adjust='pre'（前复权，拟改口径）
  3. 验证前复权序列内所有单日跌幅是否回落到板块涨跌停以内
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from app.ingestion.pandadata_source import PandadataSource
from app.storage.raw_store import repository
from sqlalchemy import text

START, END = "2022-01-01", "2026-09-04"
N_CASES = 6
NEW_IPO_ROWS = 5  # 排除上市前 N 个交易日

LIMIT_SQL = """CASE WHEN symbol LIKE '%.BJ' THEN -0.31
                    WHEN symbol LIKE '30%' OR symbol LIKE '688%' THEN -0.21
                    ELSE -0.11 END"""


def limit_of(symbol: str) -> float:
    if symbol.endswith(".BJ"):
        return -0.31
    if symbol.startswith("30") or symbol.startswith("688"):
        return -0.21
    return -0.11


def pick_cases(n: int) -> list:
    """从库中挑真·除权跳空案例。"""
    q = text(f"""
      WITH s AS (
        SELECT symbol, timestamp, close, {LIMIT_SQL} AS lim,
               ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp) rn,
               LAG(close) OVER (PARTITION BY symbol ORDER BY timestamp) pc
        FROM factor.raw_bars
        WHERE timestamp >= '{START}' AND timestamp <= '{END}'
      )
      SELECT symbol, timestamp::date d, round(pc::numeric,2) pc,
             round(close::numeric,2) c, round(((close/pc-1)*100)::numeric,2) pct
      FROM s WHERE pc > 0 AND close/pc - 1 < lim AND rn > {NEW_IPO_ROWS}
      ORDER BY pct LIMIT :n
    """)
    with repository._engine.connect() as conn:
        return [tuple(r) for r in conn.execute(q, {"n": n}).fetchall()]


def max_drop(series: pd.Series, skip_first: int = NEW_IPO_ROWS) -> tuple:
    """最大单日跌幅 (日期, 前值, 现值, 幅度)，跳过上市前 N 日。"""
    s = series.iloc[skip_first:]
    if len(s) < 2:
        return None
    r = s / s.shift(1) - 1
    i = r.idxmin()
    return (i, float(s.shift(1)[i]), float(s[i]), float(r.min()))


def main() -> None:
    cases = pick_cases(N_CASES)
    if not cases:
        print("库中未查到除权案例")
        return

    print("=" * 80)
    print(f"R1a 试跑（判据修正版）  区间 {START} ~ {END}  案例 {len(cases)} 只")
    print("排除上市前 5 个交易日；涨跌停分档 主板10% / 创业板·科创板20% / 北交所30%")
    print("=" * 80)

    src = PandadataSource()
    ok_cnt = 0
    for sym, d, pc, c, pct in cases:
        lim = limit_of(sym)
        print(f"\n--- {sym}  除权日 {d}  库中 {pc} -> {c} ({pct}%)  阈值 {lim*100:.0f}% ---")
        try:
            raw = src.fetch_daily([sym], START, END, adjust=None).set_index("timestamp").sort_index()
            pre = src.fetch_daily([sym], START, END, adjust="pre").set_index("timestamp").sort_index()
        except Exception as e:  # noqa: BLE001
            print(f"  拉取失败: {type(e).__name__}: {str(e)[:100]}")
            continue

        if raw.empty or pre.empty:
            print("  返回为空，跳过")
            continue

        j_raw = max_drop(raw["close"])
        j_pre = max_drop(pre["close"])
        print(f"  最大单日跌幅  不复权: {j_raw[0].date()}  {j_raw[1]:.2f} -> {j_raw[2]:.2f}  {j_raw[3]*100:+.2f}%")
        print(f"                前复权: {j_pre[0].date()}  {j_pre[1]:.2f} -> {j_pre[2]:.2f}  {j_pre[3]*100:+.2f}%")

        ok = j_pre[3] >= lim
        print(f"  >> 跳空回落到涨跌停内: {'✅ 是' if ok else '❌ 否'}")
        if ok:
            ok_cnt += 1

    print()
    print("=" * 80)
    print(f"通过 {ok_cnt}/{len(cases)} → {'✅ 前复权生效，可放全量' if ok_cnt == len(cases) else '❌ 有未通过项'}")
    print("=" * 80)


if __name__ == "__main__":
    main()
