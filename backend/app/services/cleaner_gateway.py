"""清洗服务网关客户端（阶段 B）

负责与主后端登记的 data-cleaner 实例通信：
- test_connection / poll_qos: 调用清洗服务 `GET /api/v1/qos`
- fetch_factors:          调用清洗服务 `GET /api/v1/factor`（需 X-API-Key）
- sync_factors:           把因子口径 upsert 进 `factor_registry`（按 service_code+factor_code 幂等）

所有对外请求统一超时（默认 5s），失败不抛异常，交由调用层决定状态标注。
"""
import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, engine
from app.models.cleaner import CleanerService, FactorRegistry

DEFAULT_TIMEOUT = 5.0


# 仅对本次新增的两张表做幂等建表（不干预现有 alembic 管理的表）
_INIT_LOCK = None


async def ensure_cleaner_tables() -> None:
    """幂等建表：只在首次调用时执行一次（基于 async engine）"""
    global _INIT_LOCK
    if _INIT_LOCK is None:
        _INIT_LOCK = asyncio.Lock()
    async with _INIT_LOCK:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[CleanerService.__table__, FactorRegistry.__table__],
            )


async def _get_json(base_url: str, path: str, api_key: str | None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    headers = {"X-API-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{base_url}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json()


async def test_connection(base_url: str, api_key: str) -> dict:
    """探测清洗服务可达性 + 因子列表可用性，返回标准化结果"""
    try:
        qos = await _get_json(base_url, "/api/v1/qos", None)
        # qos 为公开接口；若带 key 也能访问 factors 则说明认证链路通
        factors = await _get_json(base_url, "/api/v1/factor", api_key)
        return {
            "ok": True,
            "status": qos.get("status"),
            "factor_count": len(factors) if isinstance(factors, list) else qos.get("factor_count", 0),
            "message": "连接成功",
        }
    except httpx.ConnectError:
        return {"ok": False, "status": "offline", "message": "无法连接到清洗服务（网络不通）"}
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 401:
            return {"ok": False, "status": "unauthorized", "message": "X-API-Key 校验失败，请检查 key"}
        return {"ok": False, "status": "error", "message": f"清洗服务返回 {code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "message": str(e)}


async def poll_qos(service: CleanerService) -> dict:
    """拉取 QoS 快照（公开接口，无需 key）"""
    try:
        qos = await _get_json(service.base_url, "/api/v1/qos", None)
        service.status = qos.get("status", "online")
        service.qos = qos
        service.last_heartbeat = datetime.now()
        return qos
    except Exception as e:  # noqa: BLE001
        service.status = "offline"
        service.qos = {"error": str(e)}
        service.last_heartbeat = datetime.now()
        return {"status": "offline", "error": str(e)}


def _normalize_factor(item: dict) -> dict:
    """把清洗服务返回的因子条目归一为注册表口径"""
    sources = item.get("data_source") or item.get("data_sources")
    if isinstance(sources, list):
        sources = ",".join(str(s) for s in sources) or None
    return {
        "code": item.get("code") or item.get("factor_code"),
        "name": item.get("name") or "",
        "category": item.get("category"),
        "frequency": item.get("frequency"),
        "description": item.get("description"),
        "formula": item.get("formula"),
        "data_source": sources,
    }


async def fetch_factors(
    service: CleanerService,
    category: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """从清洗服务拉取因子列表（受保护接口，需 key）

    data-cleaner 的 GET /api/v1/factor 支持 category / search 过滤，但不支持分页，
    故过滤交由远端、分页在本层做。
    """
    query = urlencode({k: v for k, v in (("category", category), ("search", search)) if v})
    path = f"/api/v1/factor?{query}" if query else "/api/v1/factor"
    data = await _get_json(service.base_url, path, service.api_key)
    return data if isinstance(data, list) else []


async def list_remote_factors(
    db: AsyncSession,
    service: CleanerService,
    category: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """分页列出清洗服务的因子库，并标注本地是否已入库。"""
    remote = await fetch_factors(service, category=category, search=search)
    items = [_normalize_factor(i) for i in remote]
    items = [i for i in items if i["code"]]

    # 叠加本地 factor_registry 状态，避免重复导入的困惑
    reg = await list_factors(db, service_code=service.service_code)
    state = {r.factor_code: r.is_enabled for r in reg}
    for it in items:
        it["imported"] = it["code"] in state
        it["is_enabled"] = bool(state.get(it["code"]))

    total = len(items)
    page = max(page, 1)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def import_factors(
    db: AsyncSession,
    service: CleanerService,
    factor_codes: list[str] | None = None,
    is_enabled: bool = True,
) -> dict:
    """把勾选的因子写入 factor_registry（幂等 upsert）。

    factor_codes 为空表示全量导入。返回 { created, updated, total }。
    """
    remote = await fetch_factors(service)
    pick = set(factor_codes or [])
    chosen = []
    for item in remote:
        meta = _normalize_factor(item)
        if not meta["code"]:
            continue
        if not pick or meta["code"] in pick:
            chosen.append((meta, item))

    now = datetime.now()
    created = updated = 0
    for meta, item in chosen:
        existing = (
            await db.execute(
                select(FactorRegistry).where(
                    FactorRegistry.service_code == service.service_code,
                    FactorRegistry.factor_code == meta["code"],
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.name = meta["name"] or existing.name
            existing.category = meta["category"]
            existing.frequency = meta["frequency"]
            existing.description = meta["description"]
            existing.formula = meta["formula"]
            existing.data_source = meta["data_source"]
            existing.is_enabled = is_enabled
            existing.last_sync = now
            existing.raw = item
            updated += 1
        else:
            db.add(
                FactorRegistry(
                    service_code=service.service_code,
                    factor_code=meta["code"],
                    name=meta["name"] or meta["code"],
                    category=meta["category"],
                    frequency=meta["frequency"],
                    description=meta["description"],
                    formula=meta["formula"],
                    data_source=meta["data_source"],
                    is_enabled=is_enabled,
                    last_sync=now,
                    raw=item,
                )
            )
            created += 1

    await db.commit()
    return {"created": created, "updated": updated, "total": created + updated}


async def sync_factors(db: AsyncSession, service: CleanerService) -> int:
    """把清洗服务因子口径 upsert 进 factor_registry，返回新增/更新条数"""
    remote = await fetch_factors(service)
    synced = 0
    now = datetime.now()
    for item in remote:
        factor_code = item.get("code") or item.get("factor_code")
        if not factor_code:
            continue
        existing = (
            await db.execute(
                select(FactorRegistry).where(
                    FactorRegistry.service_code == service.service_code,
                    FactorRegistry.factor_code == factor_code,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.name = item.get("name", existing.name)
            existing.category = item.get("category")
            existing.frequency = item.get("frequency")
            existing.description = item.get("description")
            existing.formula = item.get("formula")
            existing.data_source = item.get("data_source")
            existing.last_sync = now
            existing.raw = item
        else:
            db.add(
                FactorRegistry(
                    service_code=service.service_code,
                    factor_code=factor_code,
                    name=item.get("name", factor_code),
                    category=item.get("category"),
                    frequency=item.get("frequency"),
                    description=item.get("description"),
                    formula=item.get("formula"),
                    data_source=item.get("data_source"),
                    last_sync=now,
                    raw=item,
                )
            )
        synced += 1
    await db.commit()
    return synced


async def get_service(db: AsyncSession, service_code: str) -> CleanerService | None:
    return (
        await db.execute(select(CleanerService).where(CleanerService.service_code == service_code))
    ).scalar_one_or_none()


async def list_services(db: AsyncSession) -> list[CleanerService]:
    return list((await db.execute(select(CleanerService).order_by(CleanerService.id))).scalars().all())


async def list_factors(
    db: AsyncSession,
    service_code: str | None = None,
    only_enabled: bool = False,
) -> list[FactorRegistry]:
    stmt = select(FactorRegistry)
    if service_code:
        stmt = stmt.where(FactorRegistry.service_code == service_code)
    if only_enabled:
        stmt = stmt.where(FactorRegistry.is_enabled.is_(True))
    return list((await db.execute(stmt.order_by(FactorRegistry.service_code, FactorRegistry.factor_code))).scalars().all())


async def set_factor_enabled(
    db: AsyncSession,
    service_code: str | None,
    factor_codes: list[str] | None,
    is_enabled: bool,
) -> int:
    """批量勾选/取消入库。factor_codes 为空表示操作该 service 下全部"""
    stmt = select(FactorRegistry)
    if service_code:
        stmt = stmt.where(FactorRegistry.service_code == service_code)
    if factor_codes:
        stmt = stmt.where(FactorRegistry.factor_code.in_(factor_codes))

    rows = (await db.execute(stmt)).scalars().all()
    for r in rows:
        r.is_enabled = is_enabled
    await db.commit()
    return len(rows)
