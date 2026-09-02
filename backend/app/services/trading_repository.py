"""交易域仓储层：账户 / 持仓 / 订单 / 成交的落库与查询（异步）

沿用项目既有建表约定：`Base.metadata.create_all(tables=[...])` + 进程内锁，幂等执行。
"""
import asyncio
import json
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, engine
from app.models.trading import (
    MODE_PAPER,
    ORDER_FILLED,
    Instrument,
    PortfolioDailyValue,
    TradingAccount,
    TradingOrder,
    TradingPosition,
    TradingRebalanceRecord,
    TradingTrade,
)

_INIT_LOCK: asyncio.Lock | None = None
_ENSURED = False


async def ensure_trading_tables() -> None:
    """幂等建表（与 watchlist_service / cleaner_gateway 同一约定）

    建表只真正执行一次，后续调用直接返回，避免每个请求都跑一次 DDL。
    """
    global _INIT_LOCK, _ENSURED
    if _ENSURED:
        return
    if _INIT_LOCK is None:
        _INIT_LOCK = asyncio.Lock()
    async with _INIT_LOCK:
        if _ENSURED:
            return
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[
                    TradingAccount.__table__,
                    TradingPosition.__table__,
                    TradingOrder.__table__,
                    TradingTrade.__table__,
                    TradingRebalanceRecord.__table__,
                    PortfolioDailyValue.__table__,
                    Instrument.__table__,
                ]
            )
            # 标的主数据种子：对齐模拟撮合服务内置的 5 只演示标的，
            # 避免概览页首次加载时这些代码仍无名（其余代码在查询时懒回填）。
            await _seed_instruments(conn)
        # 存量库迁移（幂等）：账户按 (mode, strategy_id) 唯一，
        # 持仓 / 成交补 strategy_id 冗余列、持仓补 prev_close。
        #
        # 注意：每条 DDL 必须在**独立事务**中执行。PostgreSQL 一旦某条语句报错，
        # 整个事务即被标记为中止，同事务内后续语句会全部静默失败（try/except 也兜不住）。
        # 若 UNIQUE 约束因重复行创建失败，会连累其后本应成功的 prev_close 添加一起失败，
        # 导致模型 SELECT 引用 prev_close 时报「字段不存在」。故逐条独立建连接执行。
        for ddl in (
            "ALTER TABLE trading_accounts DROP CONSTRAINT IF EXISTS uq_trading_account_mode_broker",
            "ALTER TABLE trading_accounts ADD COLUMN IF NOT EXISTS strategy_id INTEGER",
            "ALTER TABLE trading_positions ADD COLUMN IF NOT EXISTS strategy_id INTEGER",
            "ALTER TABLE trading_positions ADD COLUMN IF NOT EXISTS prev_close DOUBLE PRECISION",
            "ALTER TABLE trading_trades ADD COLUMN IF NOT EXISTS strategy_id INTEGER",
            "ALTER TABLE trading_accounts ADD CONSTRAINT uq_trading_account_mode_strategy UNIQUE (mode, strategy_id)",
        ):
            try:
                async with engine.begin() as c:
                    await c.execute(text(ddl))
            except Exception:  # noqa: BLE001  已是最新结构 / 重复行等，忽略
                pass
        _ENSURED = True


# ---------------------------- 标的主数据（instruments） ----------------------------

def _exchange_of(symbol: str) -> str | None:
    """由代码后缀推导交易所：.SH→SH / .SZ→SZ / .BJ→BJ。"""
    if symbol.endswith(".SH"):
        return "SH"
    if symbol.endswith(".SZ"):
        return "SZ"
    if symbol.endswith(".BJ"):
        return "BJ"
    return None


