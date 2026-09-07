"""验证 qfq 漂移：找 2026 年除权、且被 alphafeed 覆盖的标的，打印其序列。

若漂移真实存在，则除权日之后 alphafeed（qfq）写的值 与 除权日之前
pandadata（不复权）写的值 不在同一价格水平，且除权前的历史行永不被重写。
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text

from app.storage.raw_store import repository

FIND = text("""
WITH s AS (
  SELECT symbol, timestamp, close,
         CASE WHEN symbol LIKE '%.BJ' THEN -0.31
              WHEN symbol LIKE '30%' OR symbol LIKE '688%' THEN -0.21
              ELSE -0.11 END AS lim,
         ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp) rn,
         LAG(close) OVER (PARTITION BY symbol ORDER BY timestamp) pc
  FROM factor.raw_bars WHERE timestamp >= '2026-01-01'
)
SELECT symbol, timestamp::date, round(pc::numeric,2), round(close::numeric,2)
FROM s
WHERE pc > 0 AND close/pc - 1 < lim AND rn > 5
  AND symbol IN (SELECT DISTINCT symbol FROM factor.raw_bars WHERE source='alphafeed')
ORDER BY timestamp DESC LIMIT 3
""")

SEQ = text("""
SELECT timestamp::date d, round(close::numeric,2) c, source s
FROM factor.raw_bars
WHERE symbol = :sy AND timestamp >= '2026-04-01'
ORDER BY timestamp
""")


def main() -> None:
    with repository._engine.connect() as c:
        rows = c.execute(FIND).fetchall()
        if not rows:
            print('未找到 2026 年除权且有 alphafeed 覆盖的标的')
            return
        for sym, d, pc, cl in rows:
            print(f'=== {sym}  2026 年除权日 {d}: {pc} -> {cl} ===')
            prev = None
            for dd, cc, ss in c.execute(SEQ, {'sy': sym}).fetchall():
                mark = ''
                if prev:
                    ch = (cc / prev - 1) * 100
                    mark = f'   {ch:+7.2f}%' + (
                        '   <<<< 除权跳空（未复权）' if abs(ch) > 15 else '')
                print(f'  {dd}  {cc:>9.2f}  [{ss:<10s}]{mark}')
                prev = cc
            print()


if __name__ == '__main__':
    main()
