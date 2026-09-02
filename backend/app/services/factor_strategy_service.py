"""因子选股策略：backend 侧读写服务（职责归位版）

边界（docs/memo/2026-09-02.md §四/§五）：
- 策略配置、回测结果、执行记录 均存 backend Postgres；
- 因子计算（scores）、回测运行的计算、撮合/发信号、行情 由 dc 完成；
- 仅更新市值才经 market_proxy 去 dc 取行情。

故本服务的「读」路径（策略列表/详情、回测历史/详情、executions）一律读
backend 库，不再代理到 dc；写入策略 / 落回测结果也走 backend 库。
只有 scores(纯计算)、run_backtest(调 dc 算)、rebalance(调 dc 撮合)、
industries/refresh(行业数据在 dc) 才调用 factor_strategy_proxy。
"""
import asyncio
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, engine
from app.models.quant import BacktestResult, Strategy
from app.models.trading import TradingRebalanceRecord
from app.services import factor_strategy_proxy as proxy


# --------------------------------------------------------------------------- #
# 幂等补列（additive DDL，与 cleaner_gateway 同一约定）
# --------------------------------------------------------------------------- #
_INIT_LOCK: asyncio.Lock | None = None
_ENSURED = False

_DDL_MIGRATIONS = (
    "ALTER TABLE backtest_results "
    "ADD COLUMN IF NOT EXISTS nav JSON, "
    "ADD COLUMN IF NOT EXISTS rebalances JSON, "
    "ADD COLUMN IF NOT EXISTS warnings JSON",
    "ALTER TABLE strategies ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE",
)


async def ensure_quant_tables() -> None:
    """幂等补列：只在首次调用时执行一次。"""
    global _INIT_LOCK, _ENSURED
    if _ENSURED:
        return
    if _INIT_LOCK is None:
        _INIT_LOCK = asyncio.Lock()
    async with _INIT_LOCK:
        if _ENSURED:
            return
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[Strategy.__table__, BacktestResult.__table__])
            for ddl in _DDL_MIGRATIONS:
                await conn.execute(text(ddl))
        _ENSURED = True


# --------------------------------------------------------------------------- #
# 映射
# --------------------------------------------------------------------------- #
def _parse_config(code: str | dict) -> dict:
    if isinstance(code, dict):
        return code
    if isinstance(code, str):
        try:
            return json.loads(code)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _strategy_to_dict(r: Strategy) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "config": _parse_config(r.code),
        "is_active": bool(r.is_active),
        "owner": str(r.user_id) if r.user_id is not None else None,
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
    }


def _metrics_from_row(r: BacktestResult) -> dict:
    nav = r.nav or []
    rebalances = r.rebalances or []
    return {
        "totalReturn": r.total_return,
        "annualReturn": None,
        "sharpe": r.sharpe_ratio,
        "maxDrawdown": r.max_drawdown,
        "winRate": r.win_rate,
        "turnover": None,
        "finalCapital": None,
        "days": len(nav),
        "rebalances": len(rebalances),
    }


def _backtest_history(r: BacktestResult) -> dict:
    return {
        "id": r.id,
        "strategy_id": r.strategy_id,
        "start_date": _iso(r.start_date),
        "end_date": _iso(r.end_date),
        "metrics": _metrics_from_row(r),
        "created_at": _iso(r.created_at),
    }


def _backtest_detail(r: BacktestResult) -> dict:
    return {
        "id": r.id,
        "strategy_id": r.strategy_id,
        "start_date": _iso(r.start_date),
        "end_date": _iso(r.end_date),
        "metrics": _metrics_from_row(r),
        "nav": r.nav or [],
        "rebalances": r.rebalances or [],
        "warnings": r.warnings or [],
        "created_at": _iso(r.created_at),
    }


def _rebalance_to_execution(r: TradingRebalanceRecord) -> dict:
    detail = r.detail
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:  # noqa: BLE001
            detail = {"raw": detail}
    return {
        "id": r.id,
        "strategy_id": r.strategy_id,
        "rebalance_date": _iso(r.rebalance_date),
        "trade_date": _iso(r.trade_date),
        "target_count": r.target_count,
        "orders_placed": r.orders_placed,
        "amount": r.amount,
        "status": r.status,
        "detail": detail,
        "created_at": _iso(r.created_at),
    }


# --------------------------------------------------------------------------- #
# 策略 CRUD（backend 库）
# --------------------------------------------------------------------------- #
async def list_strategies(db: AsyncSession, active_only: bool = False) -> list[dict]:
    await ensure_quant_tables()
    stmt = select(Strategy)
    if active_only:
        stmt = stmt.where(Strategy.is_active.is_(True))
    rows = (await db.execute(stmt.order_by(Strategy.id))).scalars().all()
    return [_strategy_to_dict(r) for r in rows]