# 模拟撮合服务内置的 5 只演示标的（与 huatai_trading.get_available_symbols 对齐）
_SEED_INSTRUMENTS = [
    {"symbol": "600519.SH", "name": "贵州茅台", "exchange": "SH"},
    {"symbol": "000001.SH", "name": "上证指数", "exchange": "SH"},
    {"symbol": "300750.SZ", "name": "宁德时代", "exchange": "SZ"},
    {"symbol": "600036.SH", "name": "招商银行", "exchange": "SH"},
    {"symbol": "000651.SZ", "name": "格力电器", "exchange": "SZ"},
]


async def _seed_instruments(conn) -> None:
    """幂等写入演示标的种子（已存在则跳过）。"""
    from datetime import datetime

    for it in _SEED_INSTRUMENTS:
        await conn.execute(
            text(
                """
                INSERT INTO instruments (symbol, name, exchange, updated_at)
                VALUES (:symbol, :name, :exchange, :ts)
                ON CONFLICT (symbol) DO NOTHING
                """
            ),
            {
                "symbol": it["symbol"],
                "name": it["name"],
                "exchange": it["exchange"],
                "ts": datetime.now(),
            },
        )


async def upsert_instruments(session: AsyncSession, rows: list[dict]) -> None:
    """批量 upsert 标的主数据。rows: [{symbol, name, exchange?, industry?}]。

    缺失的 exchange 按代码后缀推导；已存在的代码更新 name/industry。
    """
    if not rows:
        return
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        await session.execute(
            text(
                """
                INSERT INTO instruments (symbol, name, exchange, industry, updated_at)
                VALUES (:symbol, :name, :exchange, :industry, now())
                ON CONFLICT (symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    exchange = COALESCE(instruments.exchange, EXCLUDED.exchange),
                    industry = COALESCE(instruments.industry, EXCLUDED.industry),
                    updated_at = now()
                """
            ),
            {
                "symbol": sym,
                "name": r.get("name") or sym,
                "exchange": r.get("exchange") or _exchange_of(sym),
                "industry": r.get("industry"),
            },
        )


async def get_instruments(
    session: AsyncSession, symbols: list[str] | None = None
) -> dict[str, dict]:
    """查标的主数据。symbols 为空返回全量。返回 {symbol: {name, exchange, industry}}。"""
    stmt = select(Instrument)
    if symbols:
        stmt = stmt.where(Instrument.symbol.in_(symbols))
    rows = (await session.execute(stmt)).scalars().all()
    return {
        r.symbol: {
            "symbol": r.symbol,
            "name": r.name or r.symbol,
            "exchange": r.exchange,
            "industry": r.industry,
        }
        for r in rows
    }


# ---------------------------- 账户 ----------------------------

async def get_or_create_account(
    session: AsyncSession,
    *,
    mode: str,
    broker: str,
    initial_capital: float,
    user_id: int | None = None,
    account_id: str | None = None,
    strategy_id: int | None = None,
) -> TradingAccount:
    """按 (mode, strategy_id) 取账户（每策略=一个独立资金池）；无 strategy_id 时取模式共享账户。

    不存在则按初始资金创建；账户号由撮合器返回后回填。
    """
    stmt = select(TradingAccount).where(TradingAccount.mode == mode)
    if strategy_id is not None:
        stmt = stmt.where(TradingAccount.strategy_id == strategy_id)
    else:
        stmt = stmt.where(TradingAccount.strategy_id.is_(None))
    acct = (await session.execute(stmt)).scalars().first()
    if acct:
        return acct
    acct = TradingAccount(
        user_id=user_id,
        strategy_id=strategy_id,
        mode=mode,
        broker=broker,
        account_id=account_id or f"{broker.upper()}_{mode.upper()}_{strategy_id or 'SHARED'}",
        initial_capital=initial_capital,
        cash_balance=initial_capital,
        frozen_cash=0.0,
    )
    session.add(acct)
    await session.flush()
    return acct


async def update_account_balances(
    session: AsyncSession, account: TradingAccount, *, cash: float, frozen: float = 0.0
) -> None:
    account.cash_balance = cash
    account.frozen_cash = frozen
    await session.flush()


