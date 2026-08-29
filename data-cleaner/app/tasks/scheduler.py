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


async def _daily_raw_backfill_job() -> None:
    """每日定时：增量更新全 A 股日线历史（写入 factor.raw_bars / parquet）。

    复用 app.tasks.backfill.backfill_universe（同步，包在 executor 里跑，避免阻塞事件循环）。
    """
    from app.core.config import settings
    from app.tasks import backfill as backfill_task

    source = getattr(settings, "RAW_BACKFILL_SOURCE", "alphafeed")
    logger.info(
        "定时任务启动: 全 A 股日线增量更新",
        extra={"task": "scheduled_raw_backfill", "source": source},
    )
    try:
        import asyncio

        summary = await asyncio.get_event_loop().run_in_executor(
            None, lambda: backfill_task.backfill_universe(source=source, full=False)
        )
        logger.info(
            f"定时任务完成 ok={summary.get('ok')} empty={summary.get('empty')} "
            f"skip={summary.get('skip')} error={summary.get('error')} / total={summary.get('total')}",
            extra={"task": "scheduled_raw_backfill", **summary},
        )
    except Exception as e:  # 不阻断调度器
        logger.error(f"定时任务失败: {e}", extra={"task": "scheduled_raw_backfill"})


async def _daily_verify_backfill_job() -> None:
    """次日上午校验最近交易日覆盖度，不足则自动跑一轮增量补齐。

    覆盖两类漏数据：
    - 昨日 18:30 任务因限频只更新了部分标的
    - 服务宕机导致定时任务整个没跑
    """
    from app.core.config import settings
    from app.tasks import backfill as backfill_task

    source = getattr(settings, "RAW_BACKFILL_SOURCE", "alphafeed")
    logger.info(
        "定时任务启动: 日线覆盖度校验",
        extra={"task": "scheduled_verify_backfill", "source": source},
    )
    try:
        import asyncio

        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: backfill_task.verify_and_repair(source=source)
        )
        logger.info(
            f"覆盖度校验结果 latest={result.get('latest')}({result.get('latest_count')}) "
            f"prev={result.get('prev')}({result.get('prev_count')}) ratio={result.get('ratio')} "
            f"repair={result.get('repair')}",
            extra={"task": "scheduled_verify_backfill", **result},
        )
    except Exception as e:  # 不阻断调度器
        logger.error(f"覆盖度校验失败: {e}", extra={"task": "scheduled_verify_backfill"})


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
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        _daily_raw_backfill_job,
        trigger="cron",
        hour=18,
        minute=30,
        day_of_week="mon-fri",
        id="daily_raw_backfill",
        # 服务晚启动（重启/宕机恢复）2 小时内仍补跑一次，避免整天漏数据
        misfire_grace_time=7200,
        replace_existing=True,
    )
    scheduler.add_job(
        _daily_verify_backfill_job,
        trigger="cron",
        hour=8,
        minute=30,
        day_of_week="mon-sat",
        id="daily_verify_backfill",
        misfire_grace_time=7200,
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly_metrics_job,
        trigger="cron",
        day_of_week="sat",
        hour=9,
        minute=0,
        id="weekly_metrics",
        misfire_grace_time=3600,
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
