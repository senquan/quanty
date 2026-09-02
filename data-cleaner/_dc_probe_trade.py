import sys, warnings, json
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
from app.core.config import settings
from sqlalchemy import create_engine, text

e = create_engine(settings.DATABASE_URL.replace('+asyncpg', '+psycopg2'))
with e.connect() as c:
    print('=== 策略列表(确认"测试因子策略") ===')
    for r in c.execute(text(
            'SELECT id, name, is_active FROM factor.factor_strategies ORDER BY id')).fetchall():
        print(f'  id={r[0]} name={r[1]!r} active={r[2]}')
    for r in c.execute(text(
            'SELECT id, name FROM public.strategies ORDER BY id')).fetchall():
        print(f'  [public] id={r[0]} name={r[1]!r}')

    print('\n=== trading_trades 列与约束 ===')
    for cn, dt, nl in c.execute(text(
            'SELECT column_name, data_type, is_nullable FROM information_schema.columns '
            "WHERE table_schema='public' AND table_name='trading_trades' "
            'ORDER BY ordinal_position')).fetchall():
        print(f'  {cn} ({dt}, null={nl})')

    print('\n=== trading_orders 列与约束 ===')
    for cn, dt, nl in c.execute(text(
            'SELECT column_name, data_type, is_nullable FROM information_schema.columns '
            "WHERE table_schema='public' AND table_name='trading_orders' "
            'ORDER BY ordinal_position')).fetchall():
        print(f'  {cn} ({dt}, null={nl})')

    print('\n=== 600036 在 raw_bars 的价格(核对 40.10 是否合理) ===')
    for r in c.execute(text(
            "SELECT timestamp::date, open, high, low, close FROM factor.raw_bars "
            "WHERE symbol='600036.SH' AND timestamp::date IN "
            "('2026-08-27','2026-08-28','2026-08-31','2026-09-01') "
            'ORDER BY timestamp')).fetchall():
        print(f'  {r[0]}: open={r[1]} high={r[2]} low={r[3]} close={r[4]}')

    print('\n=== 08-31 调仓记录 ===')
    for r in c.execute(text(
            'SELECT id, strategy_id, rebalance_date, status, detail '
            'FROM public.trading_rebalance_records WHERE rebalance_date=:d'),
            {'d': '2026-08-31'}).fetchall():
        print(f'  id={r[0]} sid={r[1]} date={r[2]} status={r[3]} detail={r[4]}')

    print('\n=== paper 账户 ===')
    for r in c.execute(text(
            'SELECT id, mode, cash_balance, initial_capital, frozen_cash '
            "FROM public.trading_accounts WHERE mode='paper'")).fetchall():
        print(f'  id={r[0]} mode={r[1]} cash={r[2]} 初始={r[3]} 冻结={r[4]}')