# ---------------------------- 持仓 ----------------------------

async def list_positions(
    session: AsyncSession,
    mode: str,
    account_id: int | None = None,
    strategy_id: int | None = None,
) -> list[TradingPosition]:
    """按模式列出持仓；可进一步限定账户或策略。

    说明：原先只按 mode 过滤，同 mode 多账户（如多个模拟盘）时会互相干扰，
    尤其在 sync_state 的"删除内存不存在的持仓"环节可能误删他账户的行。
    传入 account_id / strategy_id 可消除该风险。
    """
    stmt = select(TradingPosition).where(TradingPosition.mode == mode)
    if account_id is not None:
        stmt = stmt.where(TradingPosition.account_id == account_id)
    if strategy_id is not None:
        stmt = stmt.where(TradingPosition.strategy_id == strategy_id)
    stmt = stmt.order_by(TradingPosition.market_value.desc())
    return list((await session.execute(stmt)).scalars().all())


async def upsert_position(
    session: AsyncSession,
    *,
    account_id: int,
    mode: str,
    symbol: str,
    side: str,
    quantity: int,
    avg_price: float,
    last_price: float,
    strategy_id: int | None = None,
) -> None:
    """写入/更新持仓；quantity<=0 视为清仓，删除该行。"""
    stmt = select(TradingPosition).where(
        TradingPosition.account_id == account_id,
        TradingPosition.symbol == symbol,
        TradingPosition.side == side,
    )
    pos = (await session.execute(stmt)).scalars().first()

    if quantity <= 0:
        if pos:
            await session.delete(pos)
            await session.flush()
        return

    market_value = quantity * last_price
    unrealized = (last_price - avg_price) * quantity
    if pos is None:
        session.add(
            TradingPosition(
                account_id=account_id,
                strategy_id=strategy_id,
                mode=mode,
                symbol=symbol,
                side=side,
                quantity=quantity,
                avg_price=avg_price,
                last_price=last_price,
                market_value=market_value,
                unrealized_pnl=unrealized,
            )
        )
    else:
        pos.quantity = quantity
        pos.avg_price = avg_price
        pos.last_price = last_price
        pos.market_value = market_value
        pos.unrealized_pnl = unrealized
    await session.flush()


# ---------------------------- 订单 ----------------------------

async def create_order(
    session: AsyncSession,
    *,
    account_id: int,
    mode: str,
    client_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: int,
    price: float | None,
    source: str = "manual",
    strategy_id: int | None = None,
) -> TradingOrder:
    order = TradingOrder(
        account_id=account_id,
        mode=mode,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        source=source,
        strategy_id=strategy_id,
    )
    session.add(order)
    await session.flush()
    return order


async def get_order(session: AsyncSession, client_order_id: str) -> TradingOrder | None:
    stmt = select(TradingOrder).where(TradingOrder.client_order_id == client_order_id)
    return (await session.execute(stmt)).scalars().first()


async def update_order(
    session: AsyncSession,
    order: TradingOrder,
    *,
    status: str | None = None,
    filled_quantity: int | None = None,
    broker_order_id: str | None = None,
    message: str | None = None,
) -> TradingOrder:
    if status is not None:
        order.status = status
        if status == ORDER_FILLED:
            order.filled_at = datetime.now()
    if filled_quantity is not None:
        order.filled_quantity = filled_quantity
    if broker_order_id is not None:
        order.broker_order_id = broker_order_id
    if message is not None:
        order.message = message
    await session.flush()
    return order


