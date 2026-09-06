"""一次性迁移：trading_accounts → portfolios，并改挂外键列。

项目不使用 alembic，迁移走幂等的独立 DDL（每条独立事务，参见
trading_repository.ensure_trading_tables 的注释）。本模块被
ensure_trading_tables 与 scripts/migrate_accounts_to_portfolios.py 共用，
保证旧库首次启动即自愈，也可显式运行。

要点（对应 A4：删 trading_accounts、由 portfolios 完全承接、保障数据完整）：
- 复用 trading_accounts 的 id 写入 portfolios，故持仓/订单/成交中改名为
  portfolio_id 的整型值仍指向同一行，外键数据零丢失；
- 调仓记录 / 每日估值按 (strategy_id, mode) 回填 portfolio_id；
- 最后 DROP 旧表。
"""
import logging

from sqlalchemy import text

from app.core.database import Base, engine
from app.models.trading import Portfolio

logger = logging.getLogger(__name__)

_ACCOUNT_TABLES = ["trading_positions", "trading_orders", "trading_trades"]


async def migrate_accounts_to_portfolios() -> None:
    """把旧「以策略牵头的模拟盘」账户迁移为投资组合。幂等，可重复运行。"""
    # 1) 确保 portfolios 表存在
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Portfolio.__table__])

    # 2) 已迁移（旧表不存在）则直接返回
    if not await _table_exists("trading_accounts"):
        logger.info("迁移跳过：trading_accounts 不存在（已是最新结构）")
        return

    logger.info("检测到旧 trading_accounts，开始迁移为 portfolios ...")

    # 兼容历史账户：旧 trading_accounts 允许 strategy_id 为空（未绑策略的独立账户）。
    # 对「已存在」的 portfolios 表去掉 NOT NULL（新装库由 create_all 直接建为可空）；
    # 本步幂等：列已可空或表不存在时均不报错。
    await _run(
        "ALTER TABLE portfolios ALTER COLUMN strategy_id DROP NOT NULL",
        ignore=True,
    )

    # 3) 数据迁移：复用 id，组合名优先取策略名
    await _run(
        """
        INSERT INTO portfolios
            (id, name, strategy_id, mode, broker, owner_user_id,
             initial_capital, cash_balance, frozen_cash, account_id,
             auto_rebalance, is_active, created_at, updated_at)
        SELECT a.id,
               COALESCE(s.name, '策略#' || a.strategy_id::text, '组合#' || a.id::text),
               a.strategy_id, a.mode, a.broker, a.user_id,
               a.initial_capital, a.cash_balance, a.frozen_cash, a.account_id,
               TRUE, TRUE, a.created_at, a.updated_at
        FROM trading_accounts a
        LEFT JOIN strategies s ON s.id = a.strategy_id
        ON CONFLICT (id) DO NOTHING
        """
    )

    # 4) 持仓/订单/成交：account_id → portfolio_id（整型值不变）
    for t in _ACCOUNT_TABLES:
        await _run(
            f"ALTER TABLE {t} RENAME COLUMN account_id TO portfolio_id", ignore=True
        )

    # 5) 调仓记录：加 portfolio_id 并按 (strategy_id, mode) 回填，再换唯一约束
    await _run(
        "ALTER TABLE trading_rebalance_records ADD COLUMN IF NOT EXISTS portfolio_id INTEGER",
        ignore=True,
    )
    await _run(
        """
        UPDATE trading_rebalance_records r
        SET portfolio_id = (
            SELECT p.id FROM portfolios p
            WHERE p.strategy_id = r.strategy_id AND p.mode = r.mode
            LIMIT 1
        )
        WHERE r.portfolio_id IS NULL
        """
    )
    await _run(
        "ALTER TABLE trading_rebalance_records "
        "DROP CONSTRAINT IF EXISTS uq_rebalance_strategy_date_mode",
        ignore=True,
    )
    await _run(
        "ALTER TABLE trading_rebalance_records "
        "ADD CONSTRAINT uq_rebalance_portfolio_date UNIQUE (portfolio_id, rebalance_date)",
        ignore=True,
    )

    # 6) 每日估值：加 portfolio_id 并按 (strategy_id, mode) 回填，再换唯一约束
    await _run(
        "ALTER TABLE portfolio_daily_values ADD COLUMN IF NOT EXISTS portfolio_id INTEGER",
        ignore=True,
    )
    await _run(
        """
        UPDATE portfolio_daily_values v
        SET portfolio_id = (
            SELECT p.id FROM portfolios p
            WHERE p.strategy_id = v.strategy_id AND p.mode = v.mode
            LIMIT 1
        )
        WHERE v.portfolio_id IS NULL
        """
    )
    await _run(
        "ALTER TABLE portfolio_daily_values "
        "DROP CONSTRAINT IF EXISTS uq_portfolio_value",
        ignore=True,
    )
    await _run(
        "ALTER TABLE portfolio_daily_values "
        "ADD CONSTRAINT uq_portfolio_value UNIQUE (portfolio_id, value_date)",
        ignore=True,
    )

    # 7) 删旧表（数据已由 portfolios 完全承接）
    await _run("DROP TABLE IF EXISTS trading_accounts")

    logger.info("trading_accounts → portfolios 迁移完成")


async def _table_exists(name: str) -> bool:
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :n"
            ),
            {"n": name},
        )
        return res.first() is not None


async def _run(sql: str, ignore: bool = False) -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql))
    except Exception as e:  # noqa: BLE001
        if ignore:
            logger.warning("迁移 DDL 跳过（已是最新结构）: %s | %s", sql[:60], e)
        else:
            raise
