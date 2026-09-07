"""R1b/R1c 诊断：拿到两个复权因子端点的真实报错。

不猜原因，直接打真实请求，把 status / body 打出来。
"""
import sys
import traceback

sys.path.insert(0, '.')

from app.core.config import settings

SYMS = ['600519.SH', '000001.SZ', '002594.SZ', '920808.BJ']


def probe_alphafeed() -> None:
    print('=' * 70)
    print('[1] AlphaFeed ex-factors')
    key = getattr(settings, 'ALPHAFEED_KEY', None)
    base = getattr(settings, 'ALPHAFEED_BASE_URL', 'https://api.alphafeed.org')
    print(f'  base   = {base}')
    print(f'  key    = {"已配置 len=%d" % len(key) if key else "❌ 缺失"}')

    import httpx
    from app.ingestion.alphafeed_source import AlphafeedSource

    src = AlphafeedSource()
    headers = {'X-API-Key': key}
    url = f"{base.rstrip('/')}/v1/klines/ex-factors"

    # 先照抄现有实现（symbols= 参数），看真实返回
    params = {
        'symbols': '600519.SH',
        'start_time': AlphafeedSource._to_ms('1990-01-01'),
        'end_time': AlphafeedSource._to_ms('2026-09-06', end_of_day=True),
    }
    print(f'  POST/GET {url}')
    print(f'  params = {params}')
    try:
        with httpx.Client(timeout=30.0) as c:
            r = c.get(url, params=params, headers=headers)
        print(f'  → status = {r.status_code}')
        print(f'  → body   = {r.text[:600]}')
    except Exception as e:
        print(f'  → 异常 {type(e).__name__}: {e}')

    # 逐个符号走 _get_ex_factors，看是否全部降级
    print()
    print('  --- 经 _get_ex_factors ---')
    for s in SYMS:
        try:
            ex, fl, ff = src._get_ex_factors(base, headers, s, '2026-09-06')
            n = 0 if ex is None else len(ex)
            print(f'  {s}: series={n} 行  f_latest={fl}  f_first={ff}  '
                  f'{"✅" if ex is not None else "❌ 降级"}')
        except Exception as e:
            print(f'  {s}: 异常 {type(e).__name__}: {str(e)[:120]}')


def probe_tushare() -> None:
    print()
    print('=' * 70)
    print('[2] Tushare adj_factor')
    tok = getattr(settings, 'TUSHARE_TOKEN', None)
    print(f'  token  = {"已配置 len=%d" % len(tok) if tok else "❌ 缺失"}')
    if not tok:
        return
    try:
        import tushare as ts
    except ImportError:
        print('  ❌ 未安装 tushare')
        return

    ts.set_token(tok)
    for s in ['600519.SH', '000001.SZ']:
        try:
            adj = ts.pro_api().query(
                'adj_factor', ts_code=s, start_date='19900101', end_date='20260906')
            if adj is None or adj.empty:
                print(f'  {s}: 返回空')
            else:
                print(f'  {s}: ✅ {len(adj)} 行  '
                      f'max={adj["adj_factor"].max()}  min={adj["adj_factor"].min()}')
        except Exception as e:
            print(f'  {s}: ❌ {type(e).__name__}: {str(e)[:300]}')

    # 顺带看 pro_bar 是否可用（R1c 主路径）
    print()
    print('  --- pro_bar(adj=None) ---')
    for s in ['600519.SH']:
        try:
            df = ts.pro_bar(ts_code=s, start_date='20260901', end_date='20260906',
                            freq='D', adj=None)
            print(f'  {s}: {"✅ %d 行" % len(df) if df is not None and not df.empty else "空/None"}')
        except Exception as e:
            print(f'  {s}: ❌ {type(e).__name__}: {str(e)[:300]}')


def probe_pandadata_ex() -> None:
    """pandadata 是否有复权因子接口 —— 决定 R1b 能否批量补齐全历史。"""
    print()
    print('=' * 70)
    print('[3] pandadata 复权因子可用性（决定 R1b 批量补数方案）')
    try:
        from app.ingestion.pandadata_source import PandadataSource
        src = PandadataSource()
    except Exception as e:
        print(f'  初始化失败: {type(e).__name__}: {str(e)[:200]}')
        return
    pub = [m for m in dir(src) if not m.startswith('__')]
    print(f'  公开方法: {pub}')
    client = None
    for attr in ('client', '_client', 'sdk', '_sdk'):
        if hasattr(src, attr):
            client = getattr(src, attr)
            print(f'  {attr} = {type(client).__name__}')
            break
    if client is not None:
        cands = [m for m in dir(client)
                 if not m.startswith('_') and ('adj' in m.lower()
                                               or 'factor' in m.lower()
                                               or 'split' in m.lower()
                                               or 'divid' in m.lower())]
        print(f'  client 上疑似复权相关方法: {cands}')


def main() -> None:
    probe_alphafeed()
    probe_tushare()
    probe_pandadata_ex()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
