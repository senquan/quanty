"""交易定时任务调度（APScheduler）

- 交易日 9-15 点每 15 分钟：扫描启用策略，到点则调仓（自动下单）

部署约束：backend 若多副本部署，**只能在一个实例上开启**
（`ENABLE_TRADING_SCHEDULER=true`），否则会重复触发。
即便重复触发也有兜底：调仓记录带 `(strategy_id, rebalance_date, mode)` 唯一约束，
同一策略同一天同一模式只会有一条记录，第二次执行在 `rebalance_one` 的防重处即跳过。
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services import rebalance_service

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def _strategy_rebalance_job() -> None:
    mode = getattr(settings, "REBALANCE_MODE", "paper")
    try:
        async with AsyncSessionLocal() as db:
            await rebalance_service.scan_and_rebalance(db, mode=mode)
    except Exception as e:  # noqa: BLE001  不阻断调度器
        logger.error("策略调仓扫描失败: %s", e)


def register_jobs() -> None:
    scheduler.add_job(
        _strategy_rebalance_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="*/15",
            timezone="Asia/Shanghai",
        ),
        id="strategy_rebalance",
        misfire_grace_time=600,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )


def start_scheduler() -> None:
    if not getattr(settings, "ENABLE_TRADING_SCHEDULER", False):
        logger.info("交易调度器未启用（ENABLE_TRADING_SCHEDULER=false）")
        return
    register_jobs()
    scheduler.start()
    logger.info("交易调度器已启动", extra={"status": "scheduler_started"})


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("交易调度器已停止")
