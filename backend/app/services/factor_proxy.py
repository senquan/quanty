"""因子库代理客户端

把主后端的因子底册库请求转发到已登记的 data-cleaner 实例：
- GET    /api/v1/factor                 因子列表（category / search 过滤）
- GET    /api/v1/factor/{code}          因子详情（含历史效能指标 metrics）
- POST   /api/v1/factor                 创建自定义因子
- PUT    /api/v1/factor/{code}          更新自定义因子
- DELETE /api/v1/factor/{code}          删除自定义因子
- POST   /api/v1/factor/ai-generate     AI 生成因子
- POST   /api/v1/factor/correlation     因子相关性矩阵

选取清洗服务的策略：取第一个启用的服务（is_active），不依赖其是否在线，
由调用方根据异常决定是否降级。
"""
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cleaner import CleanerService

DEFAULT_TIMEOUT = 30.0  # 打分/相关性矩阵/AI生成因子可能耗时较长，放宽到 30s


class FactorProxyError(Exception):
    """转发清洗服务失败"""


async def pick_service(db: AsyncSession, service_code: str | None = None) -> CleanerService:
    stmt = select(CleanerService)
    if service_code:
        stmt = stmt.where(CleanerService.service_code == service_code)
    else:
        stmt = stmt.where(CleanerService.is_active.is_(True))
    svc = (await db.execute(stmt.order_by(CleanerService.id))).scalars().first()
    if not svc:
        raise FactorProxyError(
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
        raise FactorProxyError(f"无法连接清洗服务 {svc.service_code}：{e}") from e
    except Exception as e:  # noqa: BLE001
        raise FactorProxyError(f"请求清洗服务失败：{e}") from e

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise FactorProxyError(f"清洗服务返回 {resp.status_code}：{detail}")

    if not resp.content:
        return None
    return resp.json()


async def list_factors(
    db: AsyncSession,
    category: str | None = None,
    search: str | None = None,
    with_metrics: bool = True,
    service_code: str | None = None,
) -> list[dict]:
    """因子列表。

    with_metrics 由清洗服务在列表接口里一次性批量挂载（include_metrics=true），
    不要改成逐因子查详情——33 个因子并发会把请求拖到近 50s，直接导致前端超时。
    """
    svc = await pick_service(db, service_code)
    params = {k: v for k, v in (("category", category), ("search", search)) if v}
    if with_metrics:
        params["include_metrics"] = "true"
    items = await _request(svc, "GET", "/api/v1/factor", params=params or None)
    return items if isinstance(items, list) else []


async def get_factor(db: AsyncSession, code: str, service_code: str | None = None) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "GET", f"/api/v1/factor/{code}")


async def create_factor(
    db: AsyncSession, payload: dict, service_code: str | None = None
) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "POST", "/api/v1/factor", payload=payload)


async def update_factor(
    db: AsyncSession, code: str, payload: dict, service_code: str | None = None
) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "PUT", f"/api/v1/factor/{code}", payload=payload)


async def delete_factor(db: AsyncSession, code: str, service_code: str | None = None) -> None:
    svc = await pick_service(db, service_code)
    await _request(svc, "DELETE", f"/api/v1/factor/{code}")


async def ai_generate(
    db: AsyncSession, payload: dict, service_code: str | None = None
) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(svc, "POST", "/api/v1/factor/ai-generate", payload=payload)


async def correlation(
    db: AsyncSession, codes: list[str], service_code: str | None = None
) -> dict:
    svc = await pick_service(db, service_code)
    return await _request(
        svc, "POST", "/api/v1/factor/correlation", payload={"codes": codes}
    )
