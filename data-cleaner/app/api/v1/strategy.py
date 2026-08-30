"""因子选股策略接口（data-cleaner 持有配置与计算）

- CRUD：/strategies
- 回测：POST /strategies/{id}/backtest
- 持仓预览：POST /scores（任意配置算目标持仓）
- 手动调仓：POST /strategies/{id}/rebalance
- 执行记录：GET /strategies/{id}/executions
- 行业刷新：POST /industries/refresh

受 X-API-Key 保护（与主后端代理一致）。
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import asyncio

import app.storage.db as db
from app.core.logging import get_logger
from app.industry import store as industry_store
from app.strategy import engine
from app.strategy import rebalance as rebalance_task
from app.strategy import store as strat_store


async def _run_sync(fn, *args):
    """在线程池里跑同步函数（内部可能用 db.run_async，不能在请求 loop 内直接调）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)

logger = get_logger(__name__)
router = APIRouter(prefix="/strategy", tags=["因子策略"])


# ---------------- 请求模型 ---------------- #
class StrategyCreate(BaseModel):
    name: str
    description: str | None = None
    config: dict
    is_active: bool = False
    owner: str | None = None


class StrategyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None
    is_active: bool | None = None
    owner: str | None = None


class BacktestRequest(BaseModel):
    start: str | None = None
    end: str | None = None


class ScoresRequest(BaseModel):
    config: dict
    as_of: str | None = None


# ---------------- CRUD ---------------- #
@router.post("/strategies", status_code=201)
async def create_strategy(payload: StrategyCreate):
    try:
        row = await strat_store.create_strategy(
            payload.name, payload.description,
            payload.config, payload.owner, payload.is_active,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存失败: {e}") from e
    return row


@router.get("/strategies")
async def list_strategies(active_only: bool = False):
    return await strat_store.list_strategies(active_only=active_only)


@router.get("/strategies/{sid}")
async def get_strategy(sid: int):
    row = await strat_store.get_strategy(sid)
    if not row:
        raise HTTPException(status_code=404, detail=f"策略不存在: {sid}")
    return row


@router.put("/strategies/{sid}")
async def update_strategy(sid: int, payload: StrategyUpdate):
    row = await strat_store.update_strategy(
        sid,
        name=payload.name,
        description=payload.description,
        config=payload.config,
        is_active=payload.is_active,
        owner=payload.owner,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"策略不存在: {sid}")
    return row


@router.delete("/strategies/{sid}")
async def delete_strategy(sid: int):
    ok = await strat_store.delete_strategy(sid)
    if not ok:
        raise HTTPException(status_code=404, detail=f"策略不存在: {sid}")
    return {"id": sid, "status": "deleted"}


# ---------------- 回测 ---------------- #
@router.post("/strategies/{sid}/backtest")
async def backtest(sid: int, req: BacktestRequest):
    row = await strat_store.get_strategy(sid)
    if not row:
        raise HTTPException(status_code=404, detail=f"策略不存在: {sid}")
    try:
        result = await _run_sync(engine.run_backtest, row["config"], req.start, req.end)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"回测失败: {e}") from e
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    bid = await strat_store.save_backtest(
        sid, req.start, req.end,
        result.get("metrics", {}),
        result.get("nav", []),
        result.get("rebalances", []),
        result.get("warnings", []),
    )
    return {
        "backtest_id": bid,
        "metrics": result.get("metrics"),
        "nav": result.get("nav"),
        "rebalances": result.get("rebalances"),
        "warnings": result.get("warnings"),
    }


@router.get("/strategies/{sid}/backtests")
async def backtests(sid: int):
    return await strat_store.list_backtests(sid)


@router.get("/strategies/{sid}/backtests/{bid}")
async def backtest_detail(sid: int, bid: int):
    row = await strat_store.get_backtest(bid)
    if not row or row.get("strategy_id") != sid:
        raise HTTPException(status_code=404, detail=f"回测不存在: {bid}")
    return row


# ---------------- 持仓预览 / 手动调仓 ---------------- #
@router.get("/factors/availability")
async def factors_availability():
    """返回 {code: bool}，指示各因子当前是否有因子值（前端创建时置灰空因子）。"""
    return engine.factor_availability()


@router.post("/scores")
async def scores(req: ScoresRequest):
    try:
        return await _run_sync(engine.compute_target, req.config, req.as_of)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"计算失败: {e}") from e


@router.post("/strategies/{sid}/rebalance")
async def manual_rebalance(sid: int):
    row = await strat_store.get_strategy(sid)
    if not row:
        raise HTTPException(status_code=404, detail=f"策略不存在: {sid}")
    summary = await _run_sync(rebalance_task.rebalance_one, row)
    return summary


@router.get("/strategies/{sid}/executions")
async def executions(sid: int, limit: int = 50):
    return await strat_store.list_executions(sid, limit=limit)


# ---------------- 行业刷新 ---------------- #
@router.post("/industries/refresh")
async def industries_refresh():
    try:
        return await _run_sync(industry_store.refresh_industries)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"行业刷新失败: {e}") from e
