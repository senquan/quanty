"""因子副本同步（**根基**：dc 是源 SoT，backend 是副本）

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §5）：

- **所有权**：因子在 dc 计算、落 parquet、经 `/api/v1/factor` 暴露；
  backend 的 `factor_registry` 只是**副本**，存在意义是前端查看时不打 dc。
- **同步通道**：`event.factor.updated` 是副本同步的触发源，
  **只传 code + 版本，不传矩阵**；真实数据由后台经 HTTP 增量拉取（§5.2）。
- **一致性目标（已确认）**：**最终一致** —— 广播触发增量同步 + **每日对账**。
- **对账处置（已确认）**：缺失 → 补入并告警；冗余 → **仅标记 orphaned + 告警，
  不自动清理**（避免误删已启用因子，§5.3）。
- **副本内容边界（已确认）**：元信息 + 最新一期指标快照；
  **不含**因子 bulk、**不含**时间序列（本次不做序列，折线图维持降级，§5.1）。

并发控制：同一 service_code 的同步串行化 + 合并（同步期间到达的变更
在本次结束后再跑一轮），避免因子构建期间的广播风暴打爆 dc。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.cleaner import CleanerService, FactorRegistry
from app.services import cleaner_gateway as gw

logger = logging.getLogger(__name__)

#: service_code -> 待同步的因子 code（stale 标记）
_stale: dict[str, set[str]] = {}

#: service_code -> 正在执行的同步任务
_tasks: dict[str, asyncio.Task] = {}

#: 同步期间又到达变更 → 结束后再跑一轮
_rerun: set[str] = set()

_lock = asyncio.Lock()


def stale_snapshot() -> dict[str, list[str]]:
    """当前未完成的 stale 标记（排查用）。"""
    return {k: sorted(v) for k, v in _stale.items() if v}


async def mark_stale(service_code: str, codes: Iterable[str]) -> None:
    """标记副本为待同步（幂等累积）。"""
    if not service_code:
        return
    async with _lock:
        bucket = _stale.setdefault(service_code, set())
        for c in codes:
            if c:
                bucket.add(str(c))
    logger.info(
        "因子副本标记待同步",
        extra={"service_code": service_code, "codes": len(list(codes) or [])},
    )


async def sync_service(service_code: str) -> dict[str, Any]:
    """触发某服务的增量同步（合并并发、失败不抛）。

    Returns:
        `{"status": ..., "synced": n}`；已有同步在跑时返回 `coalesced`。
    """
    if not service_code:
        return {"status": "skipped", "reason": "missing service_code"}

    async with _lock:
        existing = _tasks.get(service_code)
        if existing is not None and not existing.done():
            _rerun.add(service_code)
            return {"status": "coalesced", "service_code": service_code}
        task = asyncio.create_task(
            _do_sync(service_code), name=f"factor-sync-{service_code}"
        )
        _tasks[service_code] = task

    try:
        result = await task
    except Exception as e:  # noqa: BLE001 - 同步失败不得影响 WS 会话
        logger.error(f"因子副本同步失败 service_code={service_code}: {e}")
        result = {"status": "error", "service_code": service_code, "error": str(e)}
    finally:
        async with _lock:
            _tasks.pop(service_code, None)
            need_rerun = service_code in _rerun
            _rerun.discard(service_code)

    # 同步期间又有变更 → 再跑一轮（保证最终一致）
    if need_rerun:
        logger.info(f"同步期间有新变更，补跑一轮 service_code={service_code}")
        await sync_service(service_code)

    return result


async def _do_sync(service_code: str) -> dict[str, Any]:
    """实际执行一次增量同步，并在成功后清理 stale 标记。"""
    started = datetime.now()
    async with AsyncSessionLocal() as db:
        svc = (
            await db.execute(
                select(CleanerService).where(
                    CleanerService.service_code == service_code
                )
            )
        ).scalar_one_or_none()
        if svc is None:
            return {"status": "unknown_service", "service_code": service_code}

        synced = await gw.sync_factors(db, svc)

    async with _lock:
        _stale.pop(service_code, None)

    duration = (datetime.now() - started).total_seconds()
    logger.info(
        "因子副本同步完成",
        extra={
            "service_code": service_code,
            "synced": synced,
            "duration_s": round(duration, 2),
        },
    )
    return {"status": "ok", "service_code": service_code, "synced": synced}


# ---------------- 每日对账 ----------------


async def reconcile(service_code: str | None = None) -> dict[str, Any]:
    """对账：比对 dc 侧因子集合与本地副本，发现缺失 / 冗余 / 陈旧。

    处置口径（评审已确认）：
    - 缺失（dc 有、本地无）→ **补入**并告警；
    - 冗余（本地有、dc 已删）→ **仅标记 `orphaned` + 告警，不自动清理**；
    - 陈旧（指标落后于 dc 最新 as_of）→ 触发重同步。

    Args:
        service_code: 指定服务；None 表示对所有 active 服务对账。
    """
    summary: dict[str, Any] = {
        "status": "ok",
        "services": [],
        "missing_total": 0,
        "orphaned_total": 0,
        "stale_total": 0,
        "errors": [],
    }

    async with AsyncSessionLocal() as db:
        stmt = select(CleanerService)
        if service_code:
            stmt = stmt.where(CleanerService.service_code == service_code)
        else:
            stmt = stmt.where(CleanerService.is_active.is_(True))
        services = list((await db.execute(stmt)).scalars().all())

        for svc in services:
            try:
                result = await _reconcile_service(db, svc)
            except Exception as e:  # noqa: BLE001
                logger.error(f"对账失败 service_code={svc.service_code}: {e}")
                summary["errors"].append({"service_code": svc.service_code, "error": str(e)})
                continue
            summary["services"].append(result)
            summary["missing_total"] += result["missing"]
            summary["orphaned_total"] += result["orphaned"]
            summary["stale_total"] += result["stale"]

    if summary["missing_total"] or summary["orphaned_total"]:
        logger.warning(
            "因子副本对账发现差异",
            extra={
                "missing": summary["missing_total"],
                "orphaned": summary["orphaned_total"],
                "stale": summary["stale_total"],
            },
        )
    else:
        logger.info("因子副本对账一致")
    return summary


async def _reconcile_service(db, svc: CleanerService) -> dict[str, Any]:
    """单个服务的对账。"""
    # 1) dc 侧（源）因子集合
    try:
        remote = await gw.fetch_factors(
            svc, include_metrics=True, timeout=gw.SYNC_TIMEOUT
        )
    except Exception as e:  # noqa: BLE001
        return {
            "service_code": svc.service_code,
            "status": "unreachable",
            "error": str(e),
            "missing": 0,
            "orphaned": 0,
            "stale": 0,
        }

    remote_by_code: dict[str, dict] = {}
    remote_as_of: dict[str, str] = {}
    for item in remote:
        code = item.get("code") or item.get("factor_code")
        if not code:
            continue
        remote_by_code[str(code)] = item
        metrics = item.get("metrics") or {}
        as_of = metrics.get("as_of_date") if isinstance(metrics, dict) else None
        if as_of:
            remote_as_of[str(code)] = str(as_of)

    # 2) 本地副本
    rows = list(
        (
            await db.execute(
                select(FactorRegistry).where(
                    FactorRegistry.service_code == svc.service_code
                )
            )
        ).scalars().all()
    )
    local_by_code = {r.factor_code: r for r in rows}

    missing = [c for c in remote_by_code if c not in local_by_code]
    orphaned = [c for c in local_by_code if c not in remote_by_code]

    # 3) 陈旧：本地 metrics 的 as_of 落后于 dc
    stale: list[str] = []
    for code, row in local_by_code.items():
        remote_date = remote_as_of.get(code)
        if not remote_date:
            continue
        local_date = None
        if isinstance(row.metrics, dict):
            local_date = row.metrics.get("as_of_date")
        if local_date is None or str(local_date) != remote_date:
            stale.append(code)

    # 处置：缺失补入（复用 import 的口径），冗余仅告警
    if missing:
        logger.warning(
            "副本缺失因子，尝试补入",
            extra={"service_code": svc.service_code, "missing": missing[:20]},
        )
        try:
            # 注意：补入时**不自动启用**（is_enabled=False）。
            # 因子底册只展示 is_enabled=True 的因子，若自动启用会让 dc 侧新增因子
            # 静默出现在前端，故补入副本后仍需人工确认启用（与"冗余不自动删"同口径）。
            added = await gw.import_factors(
                db, svc, factor_codes=missing, is_enabled=False
            )
            # 注意：不可把 added 直接展开进 extra —— import_factors 返回的
            # {"created", "updated", ...} 中 `created` 是 logging.LogRecord 的**保留属性**，
            # 展开会抛 "Attempt to overwrite 'created' in LogRecord"，导致补入成功却记成失败。
            logger.warning(
                "副本补入完成（未自动启用，需人工确认）",
                extra={"service_code": svc.service_code, "import_result": added},
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"副本补入失败 service_code={svc.service_code}: {e}")

    if orphaned:
        # 已确认：不自动删除，仅告警（避免误删已启用因子）
        logger.warning(
            "副本存在冗余因子（未自动删除，请人工确认）",
            extra={"service_code": svc.service_code, "orphaned": orphaned[:20]},
        )
        for code in orphaned:
            row = local_by_code[code]
            raw = dict(row.raw or {})
            raw["orphaned"] = True
            raw["orphaned_at"] = datetime.now().isoformat()
            row.raw = raw
        await db.commit()

    if stale:
        await mark_stale(svc.service_code, stale)

    return {
        "service_code": svc.service_code,
        "status": "ok",
        "remote_count": len(remote_by_code),
        "local_count": len(local_by_code),
        "missing": len(missing),
        "orphaned": len(orphaned),
        "stale": len(stale),
    }
