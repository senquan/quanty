"""把 data-cleaner(factor schema) 的旧策略 / 回测数据迁移到 backend(public schema)。

背景
----
职责归位后，策略与调仓由 backend 编排落库。backend 侧：
- trading_rebalance_records：调仓执行记录（已迁移过，本脚本仅校验）
- strategies / backtest_results：仍为空，需从 DC 侧补

映射
----
factor.factor_strategies        -> public.strategies
    name/description -> 同名
    config(JSON)     -> code（backend 的 code 存策略定义，NOT NULL）
    owner            -> user_id（FK users.id，DC owner=1 <=> users.id=1）
    id 保留原值 14，以维持 trading_rebalance_records.strategy_id=14 的引用
    （迁移后 setval 序列，避免将来自增撞号）

factor.factor_strategy_backtests -> public.backtest_results
    metrics.totalReturn/sharpe/winRate/maxDrawdown/rebalances -> 对应列
    start_date / end_date：DC 侧为 NULL，用 rebalances[0].date / [-1].date 推导
    （backend 这两列 NOT NULL，必须补齐）

未迁移项
--------
backtest 的 nav 曲线与 rebalances[].holdings（117 期持仓快照）在 backend
backtest_results 中**没有对应列**，本脚本不写入。若需保留，需先扩展 backend
表结构（如加 nav JSONB / rebalances JSONB 列）。

另：DC 从未成功下单（唯一一条 execution 为 error、orders_placed=0），
模拟盘账户 cash 仍是初始 100 万，故 trading_positions 本就无真实持仓可迁。

用法：
    .venv\\Scripts\\python.exe migrate_dc_to_backend.py [--dry-run]
"""
import argparse
import json
import sys
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from sqlalchemy import create_engine, text

from app.core.config import settings

