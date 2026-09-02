"""因子选股策略接口（JWT 保护）

职责归位（docs/memo/2026-09-02.md §四/§五）：
- 策略 CRUD、回测结果、执行记录 直接读写 backend 库（factor_strategy_service），
  quant/dashboard 不再经 dc；
- 仅因子计算(scores)、回测运行(调 dc 算)、撮合/发信号(rebalance)、
  行业刷新、行情 才调用 data-cleaner（factor_strategy_proxy / market_proxy）。
"""
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.api_v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import factor_strategy_proxy as proxy
from app.services import factor_strategy_service as svc
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
    data = await svc.list_strategies(db, active_only=active_only)
    return Response.success(data=data)


@router.post("")
async def create_strategy(
    payload: StrategyCreateReq,
    db=Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await svc.create_strategy(
        db,
        name=payload.name,
        description=payload.description,
        config=payload.config,
        is_active=payload.is_active,
        user_id=user.id,
    )
    return Response.success(data=data)


@router.get("/{sid}")
async def get_strategy(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await svc.get_strategy(db, sid)
    if data is None:
        return Response.fail(code=404, msg="策略不存在")
    return Response.success(data=data)


@router.put("/{sid}")
async def update_strategy(
    sid: int, payload: StrategyUpdateReq,
    db=Depends(get_db), _: User = Depends(get_current_user),
):
    data = await svc.update_strategy(
        db, sid,
        name=payload.name,
        description=payload.description,
        config=payload.config,
        is_active=payload.is_active,
    )
    if data is None:
        return Response.fail(code=404, msg="策略不存在")
    return Response.success(data=data)


@router.delete("/{sid}")
async def delete_strategy(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    ok = await svc.delete_strategy(db, sid)
    if not ok:
        return Response.fail(code=404, msg="策略不存在")
    return Response.success(data={"deleted": True})


@router.post("/{sid}/backtest")
async def backtest(
    sid: int, payload: BacktestReq,
    db=Depends(get_db), _: User = Depends(get_current_user),
):
    # 计算在 dc，结果回写 backend
    detail = await svc.run_backtest(db, sid, start=payload.start, end=payload.end)
    data = {
        "backtest_id": detail["id"],
        "metrics": detail["metrics"],
        "nav": detail["nav"],
        "rebalances": detail["rebalances"],
        "warnings": detail["warnings"],
    }
    return Response.success(data=data)


@router.get("/{sid}/backtests")
async def backtests(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await svc.list_backtests(db, sid)
    return Response.success(data=data)


@router.get("/{sid}/backtests/{bid}")
async def backtest_detail(
    sid: int, bid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await svc.get_backtest(db, sid, bid)
    if data is None:
        return Response.fail(code=404, msg="回测不存在")
    return Response.success(data=data)


@router.post("/{sid}/rebalance")
async def rebalance(
    sid: int, db=Depends(get_db), _: User = Depends(get_current_user)
):
    # 撮合/发信号在 dc
    data = await proxy.rebalance(db, sid)
    return Response.success(data=data)


@router.get("/{sid}/executions")
async def executions(
    sid: int, limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db), _: User = Depends(get_current_user),
):
    # 执行记录读 backend trading_rebalance_records
    data = await svc.list_executions(db, sid, limit=limit)
    return Response.success(data=data)


@router.post("/scores")
async def scores(
    payload: ScoresReq, db=Depends(get_db), _: User = Depends(get_current_user)
):
    # 纯计算在 dc
    data = await proxy.scores(db, payload.config, as_of=payload.as_of)
    return Response.success(data=data)


@router.get("/factors/availability")
async def factors_availability(
    db=Depends(get_db), _: User = Depends(get_current_user)
):
    # 由本地底册 + dc 状态推导，不重算因子
    data = await proxy.factor_availability(db)
    return Response.success(data=data)


@router.post("/industries/refresh")
async def industries_refresh(
    db=Depends(get_db), _: User = Depends(get_current_user)
):
    data = await proxy.refresh_industries(db)
    return Response.success(data=data)
