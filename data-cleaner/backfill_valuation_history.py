"""用 pandadata 股本数据 + 已有收盘价，推导并补齐全历史「总市值 / 流通市值」。

背景
----
factor.daily_basic 目前只有 2~3 个交易日（tushare 低权限 token 只能取最近数日），
导致 size 因子（SIZE_MKT_CAP / SIZE_MKT_CAP_CIRC）和 value 因子在该日期之外全为 NaN。

为何用「推导」而不是直接取估值
------------------------------
- tushare daily_basic：实测该 token 仅能取最近约 3 个交易日，更早日全部返回 0 行（权限限制，非限频）
- pandadata：无个股级估值接口（PE/PB/PS 仅指数级 get_index_indicator）
- akshare：历史估值为「单标的 × 单指标」接口，全市场需 1.6 万次调用，不现实

因此改为**精确推导**市值（无需 TTM 等近似）：
    total_mv(万元) = 总股本 × 收盘价 / 1e4
    circ_mv (万元) = 流通A股 × 收盘价 / 1e4
单位已与 tushare 口径核对一致（000001.SZ @2026-08-28：
1.940592e10 股 × 11.65 / 1e4 ≈ 2260.8 万元×1e4，与库存值 22,607,894.703 吻合）。

未覆盖：pb、dv_ttm（需净资产/分红数据，现有数据源均未提供）；
pe_ttm / ps_ttm 需 TTM 汇总（近似），本脚本不写入，避免污染已有的真实值。

写入策略
--------
INSERT ... ON CONFLICT (symbol, trade_date) DO UPDATE 只更新 total_mv / circ_mv，
INSERT 列表不含其它列，故已有的 pe_ttm / pb / ps_ttm / dv_ttm 等保持原值不被覆盖。

用法：
    .venv\\Scripts\\python.exe backfill_valuation_history.py
        [--start 20211001] [--end 20260901] [--batch 200] [--limit N] [--reset]
"""
import argparse
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

import pandas as pd
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.ingestion.pandadata_source import PandadataSource
from app.storage.raw_store import repository

STATE_FILE = Path('data/backfill_valuation_state.json')
LOG_FILE = Path('data/backfill_valuation.log')


def log(msg: str) -> None:
    line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}'
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding='utf-8')


# 注意：INSERT 列表只含待写入的 4 列（updated_at 走默认值），
# 与 VALUES 占位符数量必须一致；DO UPDATE 只改 total_mv / circ_mv / updated_at。
UPSERT_SQL = """
    INSERT INTO factor.daily_basic (symbol, trade_date, total_mv, circ_mv)
    VALUES %s
    ON CONFLICT (symbol, trade_date) DO UPDATE SET
        total_mv   = EXCLUDED.total_mv,
        circ_mv    = EXCLUDED.circ_mv,
        updated_at = now()
"""


def bulk_upsert_mv(conn, rows: list[tuple], page_size: int = 5000) -> int:
    from psycopg2.extras import execute_values

    sql = UPSERT_SQL.replace('    ', '')
    n = 0
    for i in range(0, len(rows), page_size):
        execute_values(conn, sql, rows[i:i + page_size])
        n += len(rows[i:i + page_size])
    return n


