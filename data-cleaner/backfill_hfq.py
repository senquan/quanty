"""R1b：用 akshare 补齐 raw_bars.hfq_close（后复权收盘价）全历史。

## 为什么做这个

qfq（前复权）以「最新一日」为锚：标的每除权一次，锚点前移，该标的**全部历史行**
都要重算。而增量写入只写当天、从不回头 ⇒ 历史必然腐化，只能靠周期性全量重拉
（R1a 已验证：单次约 23 分钟）。

hfq（后复权）以「最早一日」为锚：历史值**永不改变**。回测与时序因子改用 hfq，
就能摆脱「定期全量重拉」这个包袱。

## 为什么用 akshare

| 源 | 复权因子可用性 | 结论 |
|---|---|---|
| alphafeed | `GET /v1/klines/ex-factors` → **403 No permission for 除权因子查询 (markets: CN)** | 套餐不含，改代码无用 |
| tushare | `adj_factor` 限频 **1 次/分钟**（实测甚至 1 次/小时）→ 5555 只需 90+ 小时 | 不可行 |
| pandadata | 无复权因子接口 | 不支持 |
| **akshare** | `stock_zh_a_daily(adjust='hfq')` 全历史一次返回，覆盖 SH/SZ/科创/创业/北交 | ✅ 实测 6/6 通过 |

## 实现要点（关键优化）

hfq 与 qfq 只差一个**每标的常数** k = f_latest / f_first，故：
    hfq_close = close * k
**每只标的只需 1 条 UPDATE**（`WHERE symbol = :s`），而非 600 万行逐行写。

k 取重叠日期的 `hfq/qfq` **中位数**（而非末日单点）——抗源端精度噪声。
实测各标的该比值 std/mean 在 0.03%~0.34%，中位数稳健。

## 用法

    # 先试跑 20 只（只算不写）
    .venv/Scripts/python.exe backfill_hfq.py --limit 20

    # 全量（并发写库）
    .venv/Scripts/python.exe backfill_hfq.py --workers 6

    # 只补指定板块
    .venv/Scripts/python.exe backfill_hfq.py --market bj

状态文件 data/backfill_hfq_state.json，中断可续跑。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.')

import pandas as pd
from sqlalchemy import text

from app.core.logging import get_logger
from app.storage.raw_store import repository

logger = get_logger(__name__)

STATE_FILE = Path('data/backfill_hfq_state.json')
LOG_FILE = Path('data/backfill_hfq.log')

# akshare 前缀：北交所为 'bj'，其余为 sh/sz
_PREFIX = {'SH': 'sh', 'SZ': 'sz', 'BJ': 'bj'}

_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'done': [], 'failed': {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATE_FILE)


def all_symbols(market: str | None) -> list[str]:
    with repository._engine.connect() as c:
        rows = c.execute(text(
            'SELECT DISTINCT symbol FROM factor.raw_bars ORDER BY symbol'
        )).fetchall()
    syms = [r[0] for r in rows]
    if market == 'bj':
        return [s for s in syms if s.endswith('.BJ')]
    if market == 'shsz':
        return [s for s in syms if not s.endswith('.BJ')]
    return syms


def fetch_k(symbol: str, end: str | None = None) -> tuple[float | None, int, str]:
    """返回 (k, 重叠天数, 说明)。k = hfq/qfq 中位数。

    主路径 akshare；失败时对 SH/SZ 回退 pandadata（北交所只能用 akshare）。
    """
    import akshare as ak

    end = end or pd.Timestamp.now().strftime('%Y-%m-%d')

    suffix = symbol.rsplit('.', 1)[-1].upper()
    prefix = _PREFIX.get(suffix)
    if prefix is None:
        return None, 0, f'未知后缀 {suffix}'
    code = symbol.rsplit('.', 1)[0]

    akshare_err = ''
    try:
        hfq = ak.stock_zh_a_daily(symbol=prefix + code, adjust='hfq')
        qfq = ak.stock_zh_a_daily(symbol=prefix + code, adjust='qfq')
        if hfq is not None and not hfq.empty and qfq is not None and not qfq.empty:
            a = hfq[['date', 'close']].rename(columns={'close': 'hfq'})
            b = qfq[['date', 'close']].rename(columns={'close': 'qfq'})
            m = a.merge(b, on='date')
            m['qfq'] = pd.to_numeric(m['qfq'], errors='coerce')
            m['hfq'] = pd.to_numeric(m['hfq'], errors='coerce')
            m = m[(m['qfq'] > 0) & (m['hfq'] > 0)].dropna()
            if not m.empty:
                ratio = m['hfq'] / m['qfq']
                k = float(ratio.median())
                note = ''
                if float(ratio.std()) / k > 0.01:  # 相对 std > 1% 视为异常
                    note = f'比值离散 std/k={float(ratio.std()) / k:.4f}'
                return k, len(m), note
        akshare_err = 'akshare 返回空'
    except Exception as e:
        akshare_err = f'{type(e).__name__}: {str(e)[:60]}'

    # 主路径不可用 → pandadata 兜底（SH/SZ 支持 adjust="post"）
    k, n, note = _k_from_pandadata(symbol, end)
    if k is not None:
        return k, n, f'{note}（akshare: {akshare_err}）'
    return None, 0, f'akshare {akshare_err} | {note}'


def _k_from_pandadata(symbol: str, end: str) -> tuple[float | None, int, str]:
    """兜底：用 pandadata 的 post/pre 两序列算 k。

    akshare 并非万能 —— 实测 `689009.SH`（九号公司，科创板 CDR）在其新浪源上
    两种复权均返回 JSONDecodeError，而 pandadata 支持 SH/SZ 的 `adjust="post"`。
    取最近 1 年计算（pandadata 单次区间上限 5 年，且 k 与区间无关）。
    """
    if symbol.upper().endswith('.BJ'):
        return None, 0, '北交所 pandadata 不支持'
    try:
        from app.ingestion.pandadata_source import PandadataSource

        src = PandadataSource()
        start = (pd.Timestamp(end) - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
        pre = src.fetch_daily([symbol], start, end, adjust='pre')
        post = src.fetch_daily([symbol], start, end, adjust='post')
        if pre is None or post is None or pre.empty or post.empty:
            return None, 0, 'pandadata pre/post 返回空'
        a = post[['timestamp', 'close']].rename(columns={'close': 'hfq'})
        b = pre[['timestamp', 'close']].rename(columns={'close': 'qfq'})
        m = a.merge(b, on='timestamp')
        m['qfq'] = pd.to_numeric(m['qfq'], errors='coerce')
        m['hfq'] = pd.to_numeric(m['hfq'], errors='coerce')
        m = m[(m['qfq'] > 0) & (m['hfq'] > 0)].dropna()
        if m.empty:
            return None, 0, 'pandadata 无有效重叠'
        return float((m['hfq'] / m['qfq']).median()), len(m), 'pandadata 兜底'
    except Exception as e:
        return None, 0, f'pandadata 兜底失败: {type(e).__name__}: {str(e)[:80]}'


def apply_k(symbol: str, k: float) -> int:
    """hfq_close = close * k，整只标的一次 UPDATE。"""
    with repository._engine.begin() as c:
        r = c.execute(
            text('UPDATE factor.raw_bars SET hfq_close = close * :k '
                 'WHERE symbol = :s'),
            {'k': k, 's': symbol},
        )
        return r.rowcount


def worker(symbol: str, state: dict, dry: bool) -> tuple[str, str, str]:
    """返回 (symbol, status, detail)"""
    try:
        k, n, note = fetch_k(symbol)
        if k is None:
            return symbol, 'empty', note
        rows = 0 if dry else apply_k(symbol, k)
        return symbol, 'ok', f'k={k:.6f} 重叠{n}天 更新{rows}行 {note}'.strip()
    except Exception as e:
        return symbol, 'error', f'{type(e).__name__}: {str(e)[:120]}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 只（试跑）')
    ap.add_argument('--market', choices=['all', 'shsz', 'bj'], default='all')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--dry', action='store_true', help='只算 k 不写库')
    ap.add_argument('--reset', action='store_true', help='忽略断点，全部重跑')
    args = ap.parse_args()

    state = load_state()
    if args.reset:
        state = {'done': [], 'failed': {}}

    done = set(state['done'])
    syms = all_symbols(None if args.market == 'all' else args.market)
    todo = [s for s in syms if s not in done]
    if args.limit:
        todo = todo[:args.limit]

    _log(f'R1b hfq 补数开始 | 目标 {len(todo)} 只 | 已完成 {len(done)} | '
         f'workers={args.workers} | dry={args.dry}')
    t0 = time.time()
    n_ok = n_err = n_empty = 0
    samples: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, s, state, args.dry): s for s in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            sym, status, detail = fut.result()
            with _lock:
                if status == 'ok':
                    n_ok += 1
                    state['done'].append(sym)
                    if len(samples) < 8:
                        samples.append((sym, detail))
                elif status == 'empty':
                    n_empty += 1
                    state['failed'][sym] = detail
                else:
                    n_err += 1
                    state['failed'][sym] = detail
                if i % 100 == 0:
                    save_state(state)
                    _log(f'  进度 {i}/{len(todo)}  ok={n_ok} empty={n_empty} '
                         f'err={n_err}  用时{time.time() - t0:.0f}s')

    save_state(state)
    _log(f'结束 | ok={n_ok} empty={n_empty} err={n_err} | '
         f'用时 {time.time() - t0:.1f}s')
    if samples:
        _log('样例:')
        for s, d in samples:
            _log(f'  {s}: {d}')
    if state['failed']:
        _log(f'失败标的（前10）: {list(state["failed"].items())[:10]}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
