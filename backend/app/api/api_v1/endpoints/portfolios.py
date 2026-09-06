"""投资组合 CRUD 与详情接口（取代原以策略牵头的模拟盘账户）。

组合为交易域牵头实体：绑定一个策略、独立资金池、独立持仓。
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.api_v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.trading import Portfolio, PortfolioDailyValue
from app.models.user import User
from app.schemas.response import Response
from app.services import trading_repository as repo
from app.services.rebalance_service import rebalance_one
from app.services.trading_coordinator import TradingCoordinator, ensure_tables

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ 请求模型 ============

class PortfolioCreate(BaseModel):
    """创建组合：命名 + 选策略 + 初始资金（独立资金池）"""

    name: str = Field(..., description="组合名称")
    strategy_id: int = Field(..., description="绑定的策略ID")
    mode: str = Field("paper", description="交易模式: paper / live")
    initial_capital: float = Field(..., gt=0, description="初始资金（独立资金池规模）")
    auto_rebalance: bool = Field(True, description="是否参与自动调仓（默认 paper=true）")
    is_active: bool = Field(True, description="是否激活")
    description: Optional[str] = Field(None, description="备注")


class PortfolioUpdate(BaseModel):
    name: Optional[str] = None
    strategy_id: Optional[int] = None
    auto_rebalance: Optional[bool] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


# ============ 序列化 ============

def _portfolio_dict(p: Portfolio) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "strategy_id": p.strategy_id,
        "mode": p.mode,
        "broker": p.broker,
        "owner_user_id": p.owner_user_id,
        "initial_capital": round(p.initial_capital, 2),
        "cash_balance": round(p.cash_balance, 2),
        "frozen_cash": round(p.frozen_cash, 2),
        "auto_rebalance": p.auto_rebalance,
        "is_active": p.is_active,
        "description": p.description,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _position_dict(p) -> dict:
    pnl_pct = (
        round((p.last_price - p.avg_price) / p.avg_price * 100, 2) if p.avg_price else 0.0
    )
    return {
        "portfolio_id": p.portfolio_id,
        "symbol": p.symbol,
        "side": p.side,
        "quantity": p.quantity,
        "avg_price": round(p.avg_price, 4),
        "last_price": round(p.last_price, 4),
        "prev_close": round(p.prev_close, 4) if p.prev_close else None,
        "market_value": round(p.market_value, 2),
        "unrealized_pnl": round(p.unrealized_pnl, 2),
        "pnl_percent": pnl_pct,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _rebalance_dict(r) -> dict:
    detail = None
    if r.detail:
        try:
            detail = json.loads(r.detail)
        except ValueError:
            detail = None
    return {
        "portfolio_id": r.portfolio_id,
        "strategy_id": r.strategy_id,
        "strategy_name": r.strategy_name,
        "mode": r.mode,
        "rebalance_date": _iso(r.rebalance_date),
        "trade_date": _iso(r.trade_date),
        "target_count": r.target_count,
        "orders_placed": r.orders_placed,
        "amount": r.amount,
        "status": r.status,
        "detail": detail,
    }


def _daily_value_dict(r: PortfolioDailyValue) -> dict:
    return {
        "portfolio_id": r.portfolio_id,
        "strategy_id": r.strategy_id,
        "value_date": r.value_date.isoformat() if r.value_date else None,
        "cash_balance": r.cash_balance,
        "market_value": r.market_value,
        "total_assets": r.total_assets,
        "daily_return": r.daily_return,
        "cumulative_return": r.cumulative_return,
    }


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# ============ CRUD ============

@router.post("", summary="创建组合")
async def create_portfolio(
    body: PortfolioCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_tables()
    p = await repo.create_portfolio(
        db,
        name=body.name,
        strategy_id=body.strategy_id,
        mode=body.mode,
        owner_user_id=getattr(current_user, "id", None),
        initial_capital=body.initial_capital,
        auto_rebalance=body.auto_rebalance if body.mode != "live" else False,
        is_active=body.is_active,
        description=body.description,
    )
    await db.commit()
    return Response.success(data=_portfolio_dict(p))


@router.get("", summary="组合列表")
async def list_portfolios(
    mode: Optional[str] = Query(None, description="过滤模式: paper / live"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    rows = await repo.list_portfolios(db, mode=mode)
    return Response.success(data=[_portfolio_dict(p) for p in rows])


@router.get("/{portfolio_id}", summary="组合详情")
async def get_portfolio(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    p = await repo.get_portfolio(db, portfolio_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组合不存在")
    return Response.success(data=_portfolio_dict(p))


@router.put("/{portfolio_id}", summary="更新组合")
async def update_portfolio(
    portfolio_id: int,
    body: PortfolioUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    p = await repo.get_portfolio(db, portfolio_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组合不存在")
    await repo.update_portfolio(
        db, p,
        name=body.name,
        strategy_id=body.strategy_id,
        auto_rebalance=body.auto_rebalance,
        is_active=body.is_active,
        description=body.description,
    )
    await db.commit()
    return Response.success(data=_portfolio_dict(p))


@router.delete("/{portfolio_id}", summary="删除组合")
async def delete_portfolio(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    p = await repo.get_portfolio(db, portfolio_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组合不存在")
    await repo.delete_portfolio(db, p)
    await db.commit()
    return Response.success(data={"id": portfolio_id})


# ============ 详情 ============

@router.get("/{portfolio_id}/overview", summary="组合概览（资金池 / 持仓）")
async def portfolio_overview(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    overview = await TradingCoordinator(db, portfolio_id=portfolio_id).get_overview()
    return Response.success(data=overview)


@router.get("/{portfolio_id}/positions", summary="组合持仓")
async def portfolio_positions(
    portfolio_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    positions = await TradingCoordinator(db, portfolio_id=portfolio_id).list_positions()
    return Response.success(data=[_position_dict(p) for p in positions])


@router.get("/{portfolio_id}/rebalances", summary="组合调仓记录")
async def portfolio_rebalances(
    portfolio_id: int,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    rows = await repo.list_rebalances(db, portfolio_id=portfolio_id, limit=limit)
    return Response.success(data=[_rebalance_dict(r) for r in rows])


@router.get("/{portfolio_id}/values", summary="组合每日市值与收益")
async def portfolio_values(
    portfolio_id: int,
    limit: int = Query(120, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    rows = (
        await db.execute(
            select(PortfolioDailyValue)
            .where(PortfolioDailyValue.portfolio_id == portfolio_id)
            .order_by(PortfolioDailyValue.value_date.desc())
            .limit(limit)
        )
    ).scalars().all()
    return Response.success(data=[_daily_value_dict(r) for r in reversed(rows)])


@router.post("/{portfolio_id}/rebalance", summary="手动触发该组合调仓")
async def trigger_portfolio_rebalance(
    portfolio_id: int,
    force: bool = Query(False, description="跳过调仓时点判断与当日防重"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    p = await repo.get_portfolio(db, portfolio_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组合不存在")
    result = await rebalance_one(db, p, force=force)
    return Response.success(data=result)
