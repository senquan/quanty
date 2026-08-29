"""QoS / 状态自检接口（阶段 A）

供主后端 registry 周期性轮询，判断清洗服务是否在线：
- 在线 (online)   : 接口正常返回且依赖可用
- 离线 (offline)  : 网络不通 / 连接拒绝（由调用方判定）
- 降级 (degraded) : 接口返回但 DB 不可达（本服务依赖 PG）

注意：本接口为「开放受监控」，不挂 X-API-Key 依赖，便于监控组件轮询。
"""
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.core import metrics
from app.core.config import settings
from app.factors.registry import list_factors
from app.storage import db

router = APIRouter(prefix="/qos", tags=["qos"])


@router.get("")
async def qos():
    factor_count = len(list_factors())

    # PG 连通性探测（不写数据，仅 SELECT 1）
    db_status = "ok"
    try:
        async with db.async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    last = await db.get_last_pipeline_run()

    system = {
        "service_name": settings.SERVICE_NAME,
        "version": "1.0.0",
        "db": db_status,
        "uptime_seconds": round(metrics.uptime_seconds(), 1),
        "factor_count": factor_count,
    }

    # 根据依赖健康度给出级别（主后端据此标注 online / degraded / offline）
    health = "online" if db_status == "ok" else "degraded"

    return {
        "service": settings.SERVICE_NAME,
        "status": health,                  # online | degraded
        "timestamp": datetime.now().isoformat(),
        "factor_count": factor_count,
        "pipeline_total": (pt := metrics.pipeline_totals())[0],
        "pipeline_failed": pt[1],
        "last_pipeline": last,             # 未运行过则为 None（前端显示 never_run）
        "system": system,
    }
