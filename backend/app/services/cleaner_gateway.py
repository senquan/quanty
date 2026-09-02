"""清洗服务网关客户端（阶段 B）

负责与主后端登记的 data-cleaner 实例通信：
- test_connection / poll_qos: 调用清洗服务 `GET /api/v1/qos`
- fetch_factors:          调用清洗服务 `GET /api/v1/factor`（需 X-API-Key）
- sync_factors:           把因子口径 upsert 进 `factor_registry`（按 service_code+factor_code 幂等）

所有对外请求统一超时（默认 5s），失败不抛异常，交由调用层决定状态标注。
"""
import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, engine
from app.models.cleaner import CleanerService, FactorRegistry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
# 同步（含 include_metrics）耗时长于普通探测，单独放宽
SYNC_TIMEOUT = 30.0


# 仅对本次新增的两张表做幂等建表（不干预现有 alembic 管理的表）
_INIT_LOCK = None

# 增量列变更：create_all 不补列，故新增列在此登记（幂等，可重复执行）
_DDL_MIGRATIONS = (
    # 2026-09-01 因子底册归属 backend：落库最新一期效能指标
    "ALTER TABLE factor_registry "
    "ADD COLUMN IF NOT EXISTS metrics JSON, "
    "ADD COLUMN IF NOT EXISTS metrics_synced_at TIMESTAMP",
)


async def ensure_cleaner_tables() -> None:
    """幂等建表：只在首次调用时执行一次（基于 async engine）

    注意：`create_all` 只会创建缺失的表，**不会给已存在的表补列**，
    故后续新增的列须在此显式 ALTER（同样幂等）。
    """
    global _INIT_LOCK
    if _INIT_LOCK is None:
        _INIT_LOCK = asyncio.Lock()
    async with _INIT_LOCK:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[CleanerService.__table__, FactorRegistry.__table__],
            )
            for ddl in _DDL_MIGRATIONS:
                await conn.execute(text(ddl))


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
    include_metrics: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """从清洗服务拉取因子列表（受保护接口，需 key）

    data-cleaner 的 GET /api/v1/factor 支持 category / search 过滤，但不支持分页，
    故过滤交由远端、分页在本层做。

    include_metrics=True 时由 dc 一次性批量挂载效能指标（dc 侧是单次批量查询，
    不是逐因子回源），拉取较慢，调用方应放宽 timeout。
    """
    params = {k: v for k, v in (("category", category), ("search", search)) if v}
    if include_metrics:
        params["include_metrics"] = "true"
    query = urlencode(params)
    path = f"/api/v1/factor?{query}" if query else "/api/v1/factor"
    data = await _get_json(service.base_url, path, service.api_key, timeout=timeout)
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
    """刷新**已入库**因子的口径与效能指标，返回更新条数。

    入库语义（见 docs/plans/2026-09-01.factor-registry-backend-owned.md §1.1）：
    - 入库 = 在 backend 的 factor_registry 中注册（import_factors）；
    - 底册只显示已入库的，故本函数**只更新已存在的记录，不新增未入库因子**；
    - dc 侧已删除的因子，保留本地记录（不误删已入库因子，由人工确认后处理）。

    dc 不可达时抛异常由调用层处理，本地旧数据保持不变。
    """
    remote = await fetch_factors(service, include_metrics=True, timeout=SYNC_TIMEOUT)
    by_code: dict[str, dict] = {}
    for item in remote:
        code = item.get("code") or item.get("factor_code")
        if code:
            by_code[code] = item

    rows = (
        await db.execute(
            select(FactorRegistry).where(
                FactorRegistry.service_code == service.service_code
            )
        )
    ).scalars().all()

    now = datetime.now()
    synced = 0
    for row in rows:
        item = by_code.get(row.factor_code)
        if not item:
            continue
        meta = _normalize_factor(item)
        row.name = meta["name"] or row.name
        row.category = meta["category"]
        row.frequency = meta["frequency"]
        row.description = meta["description"]
        row.formula = meta["formula"]
        row.data_source = meta["data_source"]
        row.raw = item
        row.metrics = item.get("metrics")
        row.metrics_synced_at = now
        row.last_sync = now
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


# --------------------------------------------------------------------------- #
# 因子底册（backend 持有）
# --------------------------------------------------------------------------- #

