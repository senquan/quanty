"""用 pandadata 补齐全市场价量历史深度（可断点续跑）。

背景：factor.raw_bars 原仅约 147 个交易日（每标的最多 103 根 bar），
导致所有 >=250 日窗口的因子（如 GRO_PRICE_MOMENTUM）恒为 NaN。

分流：
- .SH / .SZ：pandadata get_stock_daily（按标的切片，200 只/批，分钟级）
- .BJ（北交所）：pandadata 不支持（返回「后缀必须为SH或SZ」），改走 akshare
  stock_zh_a_hist 逐标的补录

量纲：raw_bars 既有约定 volume 为「手」。pandadata 返回「股」，在适配器中
已折算为「手」；akshare 的 成交量 本就是「手」，无需换算。

用法：
    .venv\\Scripts\\python.exe backfill_history_pandadata.py
        [--start 2021-10-01] [--end 2026-09-01] [--batch 200]
        [--limit N] [--only bj|shsz] [--reset]
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

STATE_FILE = Path('data/backfill_history_state.json')
LOG_FILE = Path('data/backfill_history.log')


def log(msg: str) -> None:
    line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}'
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding='utf-8')


def load_symbols() -> tuple[list[str], list[str]]:
    """返回 (shsz 标的, bj 标的)，以现有 raw_bars 的代码池为准。"""
    eng = create_engine(settings.DATABASE_URL.replace('+asyncpg', '+psycopg2'))
    with eng.connect() as c:
        syms = [r[0] for r in c.execute(
            text('SELECT DISTINCT symbol FROM factor.raw_bars ORDER BY symbol')).fetchall()]
    shsz = [s for s in syms if s.endswith(('.SH', '.SZ'))]
    bj = [s for s in syms if s.endswith('.BJ')]
    return shsz, bj


def run_pandadata(symbols: list[str], start: str, end: str, batch: int,
                  state: dict, limit: int) -> int:
    """pandadata 批量补录 .SH/.SZ。"""
    done = set(state.setdefault('shsz_batches', []))
    total_rows = int(state.get('rows_shsz', 0))
    total_batches = (len(symbols) + batch - 1) // batch
    log(f'[pandadata] 标的 {len(symbols)} 只, 共 {total_batches} 批, 已完成 {len(done)} 批')

    src = PandadataSource()
    t0 = time.time()
    processed = 0
    failed = []
    for bi in range(total_batches):
        if bi in done:
            continue
        if limit and processed >= limit:
            log(f'[pandadata] 达到 --limit {limit}，提前结束')
            break
        processed += 1
        lo, hi = bi * batch, min((bi + 1) * batch, len(symbols))
        try:
            df = src.fetch_daily(symbols[lo:hi], start, end)
            if df is None or df.empty:
                log(f'[pandadata] 批次 {bi} [{lo+1}-{hi}]: 返回空')
                done.add(bi)
            else:
                n = repository.bulk_upsert(df)
                total_rows += n
                done.add(bi)
                log(f'[pandadata] 批次 {bi} [{lo+1}-{hi}/{len(symbols)}]: '
                    f'写入 {n} 行, 累计 {total_rows}, 用时 {time.time()-t0:.0f}s')
            state['shsz_batches'] = sorted(done)
            state['rows_shsz'] = total_rows
            save_state(state)
        except Exception as e:  # noqa: BLE001
            failed.append(bi)
            log(f'[pandadata] 批次 {bi} [{lo+1}-{hi}] 失败: {type(e).__name__}: {str(e)[:200]}')
            time.sleep(3)

    log(f'[pandadata] 结束: 累计 {total_rows} 行, 耗时 {time.time()-t0:.0f}s, '
        f'完成 {len(done)}/{total_batches} 批, 失败 {failed}')
    return total_rows


def run_akshare_bj(symbols: list[str], start: str, end: str, state: dict) -> int:
    """北交所(.BJ) 逐标的补录（akshare）；成交量本就是「手」，无需换算。"""
    import akshare as ak

    done = set(state.setdefault('bj_done', []))
    total_rows = int(state.get('rows_bj', 0))
    log(f'[akshare/BJ] 标的 {len(symbols)} 只, 已完成 {len(done)} 只')

    t0 = time.time()
    ok = fail = 0
    ts_start, ts_end = pd.Timestamp(start), pd.Timestamp(end)
    # 东财 push2his 在本环境连续调用会被 ProxyError 拦截，改用新浪源（bj 前缀）。
    # 仍保留节流 + 指数退避，降低被限流的概率。
    log('[akshare/BJ] 使用新浪源（bj 前缀），逐标的节流补录')

    for i, sym in enumerate(symbols, 1):
        if sym in done:
            continue
        code = sym.split('.')[0]
        df = None
        for attempt in range(4):
            try:
                df = ak.stock_zh_a_daily(symbol='bj' + code, adjust='')
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    log(f'[akshare/BJ] {sym} 重试 {attempt+1} 次仍失败: '
                        f'{type(e).__name__}: {str(e)[:100]}')
                time.sleep(1.5 * (2 ** attempt))  # 1.5s / 3s / 6s 退避
        if df is None or df.empty:
            fail += 1
            continue
        try:
            out = pd.DataFrame({
                'symbol': sym,
                'timestamp': pd.to_datetime(df['date']),
                'open': df['open'], 'high': df['high'],
                'low': df['low'], 'close': df['close'],
                # 新浪 volume 单位为「股」，折算为「手」以对齐本库既有口径
                'volume': pd.to_numeric(df['volume'], errors='coerce') / 100.0,
                'source': 'akshare', 'freq': '1d',
            })
            # 新浪返回全历史（含新三板阶段），按目标区间裁剪
            out = out[(out['timestamp'] >= ts_start) & (out['timestamp'] <= ts_end)]
            if out.empty:
                fail += 1
                continue
            n = repository.bulk_upsert(out)
        except Exception as e:  # noqa: BLE001
            fail += 1
            log(f'[akshare/BJ] {sym} 落库失败: {type(e).__name__}: {str(e)[:140]}')
            continue

        total_rows += n
        done.add(sym)
        ok += 1
        if i % 50 == 0:
            state['bj_done'] = sorted(done)
            state['rows_bj'] = total_rows
            save_state(state)
            log(f'[akshare/BJ] 进度 {i}/{len(symbols)}, 累计 {total_rows} 行, '
                f'用时 {time.time()-t0:.0f}s')
        time.sleep(0.35)  # 节流，避免东财限流

    state['bj_done'] = sorted(done)
    state['rows_bj'] = total_rows
    save_state(state)
    log(f'[akshare/BJ] 结束: ok {ok}, fail {fail}, 累计 {total_rows} 行, '
        f'耗时 {time.time()-t0:.0f}s')
    return total_rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2021-10-01')
    ap.add_argument('--end', default='2026-09-01')
    ap.add_argument('--batch', type=int, default=200)
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 批（0=全部）')
    ap.add_argument('--only', choices=['shsz', 'bj', 'all'], default='all')
    ap.add_argument('--reset', action='store_true', help='忽略 state，从头重跑')
    args = ap.parse_args()

    shsz, bj = load_symbols()
    state = {} if args.reset else (
        json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {}
    )
    log(f'开始补录: 区间 {args.start}~{args.end}; '
        f'SH/SZ {len(shsz)} 只, BJ {len(bj)} 只; reset={args.reset}')

    if args.only in ('shsz', 'all') and shsz:
        run_pandadata(shsz, args.start, args.end, args.batch, state, args.limit)
    if args.only in ('bj', 'all') and bj:
        run_akshare_bj(bj, args.start, args.end, state)

    log('全部完成')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
