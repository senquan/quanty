"""QoS 快照构造（供 HTTP /qos 与 WebSocket event.status **共用**）

抽出为独立模块的目的：
backend 原先通过 30s 轮询 `GET /api/v1/qos` 获取本快照，改造后改由 dc 经
WebSocket 主动推送 `event.status`。两条链路必须**口径完全一致**，否则 backend
注册表会出现「轮询与推送数据不一致」的诡异现象。故统一在此构造。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger
from app.factors.registry import list_factors
from app.storage import db

logger = get_logger(__name__)

SERVICE_VERSION = "1.0.0"


async def build_qos_snapshot() -> dict:
    """构造 QoS / 健康快照。

    返回结构与原 `GET /api/v1/qos` 完全一致：
    `{service, status, timestamp, factor_count, pipeline_total,
      pipeline_failed, last_pipeline, system}`

    级别判定：DB 不可达 → degraded；否则 online。
    """
    factor_count = len(list_factors())

    # PG 连通性探测（不写数据，仅 SELECT 1）
    db_status = "ok"
    try:
        async with db.async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - 探测失败即降级，不影响快照返回
        db_status = "error"

    last = await db.get_last_pipeline_run()

    system = {
        "service_name": settings.SERVICE_NAME,
        "version": SERVICE_VERSION,
        "db": db_status,
        "uptime_seconds": round(metrics.uptime_seconds(), 1),
        "factor_count": factor_count,
    }

    # 根据依赖健康度给出级别（主后端据此标注 online / degraded / offline）
    health = "online" if db_status == "ok" else "degraded"
    totals = metrics.pipeline_totals()

    return {
        "service": settings.SERVICE_NAME,
        "status": health,
        "timestamp": datetime.now().isoformat(),
        "factor_count": factor_count,
        "pipeline_total": totals[0],
        "pipeline_failed": totals[1],
        "last_pipeline": last,  # 未运行过则为 None（前端显示 never_run）
        "system": system,
    }