def load_closes(symbols: list[str]) -> pd.DataFrame:
    """读取这些标的的收盘价（用于乘以股本）。"""
    eng = create_engine(settings.DATABASE_URL.replace('+asyncpg', '+psycopg2'))
    with eng.connect() as c:
        # 分批传参，避免 SQL 参数过多
        frames = []
        for i in range(0, len(symbols), 500):
            chunk = symbols[i:i + 500]
            df = pd.read_sql(
                text('SELECT symbol, timestamp::date AS d, close FROM factor.raw_bars '
                     'WHERE symbol = ANY(:syms)'),
                c, params={'syms': chunk},
            )
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20211001')
    ap.add_argument('--end', default='20260901')
    ap.add_argument('--batch', type=int, default=200)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--reset', action='store_true')
    args = ap.parse_args()

    eng = create_engine(settings.DATABASE_URL.replace('+asyncpg', '+psycopg2'))
    with eng.connect() as c:
        all_symbols = [r[0] for r in c.execute(
            text('SELECT DISTINCT symbol FROM factor.raw_bars ORDER BY symbol')).fetchall()]
    # pandadata 的 get_share_float 同样不接受 .BJ（与 get_stock_daily 一致）
    bj = [s for s in all_symbols if s.endswith('.BJ')]
    symbols = [s for s in all_symbols if not s.endswith('.BJ')]
    if bj:
        log(f'跳过 {len(bj)} 只 .BJ（pandadata 不支持北交所）；'
            f'其市值可后续用 akshare 新浪源 outstanding_share 推导流通市值')

    state = {} if args.reset else (
        json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {})
    done = set(state.get('batches', []))
    total_rows = int(state.get('rows', 0))
    total_batches = (len(symbols) + args.batch - 1) // args.batch
    log(f'开始推导市值: 标的 {len(symbols)} 只, 共 {total_batches} 批, 已完成 {len(done)} 批')

    src = PandadataSource()
    pd_sdk = src._client()
    t0 = time.time()
    processed = 0
    failed = []

    for bi in range(total_batches):
        if bi in done:
            continue
        if args.limit and processed >= args.limit:
            log(f'达到 --limit {args.limit}，提前结束')
            break
        processed += 1
        lo, hi = bi * args.batch, min((bi + 1) * args.batch, len(symbols))
        batch = symbols[lo:hi]
        try:
            shares = pd_sdk.get_share_float(symbol=batch, start_date=args.start,
                                            end_date=args.end, fields=[])
            if shares is None or shares.empty:
                log(f'批次 {bi} [{lo+1}-{hi}]: 股本返回空')
                done.add(bi)
                continue
            shares = shares.copy()
            shares['d'] = pd.to_datetime(shares['date'], format='%Y%m%d', errors='coerce').dt.date
            shares['total'] = pd.to_numeric(shares['total'], errors='coerce')
            shares['circulation_a'] = pd.to_numeric(shares['circulation_a'], errors='coerce')
            shares = shares.dropna(subset=['d', 'total'])

            closes = load_closes(batch)
            if closes.empty:
                log(f'批次 {bi} [{lo+1}-{hi}]: 无收盘价')
                done.add(bi)
                continue
            closes['close'] = pd.to_numeric(closes['close'], errors='coerce')

            m = shares.merge(closes, on=['symbol', 'd'], how='inner')
            if m.empty:
                log(f'批次 {bi} [{lo+1}-{hi}]: 股本与收盘价无交集')
                done.add(bi)
                continue

            # 单位对齐 tushare：万元 = 股 × 元 / 1e4
            m['total_mv'] = m['total'] * m['close'] / 1e4
            m['circ_mv'] = m['circulation_a'] * m['close'] / 1e4

            rows = [
                (r.symbol, r.d,
                 None if pd.isna(r.total_mv) else float(r.total_mv),
                 None if pd.isna(r.circ_mv) else float(r.circ_mv))
                for r in m.itertuples(index=False)
            ]

            raw = eng.raw_connection()
            try:
                with raw.cursor() as cur:
                    n = bulk_upsert_mv(cur, rows)
                raw.commit()
            finally:
                raw.close()

            total_rows += n
            done.add(bi)
            log(f'批次 {bi} [{lo+1}-{hi}/{len(symbols)}]: 写入 {n} 行, '
                f'累计 {total_rows}, 用时 {time.time()-t0:.0f}s')
            state['batches'] = sorted(done)
            state['rows'] = total_rows
            save_state(state)
        except Exception as e:  # noqa: BLE001
            failed.append(bi)
            log(f'批次 {bi} [{lo+1}-{hi}] 失败: {type(e).__name__}: {str(e)[:200]}')
            time.sleep(3)

    log(f'结束: 累计 {total_rows} 行, 耗时 {time.time()-t0:.0f}s, '
        f'完成 {len(done)}/{total_batches} 批, 失败 {failed}')
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
