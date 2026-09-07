"""补齐单只标的的前复权数据（用于 pandadata 未及时同步除权的残留标的）。

用法：
    .venv\\Scripts\\python.exe _r1a_patch_symbol.py 301237.SZ
"""
import sys
import time

sys.path.insert(0, '.')

import pandas as pd

from app.storage.raw_store import repository

START, END = '2021-10-01', '2026-09-06'
PREFIX = {'.SH': 'sh', '.SZ': 'sz', '.BJ': 'bj'}


def patch(symbol: str) -> None:
    import akshare as ak
    code, suffix = symbol.split('.')
    pref = PREFIX['.' + suffix]
    print(f'{symbol} → akshare {pref}{code} adjust=qfq')

    df = None
    for attempt in range(4):
        try:
            df = ak.stock_zh_a_daily(symbol=pref + code, adjust='qfq')
            break
        except Exception as e:  # noqa: BLE001
            print(f'  第 {attempt+1} 次失败: {type(e).__name__}: {str(e)[:100]}')
            time.sleep(1.5 * (2 ** attempt))
    if df is None or df.empty:
        print('  取数失败')
        return

    df['date'] = pd.to_datetime(df['date'])
    idx = pd.DatetimeIndex(df['date']).normalize()
    out = pd.DataFrame({
        'symbol': symbol,
        'timestamp': idx,
        'open': df['open'].values,
        'high': df['high'].values,
        'low': df['low'].values,
        'close': df['close'].values,
        'volume': pd.to_numeric(df['volume'], errors='coerce').values / 100.0,
        'source': 'akshare',
        'freq': '1d',
    })
    out = out[(out['timestamp'] >= START) & (out['timestamp'] <= END)]
    print(f'  取到 {len(out)} 行，写库…')
    n = repository.bulk_upsert(out)
    print(f'  写入 {n} 行')

    # 复查该标的是否还有假跳空
    from _r1a_verify import LIMIT_SQL
    from sqlalchemy import text
    with repository._engine.connect() as c:
        cnt, syms, d0, d1 = c.execute(
            text(LIMIT_SQL.format(where=f" AND symbol = '{symbol}'"))).fetchone()
        print(f'  复查假跳空: {cnt} 条' + (f'  {d0} ~ {d1}' if cnt else '  ✅ 已归零'))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python _r1a_patch_symbol.py 301237.SZ')
        raise SystemExit(1)
    patch(sys.argv[1])
