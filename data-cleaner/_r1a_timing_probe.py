"""拆分「取数 / 写库」耗时，并实测 upsert 已存在行（重拉路径）的耗时。

背景：
- 历史全量（2026-09-02）pandadata 5214 只共 373s，但那是 取数+bulk_upsert 合计。
- 纯取数实测 400 只仅 7.6s → 写库占大头（约 75%+）。
- 首次回补时行不存在 → upsert 走 INSERT；重拉时行已存在 → 走 UPDATE。
  PG 的 UPDATE 要找行、写新版本、留死元组，通常比 INSERT 慢。
  不实测这一项，全量外推会严重低估。

安全声明：本脚本写入的是**不复权**数据（与库内现状一致），
不会改变任何数据口径，仅用于测量 UPDATE 路径耗时。
"""
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from sqlalchemy import text

from app.ingestion.pandadata_source import PandadataSource
from app.storage.raw_store import repository

START, END = '2021-10-01', '2026-09-01'
BATCH = 400


def main() -> None:
    with repository._engine.connect() as c:
        syms = [r[0] for r in c.execute(
            text('SELECT DISTINCT symbol FROM factor.raw_bars '
                 "WHERE symbol LIKE '%.SH' ORDER BY symbol LIMIT :n"),
            {'n': BATCH}).fetchall()]
    print(f'样本: {len(syms)} 只 .SH 标的（均已存在于 raw_bars）')
    print(f'区间: {START} ~ {END}')
    print('=' * 62)

    src = PandadataSource()

    # 1) 纯取数（不复权），与历史对齐，写入无害
    t0 = time.time()
    df = src.fetch_daily(syms, START, END)  # adjust=None，等价于库内现状
    t_fetch = time.time() - t0
    n = 0 if df is None else len(df)
    print(f'1) 纯取数           : {t_fetch:6.1f}s  行数={n}')

    if df is None or df.empty:
        print('   取数为空，无法继续')
        return

    # 2) upsert 已存在行（重拉路径 = UPDATE）
    t0 = time.time()
    rows = repository.bulk_upsert(df)
    t_upsert = time.time() - t0
    print(f'2) upsert(UPDATE路径): {t_upsert:6.1f}s  写入={rows} 行')

    print('=' * 62)
    total = t_fetch + t_upsert
    per_batch = total / BATCH
    print(f'合计 {total:.1f}s（取数 {t_fetch/total*100:.0f}% / 写库 {t_upsert/total*100:.0f}%）')
    print(f'每只标的 {per_batch:.3f}s')
    print()
    print('外推至全量 5214 只（pandadata）：')
    print(f'  {per_batch * 5214 / 60:.1f} 分钟')
    print()
    print('对照历史（首次回补，INSERT 路径）: 373s = 6.2 分钟')
    print(f'  重拉/首次 倍率 ≈ {per_batch * 5214 / 373:.2f}x')


if __name__ == '__main__':
    main()