SRC = 'factor'
DST = 'public'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只打印将要写入的内容，不落库')
    args = ap.parse_args()

    eng = create_engine(settings.DATABASE_URL.replace('+asyncpg', '+psycopg2'))

    with eng.connect() as c:
        # ---------- 1) 校验调仓记录（预期已迁移） ----------
        print('=== 1) 调仓记录校验 ===')
        dc_exe = c.execute(text(
            'SELECT id, strategy_id, rebalance_date, status FROM '
            'factor.factor_strategy_executions ORDER BY id')).fetchall()
        be_rec = c.execute(text(
            'SELECT id, strategy_id, rebalance_date, mode, status FROM '
            'public.trading_rebalance_records ORDER BY id')).fetchall()
        print(f'  DC  execution  : {[tuple(r) for r in dc_exe]}')
        print(f'  BE  rebalance  : {[tuple(r) for r in be_rec]}')
        be_keys = {(r[1], str(r[2])) for r in be_rec}
        missing = [r for r in dc_exe if (r[1], str(r[2])) not in be_keys]
        print(f'  结论: {"调仓记录已全部存在，无需迁移" if not missing else f"缺 {len(missing)} 条需补"}')

        # ---------- 2) 策略 ----------
        print('\n=== 2) 策略迁移 ===')
        rows = c.execute(text(
            'SELECT id, name, description, config, owner, created_at, updated_at '
            'FROM factor.factor_strategies ORDER BY id')).fetchall()
        for sid, name, desc, config, owner, ca, ua in rows:
            print(f'  DC strategy id={sid} name={name!r} owner={owner}')
            code = json.dumps(config, ensure_ascii=False) if not isinstance(config, str) else config
            exists = c.execute(text('SELECT 1 FROM public.strategies WHERE id=:i'),
                               {'i': sid}).scalar()
            if exists:
                print(f'    -> public.strategies 已存在 id={sid}，跳过')
                continue
            if args.dry_run:
                print(f'    [dry-run] 将写入 id={sid}, code 长度={len(code)}')
                continue
            c.execute(text(
                'INSERT INTO public.strategies (id, name, description, code, user_id, '
                'created_at, updated_at) VALUES (:id,:n,:d,:c,:u,:ca,:ua) '
                'ON CONFLICT (id) DO NOTHING'),
                {'id': sid, 'n': name, 'd': desc, 'c': code, 'u': owner,
                 'ca': ca, 'ua': ua})
            print(f'    -> 已写入 public.strategies id={sid}')
        if not args.dry_run:
            # 序列对齐，避免将来自增撞号
            try:
                c.execute(text(
                    "SELECT setval(pg_get_serial_sequence('public.strategies','id'), "
                    'GREATEST((SELECT COALESCE(MAX(id),0) FROM public.strategies),1))'))
                print('  strategies id 序列已对齐')
            except Exception as e:  # noqa: BLE001
                print(f'  序列对齐跳过: {type(e).__name__}: {str(e)[:100]}')

        # ---------- 3) 回测 ----------
        print('\n=== 3) 回测迁移 ===')
        bt = c.execute(text(
            'SELECT id, strategy_id, start_date, end_date, metrics, rebalances, created_at '
            'FROM factor.factor_strategy_backtests ORDER BY id')).fetchall()
        for bid, sid, sd, ed, metrics, rebal, ca in bt:
            if isinstance(metrics, str):
                metrics = json.loads(metrics)
            metrics = metrics or {}
            if isinstance(rebal, str):
                rebal = json.loads(rebal)
            rebal = rebal or []
            if rebal:
                start = sd or datetime.strptime(rebal[0]['date'], '%Y-%m-%d').date()
                end = ed or datetime.strptime(rebal[-1]['date'], '%Y-%m-%d').date()
                trades = metrics.get('rebalances') or len(rebal)
            else:
                start, end, trades = sd, ed, metrics.get('rebalances')
            print(f'  DC backtest id={bid} strategy_id={sid} '
                  f'区间推导={start}~{end} trades={trades}')
            print(f'    metrics: totalReturn={metrics.get("totalReturn")} '
                  f'sharpe={metrics.get("sharpe")} winRate={metrics.get("winRate")} '
                  f'maxDrawdown={metrics.get("maxDrawdown")}')
            if start is None or end is None:
                print('    !! 无法推导 start/end，跳过（backend 该列 NOT NULL）')
                continue
            exists = c.execute(text(
                'SELECT 1 FROM public.backtest_results WHERE strategy_id=:s '
                'AND start_date=:sd'), {'s': sid, 'sd': start}).scalar()
            if exists:
                print('    -> 已存在同策略同起始日记录，跳过')
                continue
            if args.dry_run:
                print('    [dry-run] 将写入 backtest_results')
                continue
            c.execute(text(
                'INSERT INTO public.backtest_results (strategy_id, start_date, end_date, '
                'total_return, sharpe_ratio, max_drawdown, win_rate, trades_count, created_at) '
                'VALUES (:sid,:sd,:ed,:tr,:sh,:md,:wr,:tc,:ca)'),
                {'sid': sid, 'sd': start, 'ed': end,
                 'tr': metrics.get('totalReturn'), 'sh': metrics.get('sharpe'),
                 'md': metrics.get('maxDrawdown'), 'wr': metrics.get('winRate'),
                 'tc': trades, 'ca': ca})
            print('    -> 已写入 public.backtest_results')

        if not args.dry_run:
            c.commit()  # SQLAlchemy 2.0: Connection.commit()

    # ---------- 4) 结果校验 ----------
    with eng.connect() as c:
        print('\n=== 4) 迁移后校验 ===')
        for t in ('strategies', 'backtest_results', 'trading_rebalance_records',
                  'trading_positions'):
            n = c.execute(text(f'SELECT COUNT(*) FROM public.{t}')).scalar()
            print(f'  public.{t}: {n} 行')
        print('\n  strategies 内容:')
        for r in c.execute(text('SELECT id, name, user_id, length(code) FROM public.strategies')).fetchall():
            print(f'    id={r[0]} name={r[1]!r} user_id={r[2]} code长度={r[3]}')
        print('  backtest_results 内容:')
        for r in c.execute(text(
                'SELECT id, strategy_id, start_date, end_date, total_return, '
                'sharpe_ratio, max_drawdown, win_rate, trades_count '
                'FROM public.backtest_results')).fetchall():
            print(f'    id={r[0]} strategy_id={r[1]} {r[2]}~{r[3]} '
                  f'ret={r[4]} sharpe={r[5]} mdd={r[6]} win={r[7]} trades={r[8]}')

    print('\n完成。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
