"""intel 定时任务注册（占位骨架）

``register_intel_jobs(scheduler)`` 由 ``app.tasks.scheduler.register_jobs`` 在
``INTEL_ENABLED=true`` 时调用。本文件仅注册占位任务，业务逻辑在后续阶段填充。

设计文档 §4 调度形态：intel 的准实时轮询与 dc 盘后 cron 在**同一 APScheduler 实例**
混跑；重活统一走 ``run_in_executor`` + 超时（与 dc 盘后流水线同款隔离），避免拖慢盘后。
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def _rss_poll_placeholder() -> None:
    """RSS 轮询占位（P0 实现）。

    TODO(P0): 遍历 ``intel.sources`` 中 enabled 的 RSS 源，拉取→解析→入库；
    参考 Vibe-Research ``sources/rss.py`` 的并发 + 单源失败隔离模型。
    """
    logger.info("intel RSS 轮询占位任务触发（未实现）", extra={"task": "intel_rss_poll"})


async def _daily_intel_build_placeholder() -> None:
    """每日情报构建占位（P2/P3 实现）：画像 + 因子化 + emit_factor_updated。

    TODO(P2/P3): 跑画像、产 ``INTL_*`` 因子，调用
      ``app.ws.events.emit_factor_updated(["INTL_*"], reason="intel_build")``
    复用 dc→backend 的 WS 因子副本同步通道（设计文档 §1）。
    """
    logger.info("intel 每日构建占位任务触发（未实现）", extra={"task": "intel_daily_build"})


def register_intel_jobs(scheduler: AsyncIOScheduler) -> None:
    """向全局调度器注册 intel 占位任务（仅在 INTEL_ENABLED=true 时被调用）"""
    poll_sec = max(30, int(settings.INTEL_RSS_POLL_SEC))
    scheduler.add_job(
        _rss_poll_placeholder,
        trigger="interval",
        seconds=poll_sec,
        id="intel_rss_poll",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _daily_intel_build_placeholder,
        trigger="cron",
        hour=19,
        minute=30,
        day_of_week="mon-fri",
        id="intel_daily_build",
        misfire_grace_time=7200,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info(
        "intel 定时任务已注册（占位）",
        extra={"task": "intel_register", "poll_sec": poll_sec},
    )
