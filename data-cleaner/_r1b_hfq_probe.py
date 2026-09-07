"""R1b 路线验证：akshare 能否低成本补齐 hfq（后复权）全市场。

目标：hfq 锚在最早日，历史值永不改变 ⇒ 回测/因子用 hfq 就不必定期全量重拉。
关键问题：
  1) akshare hfq 对 SH/SZ/科创板/北交所 是否都可用？
  2) 单只耗时？全市场 5555 只外推多久？
  3) 并发是否可行（串行若 4h 太慢）？
"""
import sys
import time

sys.path.insert(0, '.')

import akshare as ak
import pandas as pd

SYMS = [
    ('600519.SH', 'sh', '主板 茅台'),
    ('000001.SZ', 'sz', '主板 平安银行'),
    ('002594.SZ', 'sz', '主板 比亚迪（2025 大除权）'),
    ('688615.SH', 'sh', '科创板'),
    ('301237.SZ', 'sz', '创业板（pandadata 漏同步除权）'),
    ('920808.BJ', 'bj', '北交所'),
]


def one(sym: str, prefix: str, label: str) -> dict:
    r = {'symbol': sym, 'label': label}
    t0 = time.time()
    try:
        df = ak.stock_zh_a_daily(symbol=prefix + sym.split('.')[0], adjust='hfq')
        r['sec'] = round(time.time() - t0, 2)
        if df is None or df.empty:
            r['ok'] = False
            r['err'] = '空'
            return r
        df['date'] = pd.to_datetime(df['date'])
        r['ok'] = True
        r['rows'] = len(df)
        r['d0'] = str(df['date'].min().date())
        r['d1'] = str(df['date'].max().date())
        # hfq 特征：锚在最早日 ⇒ 历史价格应「小于等于」qfq，且首日接近真实发行价
        r['first_close'] = float(df['close'].iloc[0])
        r['last_close'] = float(df['close'].iloc[-1])
        # 与 qfq 对比，取同一天比值（应恒定 = f_latest/f_first）
        dfq = ak.stock_zh_a_daily(symbol=prefix + sym.split('.')[0], adjust='qfq')
        dfq['date'] = pd.to_datetime(dfq['date'])
        m = df[['date', 'close']].merge(dfq[['date', 'close']], on='date',
                                        suffixes=('_hfq', '_qfq'))
        if not m.empty:
            ratio = m['close_hfq'] / m['close_qfq']
            r['ratio_mean'] = float(ratio.mean())
            r['ratio_std'] = float(ratio.std())
            r['ratio_n'] = len(m)
    except Exception as e:
        r['ok'] = False
        r['sec'] = round(time.time() - t0, 2)
        r['err'] = f'{type(e).__name__}: {str(e)[:100]}'
    return r


def main() -> None:
    print('=' * 78)
    print('akshare hfq 可用性 / 耗时 / hfq-qfq 一致性')
    print('=' * 78)
    rows = []
    for sym, pre, label in SYMS:
        r = one(sym, pre, label)
        rows.append(r)
        if r['ok']:
            print(f"  {sym}  {label:<24s} ✅ {r['rows']:>5d}行 "
                  f"{r['d0']}~{r['d1']}  {r['sec']}s  "
                  f"首={r['first_close']:.2f} 末={r['last_close']:.2f}  "
                  f"hfq/qfq={r.get('ratio_mean', 0):.4f}±{r.get('ratio_std', 0):.6f}")
        else:
            print(f"  {sym}  {label:<24s} ❌ {r.get('err')}  ({r['sec']}s)")

    ok = [r for r in rows if r['ok']]
    print()
    if ok:
        avg = sum(r['sec'] for r in ok) / len(ok)
        # 上面每只调了两次（hfq+qfq），单次实际约一半
        per = avg / 2
        print(f'  平均单只(含 hfq+qfq 两次调用): {avg:.2f}s → 单次约 {per:.2f}s')
        print(f'  全市场 5555 只串行外推: {5555 * per / 60:.0f} 分钟 '
              f'({5555 * per / 3600:.1f} 小时)')
        print(f'  若 8 路并发            : {5555 * per / 60 / 8:.0f} 分钟')
        print()
        rs = [r for r in ok if 'ratio_std' in r]
        if rs:
            mx = max(r['ratio_std'] for r in rs)
            print(f'  hfq/qfq 比值 std 最大 {mx:.8f} (期望≈0，即两者只差一个常数)')


if __name__ == '__main__':
    main()
