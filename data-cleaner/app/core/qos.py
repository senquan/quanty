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


def _scheduler_state() -> dict:
    """运行中进程的调度器状态（D-4 取证用）。

    为什么必须由进程自己报：静态 import 能拿到 `_daily_intel_build_job`
    **不代表**运行中的进程加载的是这份代码 —— 进程若早于代码 mtime 启动，
    跑的还是旧的空占位。此前只能靠"进程启动时间 vs 文件 mtime"推断，
    或翻日志找 `Added job`，而日志会被重启覆盖（2026-09-10 D-4 卡在这里）。

    现在直接报 `func_ref`：一眼看出运行进程里绑的到底是真实现还是占位。
    """
    try:
        from app.tasks.scheduler import scheduler
    except Exception as e:  # noqa: BLE001 - 未启用调度器时不该拖垮快照
        return {"enabled": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    try:
        if not getattr(scheduler, "running", False):
            return {"enabled": False, "reason": "scheduler 未启动"}
        jobs = []
        for j in scheduler.get_jobs():
            nxt = getattr(j, "next_run_time", None)
            jobs.append({
                "id": j.id,
                # func_ref 形如 "app.intel.tasks:_daily_intel_build_job"
                "func_ref": getattr(j, "func_ref", None),
                "next_run": nxt.isoformat() if nxt else None,
            })
        return {"enabled": True, "running": True, "job_count": len(jobs), "jobs": jobs}
    except Exception as e:  # noqa: BLE001
        return {"enabled": True, "running": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


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

    # WS 长连接状态：此前只能去 backend 的 /ws/status 看，dc 自身不暴露，
    # 导致"服务健康但长连接已失效"完全不可见（2026-09-10/09-11 两次现象）。
    try:
        from app.ws import events as ws_events

        ws_state = ws_events.stats()
    except Exception as e:  # noqa: BLE001 - 探测失败不降级整个快照
        ws_state = {"enabled": False, "error": str(e)[:120]}

    system = {
        "service_name": settings.SERVICE_NAME,
        "version": SERVICE_VERSION,
        "db": db_status,
        "uptime_seconds": round(metrics.uptime_seconds(), 1),
        "factor_count": factor_count,
        "ws": ws_state,
        "scheduler": _scheduler_state(),
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
