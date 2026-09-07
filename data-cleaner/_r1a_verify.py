"""R1a 校验：统计 factor.raw_bars 里还剩多少「除权假跳空」。

判据（v2）：分板块涨跌停阈值 + 排除序列前 5 行（新股上市初期无涨跌幅限制）
- 主板 ±10%      → 单日跌幅 < -11% 即除权
- 创业板/科创板 ±20% → < -21%
- 北交所 ±30%    → < -31%

用法：
    .venv\\Scripts\\python.exe _r1a_verify.py                # 全库统计
    .venv\\Scripts\\python.exe _r1a_verify.py --top 15       # 列出最严重的标的
    .venv\\Scripts\\python.exe _r1a_verify.py --since 2026   # 只查某年之后
"""
import argparse
import sys

sys.path.insert(0, '.')

from sqlalchemy import text

from app.storage.raw_store import repository

CASE = """
CASE WHEN symbol LIKE '%.BJ' THEN -0.31
     WHEN symbol LIKE '30%' OR symbol LIKE '688%' OR symbol LIKE '689%' THEN -0.21
     ELSE -0.11 END
"""

LIMIT_SQL = f"""
WITH s AS (
  SELECT symbol, timestamp, close, source,
         {CASE} AS lim,
         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp) rn,
         LAG(close) OVER (PARTITION BY symbol ORDER BY timestamp) pc
  FROM factor.raw_bars
  WHERE 1=1 {{where}}
)
SELECT count(*) n, count(DISTINCT symbol) syms, min(timestamp)::date, max(timestamp)::date
FROM s WHERE pc > 0 AND pc IS NOT NULL AND close/pc - 1 < lim AND rn > 5
"""

TOP_SQL = f"""
WITH s AS (
  SELECT symbol, timestamp, close, source,
         {CASE} AS lim,
         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp) rn,
         LAG(close) OVER (PARTITION BY symbol ORDER BY timestamp) pc
  FROM factor.raw_bars
  WHERE 1=1 {{where}}
)
SELECT symbol, timestamp::date d, round(pc::numeric,2) prev_close,
       round(close::numeric,2) close,
       round(((close/pc-1)*100)::numeric,2) pct, source
FROM s WHERE pc > 0 AND pc IS NOT NULL AND close/pc - 1 < lim AND rn > 5
ORDER BY pct LIMIT {{limit}}
"""

BY_SOURCE = f"""
WITH s AS (
  SELECT symbol, close, source,
         {CASE} AS lim,
         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp) rn,
         LAG(close) OVER (PARTITION BY symbol ORDER BY timestamp) pc
  FROM factor.raw_bars
  WHERE 1=1 {{where}}
)
SELECT source, count(*) n, count(DISTINCT symbol) syms
FROM s WHERE pc > 0 AND pc IS NOT NULL AND close/pc - 1 < lim AND rn > 5
GROUP BY source ORDER BY n DESC
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=0, help='列出最严重的 N 条')
    ap.add_argument('--since', default='', help='只查该日期之后，如 2026-01-01')
    args = ap.parse_args()

    where = ''
    if args.since:
        where = f" AND timestamp >= '{args.since}'"

    with repository._engine.connect() as c:
        n, syms, d0, d1 = c.execute(
            text(LIMIT_SQL.format(where=where))).fetchone()
        print('=' * 70)
        print('R1a 校验：raw_bars 除权假跳空')
        print('=' * 70)
        scope = f'（{args.since} 之后）' if args.since else '（全库）'
        print(f'范围 {scope}')
        print(f'  假跳空      : {n} 条 / {syms} 只标的'
              + (f'   {d0} ~ {d1}' if n else ''))
        print()
        print('  按源分布:')
        for src, cnt, s2 in c.execute(text(BY_SOURCE.format(where=where))).fetchall():
            print(f'    {src:<18s} {cnt:>6d} 条 / {s2:>5d} 只')
        print()
        if n == 0:
            print('  >>> 通过：无除权假跳空')
        else:
            print('  >>> 未通过：仍存在假跳空')
            if args.top:
                print()
                print('  最严重案例:')
                for r in c.execute(
                        text(TOP_SQL.format(where=where, limit=args.top))).fetchall():
                    print(f'    {r[0]}  {r[1]}  {r[2]} -> {r[3]}  {r[4]}%  [{r[5]}]')


if __name__ == '__main__':
    main()
