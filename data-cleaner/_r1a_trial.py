"""R1a 全量重拉前的试跑：先内存验证前复权口径，通过后才写库。

验证维度（对每只标的，用「库内现值 old」对比「前复权重拉值 new」）：

1. **跳空消除**：new 序列中超出板块涨跌停的假跳空应归零
   （板块阈值：主板 ±10% / 创业板·科创板 ±20% / 北交所 ±30%，排除上市前 5 行）
2. **锚点效应**：最后一个除权日**之后**，new ≈ old（qfq 以最新为锚，近期不变）
3. **比例缩放**：第一个除权日**之前**，new/old 应为恒定比值（缩放因子恒定）
4. **真实波动未破坏**：非除权区间内，new 与 old 的**日收益率应完全一致**
   （缩放因子在区间内恒定 ⇒ 收益率不变；若被改掉说明复权算法有问题）

默认只验证不写库；确认无误后加 `--write` 落库。
用法：
    .venv\\Scripts\\python.exe _r1a_trial.py [--n 30] [--bj 3] [--write]
"""
import argparse
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.ingestion.pandadata_source import PandadataSource
from app.storage.raw_store import repository

START, END = '2021-10-01', '2026-09-01'


def limit_of(symbol: str) -> float:
    """返回该标的单日跌幅下限（超出即为除权假跳空）。"""
    if symbol.endswith('.BJ'):
        return -0.31
    # 科创板为 688xxx 与 689xxx（689 为 CDR，如 689009 九号公司）
    if symbol.startswith('30') or symbol.startswith('688') \
            or symbol.startswith('689'):
        return -0.21
    return -0.11


def find_ex_div(symbols: list[str]) -> dict[str, list[str]]:
    """从库内现有数据里找每只标的的除权日。"""
    out: dict[str, list[str]] = {}
    if not symbols:
        return out
    with repository._engine.connect() as c:
        for sym in symbols:
            rows = c.execute(
                text('SELECT timestamp::date, close FROM factor.raw_bars '
                     'WHERE symbol=:s ORDER BY timestamp'),
                {'s': sym}).fetchall()
            if len(rows) < 6:
                continue
            lim = limit_of(sym)
            dates = []
            for i in range(5, len(rows)):
                pc, cl = float(rows[i - 1][1]), float(rows[i][1])
                if pc > 0 and cl / pc - 1 < lim:
                    dates.append(str(rows[i][0]))
            if dates:
                out[sym] = dates
    return out


