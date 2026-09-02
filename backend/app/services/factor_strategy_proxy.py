"""因子选股策略代理客户端

把主后端的因子策略请求转发到已登记的 data-cleaner 实例（与 factor_proxy 一致）：
- GET    /api/v1/strategy/strategies                 策略列表
- POST   /api/v1/strategy/strategies                 创建
- GET    /api/v1/strategy/strategies/{id}            详情
- PUT    /api/v1/strategy/strategies/{id}            更新
- DELETE /api/v1/strategy/strategies/{id}            删除
- POST   /api/v1/strategy/strategies/{id}/backtest   回测
- GET    /api/v1/strategy/strategies/{id}/backtests  回测历史
- POST   /api/v1/strategy/strategies/{id}/rebalance  手动调仓
- GET    /api/v1/strategy/strategies/{id}/executions 执行记录
- POST   /api/v1/strategy/scores                     任意配置算目标持仓
- POST   /api/v1/strategy/industries/refresh         行业刷新
"""
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cleaner import CleanerService

DEFAULT_TIMEOUT = 30.0  # 回测可能耗时稍长


class FactorStrategyProxyError(Exception):
    """转发清洗服务失败"""


async def pick_service(db: AsyncSession, service_code: str | None = None) -> CleanerService:
    stmt = select(CleanerService)
    if service_code:
        stmt = stmt.where(CleanerService.service_code == service_code)
    else:
        stmt = stmt.where(CleanerService.is_active.is_(True))
    svc = (await db.execute(stmt.order_by(CleanerService.id))).scalars().first()
    if not svc:
        raise FactorStrategyProxyError(
            f"未找到可用的清洗服务（service_code={service_code or '自动选择'}）"
        )
    return svc


async def _request(
    svc: CleanerService,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
) -> Any:
    headers = {"X-API-Key": svc.api_key} if svc.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.request(
                method,
                f"{svc.base_url}{path}",
                headers=headers,
                params=params,
                json=payload,
            )
    except httpx.ConnectError as e:
        raise FactorStrategyProxyError(f"无法连接清洗服务：{e}") from e
    except Exception as e:  # noqa: BLE001
        raise FactorStrategyProxyError(f"请求清洗服务失败：{e}") from e

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise FactorStrategyProxyError(f"清洗服务返回 {resp.status_code}：{detail}")

    if not resp.content:
        return None
    return resp.json()


async def list_strategies(db, active_only=False, service_code=None) -> list[dict]:
    svc = await pick_service(db, service_code)
    params = {"active_only": "true"} if active_only else None
    items = await _request(svc, "GET", "/api/v1/strategy/strategies", params=params)
    return items if isinstance(items, list) else []


async def create_strategy(db, payload: dict, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "POST", "/api/v1/strategy/strategies", payload=payload)


async def get_strategy(db, sid: int, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "GET", f"/api/v1/strategy/strategies/{sid}")


async def update_strategy(db, sid: int, payload: dict, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "PUT", f"/api/v1/strategy/strategies/{sid}", payload=payload)


async def delete_strategy(db, sid: int, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "DELETE", f"/api/v1/strategy/strategies/{sid}")


async def backtest(db, sid: int, start=None, end=None, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    payload = {k: v for k, v in (("start", start), ("end", end)) if v}
    return await _request(svc, "POST", f"/api/v1/strategy/strategies/{sid}/backtest", payload=payload or {})


async def list_backtests(db, sid: int, service_code=None) -> list[dict]:
    svc = await pick_service(db, service_code)
    items = await _request(svc, "GET", f"/api/v1/strategy/strategies/{sid}/backtests")
    return items if isinstance(items, list) else []


async def get_backtest(db, sid: int, bid: int, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "GET", f"/api/v1/strategy/strategies/{sid}/backtests/{bid}")


async def rebalance(db, sid: int, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "POST", f"/api/v1/strategy/strategies/{sid}/rebalance")


async def list_executions(db, sid: int, limit: int = 50, service_code=None) -> list[dict]:
    svc = await pick_service(db, service_code)
    items = await _request(
        svc, "GET", f"/api/v1/strategy/strategies/{sid}/executions",
        params={"limit": limit},
    )
    return items if isinstance(items, list) else []


async def scores(db, config: dict, as_of: str | None = None, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(
        svc, "POST", "/api/v1/strategy/scores",
        payload={"config": config, "as_of": as_of},
    )


async def refresh_industries(db, service_code=None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "POST", "/api/v1/strategy/industries/refresh")


async def instrument_metadata(
    db, symbols: list[str] | None = None, service_code=None
) -> dict:
    """标的代码 → 基础信息（含中文名），数据源为 dc 只读元数据接口。

    backend 的标的主数据表（instruments）以此为名字源，查询缺失时懒回填。
    symbols 为 None 表示拉全量。
    """
    svc = await pick_service(db, service_code)
    params = {}
    if symbols:
        params["symbols"] = ",".join(symbols)
    return await _request(
        svc, "GET", "/api/v1/strategy/instruments/metadata", params=params
    )


async def factor_availability(db, service_code=None) -> dict:
    """因子可用性：由**本地底册 + dc 连接状态**推导，不再回源 dc。

    语义（见 docs/plans/2026-09-01.factor-registry-backend-owned.md §6）：
    "dc 连接状态就是因子可用状态"。故 可用 = 已入库(底册中存在) 且 其 dc 源
    状态为 online/degraded。dc 宕机时全部不可用，但接口仍正常返回（不超时）。

    返回契约保持 {factor_code: bool} 不变（前端 factorAvailabilityApi 依赖）。
    """
    from app.services import cleaner_gateway as gw

    await gw.ensure_cleaner_tables()
    items = await gw.list_catalog_factors(db, with_metrics=False)
    if service_code:
        items = [i for i in items if i["service_code"] == service_code]
    return {i["code"]: bool(i["available"]) for i in items}