async def list_orders(
    session: AsyncSession,
    mode: str,
    status: str | None = None,
    limit: int = 50,
) -> list[TradingOrder]:
    stmt = select(TradingOrder).where(TradingOrder.mode == mode)
    if status:
        stmt = stmt.where(TradingOrder.status == status.upper())
    stmt = stmt.order_by(TradingOrder.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------- 成交 ----------------------------

async def add_trade(
    session: AsyncSession,
    *,
    order_id: int,
    account_id: int,
    mode: str,
    symbol: str,
    side: str,
    price: float,
    quantity: int,
    commission: float = 0.0,
    strategy_id: int | None = None,
) -> TradingTrade:
    trade = TradingTrade(
        order_id=order_id,
        account_id=account_id,
        strategy_id=strategy_id,
        mode=mode,
        symbol=symbol,
        side=side,
        price=price,
        quantity=quantity,
        amount=price * quantity,
        commission=commission,
    )
    session.add(trade)
    await session.flush()
    return trade


async def list_trades(
    session: AsyncSession,
    mode: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 200,
) -> list[TradingTrade]:
    stmt = select(TradingTrade).where(TradingTrade.mode == mode)
    if start:
        stmt = stmt.where(TradingTrade.trade_time >= _parse_date(start))
    if end:
        stmt = stmt.where(TradingTrade.trade_time <= _parse_date(end, end_of_day=True))
    stmt = stmt.order_by(TradingTrade.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def _parse_date(value: str, end_of_day: bool = False) -> datetime:
    d = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59)
    return d


# ---------------------------- 调仓记录 ----------------------------

def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


async def upsert_rebalance(
    session: AsyncSession,
    *,
    strategy_id: int,
    rebalance_date,
    mode: str = MODE_PAPER,
    strategy_name: str | None = None,
    trade_date=None,
    target_count: int | None = None,
    orders_placed: int | None = None,
    amount: float | None = None,
    status: str = "success",
    detail: dict | None = None,
) -> TradingRebalanceRecord:
    """按 (strategy_id, rebalance_date, mode) 幂等写入调仓记录。"""
    rd = _as_date(rebalance_date)
    stmt = select(TradingRebalanceRecord).where(
        TradingRebalanceRecord.strategy_id == strategy_id,
        TradingRebalanceRecord.rebalance_date == rd,
        TradingRebalanceRecord.mode == mode,
    )
    rec = (await session.execute(stmt)).scalars().first()
    if rec is None:
        rec = TradingRebalanceRecord(
            strategy_id=strategy_id, rebalance_date=rd, mode=mode
        )
        session.add(rec)
    rec.strategy_name = strategy_name
    rec.trade_date = _as_date(trade_date)
    rec.target_count = target_count
    rec.orders_placed = orders_placed
    rec.amount = amount
    rec.status = status
    rec.detail = json.dumps(detail, ensure_ascii=False) if detail is not None else None
    await session.flush()
    return rec


async def get_rebalance(
    session: AsyncSession, *, strategy_id: int, rebalance_date, mode: str = MODE_PAPER
) -> TradingRebalanceRecord | None:
    stmt = select(TradingRebalanceRecord).where(
        TradingRebalanceRecord.strategy_id == strategy_id,
        TradingRebalanceRecord.rebalance_date == _as_date(rebalance_date),
        TradingRebalanceRecord.mode == mode,
    )
    return (await session.execute(stmt)).scalars().first()


async def list_rebalances(
    session: AsyncSession, mode: str | None = None, limit: int = 20
) -> list[TradingRebalanceRecord]:
    stmt = select(TradingRebalanceRecord)
    if mode:
        stmt = stmt.where(TradingRebalanceRecord.mode == mode)
    stmt = (
        stmt.order_by(
            TradingRebalanceRecord.rebalance_date.desc(),
            TradingRebalanceRecord.id.desc(),
        )
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "ensure_trading_tables",
    "upsert_instruments",
    "get_instruments",
    "get_or_create_account",
    "update_account_balances",
    "list_positions",
    "upsert_position",
    "create_order",
    "get_order",
    "update_order",
    "list_orders",
    "add_trade",
    "list_trades",
    "upsert_rebalance",
    "get_rebalance",
    "list_rebalances",
]