#: dc 实例状态 -> 因子可用性。"dc 连接状态就是因子可用状态"
_AVAILABLE_STATUSES = {"online", "degraded"}


def _to_catalog_item(row: FactorRegistry, service_status: str) -> dict:
    """registry 行 -> 前端 FactorDefinition 口径。

    字段名须与 data-cleaner 的 /api/v1/factor 保持一致（code / data_sources[] /
    metrics …），否则前端因子页会拿到 undefined。
    """
    raw = row.raw or {}
    data_source = row.data_source
    if data_source:
        data_sources = [s for s in data_source.split(",") if s]
    else:
        data_sources = raw.get("data_sources") or None
    return {
        "code": row.factor_code,
        "name": row.name,
        "category": row.category,
        "frequency": row.frequency,
        "formula": row.formula,
        "data_sources": data_sources,
        "author": raw.get("author"),
        "status": raw.get("status"),
        "created_at": raw.get("created_at")
        or (row.created_at.isoformat() if row.created_at else None),
        "description": row.description,
        "metrics": row.metrics,
        # 归属 dc 源 + 可用性（由 dc 连接状态推导）
        "service_code": row.service_code,
        "service_status": service_status,
        "available": service_status in _AVAILABLE_STATUSES,
        "is_enabled": row.is_enabled,
        "last_sync": row.last_sync.isoformat() if row.last_sync else None,
    }


async def list_catalog_factors(
    db: AsyncSession,
    *,
    category: str | None = None,
    search: str | None = None,
    with_metrics: bool = True,
) -> list[dict]:
    """因子底册：只返回**已入库**（is_enabled=True）的因子。

    数据来自本地 factor_registry，不回源 dc —— dc 宕机时仍能返回，
    只是 available=False。
    """
    services = (await db.execute(select(CleanerService))).scalars().all()
    status_map = {s.service_code: (s.status or "unknown") for s in services}

    stmt = select(FactorRegistry).where(FactorRegistry.is_enabled.is_(True))
    if category:
        stmt = stmt.where(FactorRegistry.category == category)
    if search:
        kw = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(FactorRegistry.factor_code).like(kw),
                func.lower(FactorRegistry.name).like(kw),
            )
        )
    rows = (
        await db.execute(
            stmt.order_by(FactorRegistry.service_code, FactorRegistry.factor_code)
        )
    ).scalars().all()

    items = [
        _to_catalog_item(r, status_map.get(r.service_code, "unknown")) for r in rows
    ]
    if not with_metrics:
        for it in items:
            it.pop("metrics", None)
    return items


async def list_catalog_services(db: AsyncSession) -> list[dict]:
    """底册关联的 dc 源及其连接状态（供前端提示"dc 离线"）。"""
    services = (
        await db.execute(select(CleanerService).order_by(CleanerService.service_code))
    ).scalars().all()
    return [
        {
            "service_code": s.service_code,
            "name": s.name,
            "base_url": s.base_url,
            "status": s.status,
            "last_heartbeat": (
                s.last_heartbeat.isoformat() if s.last_heartbeat else None
            ),
            "available": (s.status or "unknown") in _AVAILABLE_STATUSES,
        }
        for s in services
    ]


async def remove_registry_factor(
    db: AsyncSession, service_code: str, factor_code: str
) -> bool:
    """从本地底册移除（删除因子后同步，保持底册与 dc 一致）。"""
    row = (
        await db.execute(
            select(FactorRegistry).where(
                FactorRegistry.service_code == service_code,
                FactorRegistry.factor_code == factor_code,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def sync_all_services(db: AsyncSession) -> dict:
    """对所有启用中的 dc 实例执行因子同步，返回 {service_code: 更新条数|错误}。"""
    services = (
        await db.execute(
            select(CleanerService).where(CleanerService.is_active.is_(True))
        )
    ).scalars().all()

    result: dict[str, Any] = {}
    for svc in services:
        try:
            await poll_qos(svc)
            if (svc.status or "") not in _AVAILABLE_STATUSES:
                result[svc.service_code] = f"skipped: status={svc.status}"
                continue
            result[svc.service_code] = await sync_factors(db, svc)
        except Exception as e:  # noqa: BLE001  单个源失败不影响其他源
            logger.warning("因子同步失败 %s: %s", svc.service_code, e)
            result[svc.service_code] = f"error: {e}"
        finally:
            await db.commit()
    return result


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