async def get_strategy(db: AsyncSession, sid: int) -> dict | None:
    await ensure_quant_tables()
    r = (await db.execute(select(Strategy).where(Strategy.id == sid))).scalar_one_or_none()
    return _strategy_to_dict(r) if r else None


async def create_strategy(
    db: AsyncSession, *, name: str, description: str | None, config: dict, is_active: bool, user_id: int
) -> dict:
    await ensure_quant_tables()
    r = Strategy(
        name=name,
        description=description,
        code=json.dumps(config, ensure_ascii=False),
        user_id=user_id,
        is_active=bool(is_active),
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _strategy_to_dict(r)


async def update_strategy(
    db: AsyncSession, sid: int, *, name=None, description=None, config=None, is_active=None
) -> dict | None:
    await ensure_quant_tables()
    r = (await db.execute(select(Strategy).where(Strategy.id == sid))).scalar_one_or_none()
    if r is None:
        return None
    if name is not None:
        r.name = name
    if description is not None:
        r.description = description
    if config is not None:
        r.code = json.dumps(config, ensure_ascii=False)
    if is_active is not None:
        r.is_active = bool(is_active)
    await db.commit()
    await db.refresh(r)
    return _strategy_to_dict(r)


async def delete_strategy(db: AsyncSession, sid: int) -> bool:
    await ensure_quant_tables()
    r = (await db.execute(select(Strategy).where(Strategy.id == sid))).scalar_one_or_none()
    if r is None:
        return False
    await db.delete(r)
    await db.commit()
    return True


# --------------------------------------------------------------------------- #
# 回测（读 backend；运行调 dc 算、结果回写 backend）
# --------------------------------------------------------------------------- #
async def list_backtests(db: AsyncSession, sid: int) -> list[dict]:
    await ensure_quant_tables()
    rows = (
        await db.execute(
            select(BacktestResult)
            .where(BacktestResult.strategy_id == sid)
            .order_by(BacktestResult.created_at.desc())
        )
    ).scalars().all()
    return [_backtest_history(r) for r in rows]


async def get_backtest(db: AsyncSession, sid: int, bid: int) -> dict | None:
    await ensure_quant_tables()
    r = (
        await db.execute(
            select(BacktestResult).where(BacktestResult.id == bid, BacktestResult.strategy_id == sid)
        )
    ).scalar_one_or_none()
    return _backtest_detail(r) if r else None


def _derive_dates(payload: dict) -> tuple[Any, Any]:
    """从 dc 计算结果推导 start/end：优先 rebalances 首尾 date，其次 nav 首尾。"""
    rebalances = payload.get("rebalances") or []
    nav = payload.get("nav") or []
    if rebalances:
        return rebalances[0].get("date"), rebalances[-1].get("date")
    if nav:
        return nav[0].get("date"), nav[-1].get("date")
    return None, None


async def run_backtest(
    db: AsyncSession, sid: int, start: str | None = None, end: str | None = None
) -> dict:
    """调 dc 计算回测（因子引擎在 dc），结果回写 backend backtest_results。"""
    await ensure_quant_tables()
    dc = await proxy.backtest(db, sid, start=start, end=end)
    if not isinstance(dc, dict):
        return dc  # 透传错误
    metrics = dc.get("metrics") or {}
    start_d, end_d = _derive_dates(dc)
    # 显式 start/end 优先（前端传入）
    if start:
        start_d = start
    if end:
        end_d = end
    row = BacktestResult(
        strategy_id=sid,
        start_date=_as_dt(start_d) if start_d else datetime.now(),
        end_date=_as_dt(end_d) if end_d else datetime.now(),
        total_return=metrics.get("totalReturn"),
        sharpe_ratio=metrics.get("sharpe"),
        max_drawdown=metrics.get("maxDrawdown"),
        win_rate=metrics.get("winRate"),
        trades_count=metrics.get("rebalances"),
        nav=dc.get("nav"),
        rebalances=dc.get("rebalances"),
        warnings=dc.get("warnings"),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _backtest_detail(row)


def _as_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 执行记录（读 backend trading_rebalance_records）
# --------------------------------------------------------------------------- #
async def list_executions(db: AsyncSession, sid: int, limit: int = 50) -> list[dict]:
    await ensure_quant_tables()
    rows = (
        await db.execute(
            select(TradingRebalanceRecord)
            .where(TradingRebalanceRecord.strategy_id == sid)
            .order_by(TradingRebalanceRecord.rebalance_date.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [_rebalance_to_execution(r) for r in rows]
