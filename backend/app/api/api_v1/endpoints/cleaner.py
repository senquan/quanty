"""清洗服务管理 + 因子聚合（阶段 B）

端点（均挂在 /api/v1/cleaner 下，需登录）：
- POST   /           注册清洗服务并测试连接
- GET    /           列出已注册清洗服务
- GET    /{code}     获取单个服务详情
- PUT    /{code}     更新服务配置
- DELETE /{code}     删除服务（级联移除其因子登记）
- POST   /{code}/test        仅测试连接，不落库
- POST   /{code}/qos         手动触发 QoS 轮询并回写状态
- POST   /{code}/sync        拉取并入库该服务的因子口径
- POST   /{code}/factors/enable   批量勾选/取消入库因子
- GET    /factors/registry    聚合因子底册（可选 service_code / only_enabled 过滤）
- GET    /{code}/coverage             查看 dc 最新交易日数据完整度（经 WS 命令）
- POST   /{code}/coverage/repair      指令 dc 执行修复/回填补全（异步）
- GET    /{code}/coverage/repair/status  查看修复进度 / 最近结果
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.cleaner import (
    CleanerServiceCreate,
    CleanerServiceOut,
    CleanerServiceUpdate,
    FactorImportRequest,
    FactorListQuery,
    FactorRegistryOut,
)
from app.schemas.response import Response
from app.core import credential
from app.services import cleaner_gateway as gw
from app.services import coverage_proxy
from app.ws import commands
from app.ws.registry import registry

router = APIRouter(prefix="/cleaner", tags=["cleaner"])


async def _ensure_tables() -> None:
    """依赖项：首次请求时幂等建表"""
    await gw.ensure_cleaner_tables()


@router.post("", summary="注册清洗服务")
async def register_service(
    payload: CleanerServiceCreate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    if await gw.get_service(db, payload.service_code):
        raise HTTPException(status_code=400, detail=f"服务编码 {payload.service_code} 已存在")

    # 注册前先测试连接
    probe = await gw.test_connection(payload.base_url, payload.api_key)
    if not probe["ok"]:
        raise HTTPException(status_code=400, detail=f"连接测试失败：{probe['message']}")

    svc = gw.CleanerService(
        service_code=payload.service_code,
        name=payload.name,
        base_url=payload.base_url.rstrip("/"),
        # api_key 治理：多 key 逗号并存，整体加密存储（设计文档 §8 / §11）
        api_key=credential.store_keys([payload.api_key]),
        status=probe.get("status") or "online",
    )
    db.add(svc)
    await db.commit()
    await db.refresh(svc)
    return Response.success(data=CleanerServiceOut(**svc.to_dict()))


@router.get("", summary="清洗服务列表")
async def list_services(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    rows = [CleanerServiceOut(**s.to_dict()) for s in await gw.list_services(db)]
    return Response.success(data=rows)


@router.get("/{service_code}", summary="服务详情")
async def get_service(
    service_code: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    return Response.success(data=CleanerServiceOut(**svc.to_dict()))


@router.put("/{service_code}", summary="更新服务")
async def update_service(
    service_code: str,
    payload: CleanerServiceUpdate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")

    data = payload.model_dump(exclude_unset=True)
    if "base_url" in data and data["base_url"]:
        data["base_url"] = data["base_url"].rstrip("/")
    # api_key 治理：更新时也加密存储
    if "api_key" in data and data["api_key"] is not None:
        data["api_key"] = credential.store_keys([data["api_key"]])
    for k, v in data.items():
        setattr(svc, k, v)
    await db.commit()
    await db.refresh(svc)
    return Response.success(data=CleanerServiceOut(**svc.to_dict()))


@router.delete("/{service_code}", summary="删除服务")
async def delete_service(
    service_code: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    await db.delete(svc)
    await db.commit()
    return Response.success(msg="删除成功")


@router.post("/{service_code}/test", summary="测试连接（不落库）")
async def test_service(
    service_code: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    return Response.success(data=await gw.test_connection(svc.base_url, svc.api_key))


@router.post("/{service_code}/qos", summary="手动触发 QoS 轮询")
async def poll_service_qos(
    service_code: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    qos = await gw.poll_qos(svc)
    await db.commit()
    return Response.success(data=qos)


def _ws_snapshot(service_code: str) -> dict:
    """取该服务连接上的最近覆盖度/修复快照（命令终态即使 Future 超时也会落这里）。"""
    for conn in registry.all():
        if conn.service_code == service_code:
            return {
                "last_coverage": conn.last_coverage,
                "last_repair": conn.last_repair,
            }
    return {"last_coverage": None, "last_repair": None}


@router.get("/{service_code}/coverage", summary="数据完整度（最新交易日覆盖度）")
async def get_coverage(
    service_code: str,
    min_ratio: float = 0.95,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    try:
        data = await coverage_proxy.check(service_code, min_ratio=min_ratio)
    except coverage_proxy.CoverageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except commands.CommandTimeoutError:
        raise HTTPException(status_code=504, detail="dc 响应超时")
    return Response.success(data=data)


@router.post("/{service_code}/coverage/repair", summary="指令 dc 修复/回填补全（异步）")
async def trigger_coverage_repair(
    service_code: str,
    source: str | None = None,
    force: bool = True,
    min_ratio: float = 0.95,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    try:
        data = await coverage_proxy.repair(
            service_code, source=source, min_ratio=min_ratio, force=force
        )
    except coverage_proxy.CoverageUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except commands.CommandTimeoutError:
        raise HTTPException(status_code=504, detail="dc 响应超时")
    return Response.success(data=data, msg="修复已受理，正在后台执行")


@router.get("/{service_code}/coverage/repair/status", summary="修复进度 / 最近结果")
async def coverage_repair_status(
    service_code: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    snap = _ws_snapshot(service_code)
    try:
        data = await coverage_proxy.repair_status(service_code)
    except coverage_proxy.CoverageUnavailableError as e:
        # 离线时仍回快照，便于看到"最后一次已知结果"
        return Response.success(data={"online": False, "reason": str(e), **snap})
    except commands.CommandTimeoutError:
        raise HTTPException(status_code=504, detail="dc 响应超时")
    return Response.success(data={"online": True, **data, **snap})


@router.post("/{service_code}/sync", summary="拉取并入库因子口径")
async def sync_service_factors(
    service_code: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    await gw.poll_qos(svc)  # 先刷新状态
    try:
        synced = await gw.sync_factors(db, svc)
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"因子同步失败：{e}")
    await db.commit()
    return Response.success(data={"synced": synced, "status": svc.status}, msg="同步完成")


@router.post("/{service_code}/factors/enable", summary="批量勾选/取消入库因子")
async def enable_factors(
    service_code: str,
    payload: FactorListQuery,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    if payload.service_code and payload.service_code != service_code:
        raise HTTPException(status_code=400, detail="路径 service_code 与 body 不一致")
    count = await gw.set_factor_enabled(db, service_code, payload.factor_codes, payload.is_enabled)
    return Response.success(data={"updated": count}, msg="更新成功")


@router.get("/{service_code}/factors", summary="分页列出该清洗服务的因子库")
async def list_service_factors(
    service_code: str,
    page: int = 1,
    page_size: int = 10,
    category: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    try:
        data = await gw.list_remote_factors(
            db, svc, category=category, search=search,
            page=page, page_size=page_size,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"读取清洗服务因子库失败：{e}")
    return Response.success(data=data)


@router.post("/{service_code}/factors/import", summary="勾选因子入库")
async def import_service_factors(
    service_code: str,
    payload: FactorImportRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    svc = await gw.get_service(db, service_code)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    try:
        result = await gw.import_factors(
            db, svc, payload.factor_codes, payload.is_enabled
        )
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"因子入库失败：{e}")
    return Response.success(
        data=result,
        msg=f"入库完成：新增 {result['created']}，更新 {result['updated']}",
    )


@router.get("/factors/registry", summary="聚合因子底册")
async def factor_registry(
    service_code: str | None = None,
    only_enabled: bool = False,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_ensure_tables),
    _user: User = Depends(get_current_user),
):
    rows = [FactorRegistryOut(**r.to_dict()) for r in await gw.list_factors(db, service_code=service_code, only_enabled=only_enabled)]
    return Response.success(data=rows)
