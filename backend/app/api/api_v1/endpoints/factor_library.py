"""因子底册库（代理清洗服务的因子定义与效能指标）

端点（挂在 /api/v1/factors 下，需登录）：
- GET    /                 因子列表（category / search / with_metrics）
- GET    /{code}           因子详情
- POST   /                 创建自定义因子
- PUT    /{code}           更新自定义因子
- DELETE /{code}           删除自定义因子
- POST   /ai-generate      AI 生成因子
- POST   /correlation      因子相关性矩阵
"""
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
from app.services import factor_proxy

router = APIRouter(prefix="/factors", tags=["因子库"])


def _wrap(e: factor_proxy.FactorProxyError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(e))


@router.get("", summary="因子列表")
async def list_factors(
    category: str | None = None,
    search: str | None = None,
    with_metrics: bool = True,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        items = await factor_proxy.list_factors(
            db, category=category, search=search,
            with_metrics=with_metrics, service_code=service_code,
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e
    return Response.success(data=items)


@router.get("/{code}", summary="因子详情")
async def get_factor(
    code: str,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        return Response.success(
            data=await factor_proxy.get_factor(db, code, service_code)
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e


@router.post("", summary="创建自定义因子")
async def create_factor(
    payload: FactorCreateRequest,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        data = await factor_proxy.create_factor(
            db, payload.model_dump(exclude_none=True), service_code
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e
    return Response.success(data=data, msg="因子已创建")


@router.put("/{code}", summary="更新自定义因子")
async def update_factor(
    code: str,
    payload: FactorUpdateRequest,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        data = await factor_proxy.update_factor(
            db, code, payload.model_dump(exclude_none=True), service_code
        )
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e
    return Response.success(data=data, msg="因子已更新")


@router.delete("/{code}", summary="删除自定义因子")
async def delete_factor(
    code: str,
    service_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        await factor_proxy.delete_factor(db, code, service_code)
    except factor_proxy.FactorProxyError as e:
        raise _wrap(e) from e
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
