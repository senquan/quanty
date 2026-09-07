"""R1b 终检：hfq_close 覆盖率 + 后复权序列一致性。

检查项：
1. 覆盖率 —— hfq_close 有值的标的数 / 行数（应接近 100%）
2. 序列无假跳空 —— hfq 与 qfq 只差常数 k ⇒ 假跳空数应与 qfq 相同（R1a 后为 0）
3. 比值恒定 —— 同标的 hfq_close/close 的 std 应 ≈ 0
4. hfq 的时序方向 —— 后复权锚在最早日 ⇒ hfq 首值应等于该标的早期真实价量级

用法：
    .venv/Scripts/python.exe _r1b_verify.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, '.')

from sqlalchemy import text

from app.storage.raw_store import repository

# 板块涨跌停（负值阈），与 _r1a_verify.py 一致。
# {col} 为被检测的价格列；{where} 为完整 WHERE 片段（含关键字）或空串。
_LIMIT_TPL = """
WITH s AS (
    SELECT symbol, timestamp, {col} AS close,
           CASE WHEN symbol LIKE '%%.BJ' THEN -0.31
                WHEN symbol LIKE '30%%' OR symbol LIKE '688%%' OR symbol LIKE '689%%' THEN -0.21
                ELSE -0.11 END AS lim,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp) rn,
           LAG({col}) OVER (PARTITION BY symbol ORDER BY timestamp) pc
    FROM factor.raw_bars
    {where}
)
SELECT count(*) n, count(DISTINCT symbol) syms, min(timestamp)::date, max(timestamp)::date
FROM s WHERE pc > 0 AND close/pc - 1 < lim AND rn > 5
"""

LIMIT_SQL = _LIMIT_TPL.format(col="close", where="{where}")


def main() -> None:
    with repository._engine.connect() as c:
        print('=' * 68)
        print('R1b 终检：hfq_close 覆盖率与一致性')
        print('=' * 68)

        # 1) 覆盖率
        tot_sym, tot_row = c.execute(text(
            'SELECT count(DISTINCT symbol), count(*) FROM factor.raw_bars'
        )).fetchone()
        hfq_sym, hfq_row = c.execute(text(
            'SELECT count(DISTINCT symbol), count(*) FROM factor.raw_bars '
            'WHERE hfq_close IS NOT NULL'
        )).fetchone()
        print(f'  标的覆盖 : {hfq_sym} / {tot_sym}  ({hfq_sym / tot_sym * 100:.1f}%)')
        print(f'  行数覆盖 : {hfq_row} / {tot_row}  ({hfq_row / tot_row * 100:.1f}%)')

        miss = c.execute(text(
            'SELECT count(DISTINCT symbol) FROM factor.raw_bars '
            'WHERE hfq_close IS NULL'
        )).fetchone()[0]
        if miss:
            print(f'  ⚠️ 未覆盖标的: {miss} 只')
            rows = c.execute(text(
                'SELECT DISTINCT symbol FROM factor.raw_bars '
                'WHERE hfq_close IS NULL ORDER BY symbol LIMIT 10'
            )).fetchall()
            print(f'     样例: {[r[0] for r in rows]}')
        print()

        # 2) 比值恒定
        print('  --- hfq_close / close 比值（每标的应为常数）---')
        bad = c.execute(text("""
            SELECT symbol,
                   count(*) n,
                   min(hfq_close/close) k_min,
                   max(hfq_close/close) k_max,
                   stddev(hfq_close/close) k_std,
                   avg(hfq_close/close)  k_avg
            FROM factor.raw_bars
            WHERE hfq_close IS NOT NULL AND close > 0
            GROUP BY symbol
            HAVING stddev(hfq_close/close) / NULLIF(avg(hfq_close/close), 0) > 0.001
            ORDER BY (stddev(hfq_close/close)/avg(hfq_close/close)) DESC
            LIMIT 5
        """)).fetchall()
        if not bad:
            print('  ✅ 全部标的比值恒定（相对 std < 0.1%）')
        else:
            print(f'  ⚠️ {len(bad)} 只比值离散 > 0.1%：')
            for sym, n, kmin, kmax, kstd, kavg in bad:
                print(f'     {sym}: n={n} k={kavg:.4f} '
                      f'[{kmin:.4f}, {kmax:.4f}] std={kstd:.6f}')

        ex = c.execute(text("""
            SELECT symbol, round(avg(hfq_close/close)::numeric, 4) k
            FROM factor.raw_bars
            WHERE hfq_close IS NOT NULL AND close > 0
              AND symbol IN ('600519.SH','000001.SZ','002594.SZ','920808.BJ')
            GROUP BY symbol
        """)).fetchall()
        if ex:
            print('  样例 k:', {s: float(k) for s, k in ex})
        print()

        # 3) 假跳空（qfq 基线 vs hfq）
        n0, s0, d0, d1 = c.execute(text(LIMIT_SQL.format(where=''))).fetchone()
        print(f'  qfq(close)  假跳空: {n0} 条 / {s0} 只'
              + (f'  {d0}~{d1}' if n0 else '  ✅'))
        nh, sh, dh0, dh1 = c.execute(text(
            _LIMIT_TPL.format(col="hfq_close", where="WHERE hfq_close IS NOT NULL")
        )).fetchone()
        print(f'  hfq         假跳空: {nh} 条 / {sh} 只'
              + (f'  {dh0}~{dh1}' if nh else '  ✅'))
        print()

        ok = (miss == 0) and (nh == 0) and (not bad)
        print('  判定:', '✅ 通过' if ok else '⚠️ 存在问题，见上')


if __name__ == '__main__':
    main()
