"""因子底册库（backend 持有，关联 data-cleaner 源）

端点（挂在 /api/v1/factors 下，需登录）：
- GET    /                 因子底册（只含**已入库**的，读本地库）
- GET    /services         底册关联的 dc 源及其连接状态
- GET    /{code}           因子详情（读本地库）
- POST   /                 创建自定义因子（写 dc 后自动注册入库）
- PUT    /{code}           更新自定义因子
- DELETE /{code}           删除自定义因子
- POST   /ai-generate      AI 生成因子（需回源 dc）
- POST   /correlation      因子相关性矩阵（需回源 dc）

设计要点（见 docs/plans/2026-09-01.factor-registry-backend-owned.md）：
- 底册数据存于 backend 的 `factor_registry`，按 `service_code` 关联 dc 源；
- **入库 = 在 backend 注册**（POST /api/v1/cleaner/{code}/factors/import），
  未入库的因子不出现在底册，只在"远端因子列表"供勾选；
- **dc 连接状态即因子可用状态**：available 由 service.status 推导，不单独维护；
- 因此列表/详情读本地，dc 宕机时仍返回 200（只是 available=false）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.factor_library import (
    FactorAiGenerateRequest,
    FactorCorrelationRequest,
    FactorCreateRequest,
    FactorUpdateRequest,
)
from app.schemas.response import Response
from app.services import cleaner_gateway as gw
from app.services import factor_proxy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/factors", tags=["因子库"])


def _wrap(e: factor_proxy.FactorProxyError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(e))


@router.get("", summary="因子底册（只含已入库）")
async def list_factors(
    category: str | None = None,
    search: str | None = None,
    with_metrics: bool = True,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """读本地 factor_registry。未入库的因子不返回；dc 不可达不影响返回。"""
    await gw.ensure_cleaner_tables()
    items = await gw.list_catalog_factors(
        db, category=category, search=search, with_metrics=with_metrics
    )
    return Response.success(data=items)


@router.get("/services", summary="底册关联的 dc 源及连接状态")
async def list_factor_services(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await gw.ensure_cleaner_tables()
    return Response.success(data=await gw.list_catalog_services(db))


@router.get("/{code}", summary="因子详情")
async def get_factor(
    code: str,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await gw.ensure_cleaner_tables()
    items = await gw.list_catalog_factors(db)
    hit = next(
        (i for i in items if i["code"] == code and (not service_code or i["service_code"] == service_code)),
        None,
    )
    if hit is None:
        raise HTTPException(
            status_code=404, detail=f"因子不存在或未入库: {code}"
        )
    return Response.success(data=hit)


@router.post("", summary="创建自定义因子")
async def create_factor(
    payload: FactorCreateRequest,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """先写 dc（dc 计算因子值需要定义），成功后自动注册入库，使底册立即可见。"""
    await gw.ensure_cleaner_tables()
    try:
        data = await factor_proxy.create_factor(
            db, payload.model_dump(exclude_none=True), service_code
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e

    code = (data or {}).get("code") if isinstance(data, dict) else None
    if code:
        try:
            svc = await factor_proxy.pick_service(db, service_code)
            await gw.import_factors(db, svc, [code], is_enabled=True)
        except Exception as e:  # noqa: BLE001  注册失败不影响 dc 已创建的事实
            logger.warning("自定义因子创建后注册入库失败 %s: %s", code, e)
    return Response.success(data=data, msg="因子已创建并入库")


@router.put("/{code}", summary="更新自定义因子")
async def update_factor(
    code: str,
    payload: FactorUpdateRequest,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await gw.ensure_cleaner_tables()
    try:
        data = await factor_proxy.update_factor(
            db, code, payload.model_dump(exclude_none=True), service_code
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e

    try:
        svc = await factor_proxy.pick_service(db, service_code)
        await gw.sync_factors(db, svc)
    except Exception as e:  # noqa: BLE001
        logger.warning("更新后同步底册失败 %s: %s", code, e)
    return Response.success(data=data, msg="因子已更新")


@router.delete("/{code}", summary="删除自定义因子")
async def delete_factor(
    code: str,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await gw.ensure_cleaner_tables()
    try:
        await factor_proxy.delete_factor(db, code, service_code)
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e

    svc_code = service_code
    if not svc_code:
        try:
            svc_code = (await factor_proxy.pick_service(db, service_code)).service_code
        except Exception:  # noqa: BLE001
            svc_code = None
    if svc_code:
        await gw.remove_registry_factor(db, svc_code, code)
    return Response.success(msg="因子已删除")


@router.post("/ai-generate", summary="AI 生成因子")
async def ai_generate(
    payload: FactorAiGenerateRequest,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        data = await factor_proxy.ai_generate(
            db, payload.model_dump(exclude_none=True), service_code
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e
    return Response.success(data=data, msg="AI 因子已生成")


@router.post("/correlation", summary="因子相关性矩阵")
async def correlation(
    payload: FactorCorrelationRequest,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        data = await factor_proxy.correlation(db, payload.codes, service_code)
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e
    return Response.success(data=data)
