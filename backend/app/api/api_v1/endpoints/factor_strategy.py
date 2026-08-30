"""因子选股策略接口（主后端代理到 data-cleaner，JWT 保护）

所有读写经 factor_strategy_proxy 转发；策略配置与计算统一来源在 data-cleaner。
"""
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.api_v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import factor_strategy_proxy as proxy
from app.schemas.response import Response

router = APIRouter(prefix="/factor-strategies", tags=["因子策略"])


class StrategyCreateReq(BaseModel):
    name: str
    description: str | None = None
    config: dict
    is_active: bool = False


class StrategyUpdateReq(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None
    is_active: bool | None = None


class BacktestReq(BaseModel):
    start: str | None = None
    end: str | None = None


class ScoresReq(BaseModel):
    config: dict
    as_of: str | None = None


@router.get("")
async def list_strategies(
    active_only: bool = False,
    db=Depends(get_db),
    _: User = Depends(get_current_user),
):
    data = await proxy.list_strategies(db, active_only=active_only)
    return Response.success(data=data)


@router.post("")
async def create_strategy(
    payload: StrategyCreateReq,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
):
    body = payload.model_dump()
    body["owner"] = str(user.id)
    data = await proxy.create_strategy(db, body)
    return Response.success(data=data)


@router.get("/{sid}")
async def get_strategy(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.get_strategy(db, sid)
    return Response.success(data=data)


@router.put("/{sid}")
async def update_strategy(
    sid: int, payload: StrategyUpdateReq,
    db=Depends(get_db), _: User = Depends(get_current_user),
):
    data = await proxy.update_strategy(db, sid, payload.model_dump(exclude_unset=True))
    return Response.success(data=data)


@router.delete("/{sid}")
async def delete_strategy(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.delete_strategy(db, sid)
    return Response.success(data=data)


@router.post("/{sid}/backtest")
async def backtest(
    sid: int, payload: BacktestReq,
    db=Depends(get_db), _: User = Depends(get_current_user),
):
    data = await proxy.backtest(db, sid, start=payload.start, end=payload.end)
    return Response.success(data=data)


@router.get("/{sid}/backtests")
async def backtests(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.list_backtests(db, sid)
    return Response.success(data=data)


@router.get("/{sid}/backtests/{bid}")
async def backtest_detail(
    sid: int, bid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.get_backtest(db, sid, bid)
    return Response.success(data=data)


@router.post("/{sid}/rebalance")
async def rebalance(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.rebalance(db, sid)
    return Response.success(data=data)


@router.get("/{sid}/executions")
async def executions(
    sid: int, limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db), _: User = Depends(get_current_user),
):
    data = await proxy.list_executions(db, sid, limit=limit)
    return Response.success(data=data)


@router.post("/scores")
async def scores(
    payload: ScoresReq, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.scores(db, payload.config, as_of=payload.as_of)
    return Response.success(data=data)


@router.get("/factors/availability")
async def factors_availability(
    db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.factor_availability(db)
    return Response.success(data=data)


@router.post("/industries/refresh")
async def industries_refresh(
    db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.refresh_industries(db)
    return Response.success(data=data)
