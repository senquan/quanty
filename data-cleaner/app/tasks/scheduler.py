"""定时任务调度（APScheduler）

- 每个交易日 18:00: 触发一次全量清洗+因子计算流水线（由 pipeline 路由逻辑复用）
- 每周六 09:00: 重算因子效能指标（更长回看窗口）
- 每 30s: 心跳写入 Redis factor:status（容器存活探针可由此外部读取）
"""
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.logging import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def _daily_pipeline_job() -> None:
    """每日定时：拉取默认标的并跑清洗+因子计算。

    实现复用 app.api.v1.pipeline 的 run 逻辑，避免重复。
    """
    from app.api.v1 import pipeline as pipeline_api

    logger.info("定时任务启动: 日线清洗+因子计算", extra={"task": "scheduled_pipeline"})
    try:
        # 复用路由层的执行函数（同步封装）
        await pipeline_api.run_default_pipeline()
        logger.info("定时任务完成", extra={"task": "scheduled_pipeline"})
    except Exception as e:  # 不阻断调度器
        logger.error(f"定时任务失败: {e}", extra={"task": "scheduled_pipeline"})


async def _weekly_metrics_job() -> None:
    """每周定时：重算全部因子效能指标（示意，依赖因子值落库）"""
    logger.info("定时任务启动: 因子效能重算", extra={"task": "scheduled_metrics"})


async def _heartbeat_job() -> None:
    """每 30s: 心跳写入 Redis factor:status（存活探针/实时状态）"""
    from app.storage import cache

    await cache.publish_status({"status": "alive", "ts": datetime.now().isoformat()})


def register_jobs() -> None:
    scheduler.add_job(
        _daily_pipeline_job,
        trigger="cron",
        hour=18,
        minute=0,
        id="daily_pipeline",
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly_metrics_job,
        trigger="cron",
        day_of_week="sat",
        hour=9,
        minute=0,
        id="weekly_metrics",
        replace_existing=True,
    )
    scheduler.add_job(
        _heartbeat_job,
        trigger="interval",
        seconds=30,
        id="heartbeat",
        replace_existing=True,
    )


def start_scheduler() -> None:
    register_jobs()
    scheduler.start()
    logger.info("调度器已启动", extra={"status": "scheduler_started"})


def shutdown_scheduler() -> None:
    scheduler.shutdown(wait=False)