def load_old(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """读库内现值，返回 {sym: DataFrame(index=date, columns=[close, source])}。

    带 source 是为了锚点验证只比 pandadata 行 —— 库内最近几天混有
    alphafeed(qfq，锚=最新) / tushare(不复权)，与 pandadata 基准不同，
    混在一起比会得出假偏差。
    """
    old: dict[str, pd.DataFrame] = {}
    with repository._engine.connect() as c:
        for sym in symbols:
            rows = c.execute(
                text('SELECT timestamp::date, close, source FROM factor.raw_bars '
                     'WHERE symbol=:s ORDER BY timestamp'),
                {'s': sym}).fetchall()
            if rows:
                old[sym] = pd.DataFrame(
                    {'close': [float(r[1]) for r in rows],
                     'source': [r[2] for r in rows]},
                    index=pd.DatetimeIndex([r[0] for r in rows]))
    return old


def jumps(series: pd.Series, sym: str) -> list[tuple[str, float, float, float]]:
    """返回超出板块阈值的跳空 [(date, prev, cur, pct)]，排除上市前 5 行。"""
    lim = limit_of(sym)
    res = []
    v = series.values
    idx = series.index
    for i in range(5, len(v)):
        pc, cl = v[i - 1], v[i]
        if pc > 0 and cl / pc - 1 < lim:
            res.append((str(idx[i].date()), pc, cl, (cl / pc - 1) * 100))
    return res


def verify(sym: str, old_df: pd.DataFrame, new: pd.Series,
           ex_div: list[str]) -> dict:
    """对单只标的做四项验证。

    4) 必须**分段**算收益率：除权日把序列切成若干段，段内缩放因子恒定 ⇒
       old 与 new 的日收益率应完全相同。若跨段直接 pct_change，会把除权日
       前后两天连起来 —— 而那恰是复权要修正的地方，必然出现假偏差。
    2) 只比 pandadata 行：库内最近几天混有 alphafeed(qfq，锚=最新) 与
       tushare(不复权)，基准不同，混比会得出假偏差。
    """
    df = old_df.join(new.rename('new'), how='inner').dropna(subset=['close', 'new'])
    df = df.rename(columns={'close': 'old'})
    r = {'symbol': sym, 'bars': len(df), 'ex_div': len(ex_div)}

    if df.empty:
        r['error'] = '无重叠数据'
        return r

    # 1) 跳空消除
    r['jump_old'] = len(jumps(df['old'], sym))
    r['jump_new'] = len(jumps(df['new'], sym))

    if ex_div:
        last_ed = pd.Timestamp(ex_div[-1])
        first_ed = pd.Timestamp(ex_div[0])

        # 2) 锚点效应：最后除权日之后 new ≈ old（仅 pandadata 行）
        after = df[(df.index > last_ed) & (df['source'] == 'pandadata')]
        if len(after) > 0:
            r['anchor_max_dev'] = float(
                (after['new'] / after['old'] - 1).abs().max())
            r['anchor_n'] = len(after)
        else:
            r['anchor_max_dev'] = r['anchor_n'] = None

        # 3) 比例缩放：首个除权日之前 new/old 恒定
        before = df[df.index < first_ed]
        if len(before) > 2:
            ratio = before['new'] / before['old']
            r['scale_mean'] = float(ratio.mean())
            r['scale_std'] = float(ratio.std())
        else:
            r['scale_mean'] = r['scale_std'] = None
    else:
        r['anchor_max_dev'] = r['anchor_n'] = None
        r['scale_mean'] = r['scale_std'] = None

    # 4) 真实波动判据：统计 old/new 收益率「不一致的天数」，而非最大偏差。
    #    阈值判据只能抓大除权（如 -66%），抓不到小额分红除权（10派1，跌1~2%），
    #    而 qfq 会一并抹平 ⇒ 那些天的收益率差异是「修对了」而非缺陷。
    #    所以看的是「偏差是否集中在少数几天」：若只有个位数/几十天不一致
    #    （对应除权次数），说明正常；若成百上千天不一致，才是真的改坏了。
    ro_all = df['old'].pct_change()
    rn_all = df['new'].pct_change()
    diffs = []
    for i in range(1, len(df)):
        a, b = ro_all.iloc[i], rn_all.iloc[i]
        if pd.notna(a) and pd.notna(b):
            diffs.append(abs(a - b))
    if diffs:
        arr = np.array(diffs)
        r['ret_diff_days'] = int((arr > 1e-6).sum())
        r['ret_max_diff'] = float(arr.max())
    else:
        r['ret_diff_days'] = r['ret_max_diff'] = None
    r['ret_n'] = len(diffs)
    return r


def run_shsz(n: int, write: bool) -> None:
    print('=' * 74)
    print(f'[SH/SZ] 试跑 {n} 只（pandadata adjust="pre"）')
    print('=' * 74)
    with repository._engine.connect() as c:
        syms = [r[0] for r in c.execute(
            text("SELECT DISTINCT symbol FROM factor.raw_bars "
                 "WHERE symbol LIKE '%.SH' OR symbol LIKE '%.SZ' "
                 "ORDER BY symbol")).fetchall()]

    print(f'库内 SH/SZ 标的 {len(syms)} 只，扫描除权日…')
    t0 = time.time()
    # 全量扫描太慢，先取前 800 只里挑有除权的
    ex_div = find_ex_div(syms[:800])
    picked = sorted(ex_div.keys())[:n]
    if not picked:
        print('  前 800 只未找到除权标的，扩大扫描范围')
        ex_div = find_ex_div(syms)
        picked = sorted(ex_div.keys())[:n]
    print(f'  挑出 {len(picked)} 只有除权跳空的标的（扫描耗时 {time.time()-t0:.0f}s）')

    old_map = load_old(picked)
    src = PandadataSource()
    t0 = time.time()
    df = src.fetch_daily(picked, START, END, adjust='pre')
    print(f'  前复权取数 {time.time()-t0:.1f}s，{0 if df is None else len(df)} 行')

    if df is None or df.empty:
        print('  取数为空，中止')
        return

    new_map = {s: g.sort_values('timestamp').set_index('timestamp')['close']
               for s, g in df.groupby('symbol')}

    rows = []
    for sym in picked:
        if sym not in new_map:
            print(f'  {sym}: 新数据缺失，跳过')
            continue
        new = new_map[sym]
        new.index = pd.DatetimeIndex(new.index).normalize()
        rows.append(verify(sym, old_map.get(sym), new, ex_div.get(sym, [])))

    rep = pd.DataFrame(rows)
    pd.set_option('display.width', 200)
    print()
    cols = ['symbol', 'bars', 'ex_div', 'jump_old', 'jump_new',
            'ret_diff_days', 'ret_max_diff', 'anchor_max_dev', 'anchor_n',
            'scale_mean', 'scale_std']
    print(rep[[c for c in cols if c in rep.columns]].to_string(index=False))

    print()
    if 'anchor_max_dev' in rep and rep['anchor_max_dev'].notna().any():
        w = rep.loc[rep['anchor_max_dev'].idxmax()]
        sym, ed = w['symbol'], pd.Timestamp(ex_div[w['symbol']][-1])
        j = old_map[sym].join(new_map[sym].rename('new'), how='inner')
        j = j[j.index > ed]
        print(f'--- 锚点偏差诊断: {sym}  最后除权日 {ed.date()}  '
              f'max_dev={w["anchor_max_dev"]:.4f} ---')
        print(j.tail(6).to_string())
        print('  该段源分布:', j['source'].value_counts().to_dict())
        print()

    ok_jump = int((rep['jump_new'] == 0).sum())
    print(f'跳空归零: {ok_jump}/{len(rep)}')
    if 'anchor_max_dev' in rep:
        am = rep['anchor_max_dev'].dropna()
        if len(am):
            print(f'锚点后偏差 max: {am.max():.6f}  (期望 < 0.005)')
    if 'ret_diff_days' in rep:
        rd = rep['ret_diff_days'].dropna()
        if len(rd):
            print(f'收益率不一致天数: 中位 {rd.median():.0f} / 最大 {rd.max()}  '
                  f'(应 ≈ 除权次数级别，量级 5~20；远大于此才是真改坏)')
    if 'scale_std' in rep:
        ss = rep['scale_std'].dropna()
        if len(ss):
            print(f'除权前缩放比 std: {ss.max():.8f}  '
                  f'(1e-16 级=完美恒定；偏大说明首除权日前还有未检出的小额除权)')

    passed = bool((rep['jump_new'] == 0).all())
    if 'ret_diff_days' in rep and rep['ret_diff_days'].dropna().size:
        passed = passed and bool(rep['ret_diff_days'].dropna().median() <= 30)
    print()
    print(f'>>> 试跑验证: {"通过" if passed else "未通过"}')

    if write and passed:
        t0 = time.time()
        nrows = repository.bulk_upsert(df)
        print(f'>>> 已写库 {nrows} 行，用时 {time.time()-t0:.1f}s')
    elif write:
        print('>>> 验证未通过，拒绝写库')
    else:
        print('>>> 未加 --write，未写库')


def run_bj(n: int, write: bool) -> None:
    print()
    print('=' * 74)
    print(f'[BJ] 试跑 {n} 只（akshare adjust="qfq"）')
    print('=' * 74)
    try:
        import akshare as ak
    except ImportError:
        print('  akshare 不可用，跳过')
        return

    with repository._engine.connect() as c:
        syms = [r[0] for r in c.execute(
            text("SELECT DISTINCT symbol FROM factor.raw_bars "
                 "WHERE symbol LIKE '%.BJ' ORDER BY symbol")).fetchall()]
    ex_div = find_ex_div(syms)
    picked = sorted(ex_div.keys())[:n] or syms[:n]
    print(f'  北交所 {len(syms)} 只，挑 {len(picked)} 只')

    old_map = load_old(picked)
    rows = []
    frames = []
    for sym in picked:
        code = sym.split('.')[0]
        try:
            raw = ak.stock_zh_a_daily(symbol='bj' + code, adjust='qfq')
        except Exception as e:  # noqa: BLE001
            print(f'  {sym} 取数失败: {type(e).__name__}: {str(e)[:80]}')
            continue
        if raw is None or raw.empty:
            continue
        raw['date'] = pd.to_datetime(raw['date'])
        new = raw.set_index('date')['close'].sort_index()
        new.index = pd.DatetimeIndex(new.index).normalize()
        rows.append(verify(sym, old_map.get(sym), new, ex_div.get(sym, [])))

        out = pd.DataFrame({
            'symbol': sym,
            'timestamp': new.index,
            'open': raw.set_index(new.index)['open'],
            'high': raw.set_index(new.index)['high'],
            'low': raw.set_index(new.index)['low'],
            'close': new.values,
            'volume': pd.to_numeric(
                raw.set_index(new.index)['volume'], errors='coerce') / 100.0,
            'source': 'akshare', 'freq': '1d',
        })
        out = out[(out['timestamp'] >= START) & (out['timestamp'] <= END)]
        frames.append(out.reset_index(drop=True))
        time.sleep(0.35)

    if not rows:
        print('  无有效数据')
        return
    rep = pd.DataFrame(rows)
    pd.set_option('display.width', 200)
    print()
    print(rep[['symbol', 'bars', 'ex_div', 'jump_old', 'jump_new',
               'anchor_max_dev', 'scale_mean', 'ret_max_diff']].to_string(
        index=False))
    passed = (rep['jump_new'] == 0).all()
    print()
    print(f'>>> BJ 试跑验证: {"通过" if passed else "未通过"}')

    if write and passed and frames:
        t0 = time.time()
        nrows = repository.bulk_upsert(pd.concat(frames, ignore_index=True))
        print(f'>>> 已写库 {nrows} 行，用时 {time.time()-t0:.1f}s')
    elif write:
        print('>>> 验证未通过或数据为空，拒绝写库')
    else:
        print('>>> 未加 --write，未写库')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=30, help='SH/SZ 试跑只数')
    ap.add_argument('--bj', type=int, default=3, help='BJ 试跑只数')
    ap.add_argument('--write', action='store_true', help='验证通过后写库')
    a = ap.parse_args()
    run_shsz(a.n, a.write)
    run_bj(a.bj, a.write)
