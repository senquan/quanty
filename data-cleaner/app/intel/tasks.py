"""intel 定时任务注册

``register_intel_jobs(scheduler)`` 由 ``app.tasks.scheduler.register_jobs`` 在
``INTEL_ENABLED=true`` 时调用。

设计文档 §4 调度形态：intel 的准实时轮询与 dc 盘后 cron 在**同一 APScheduler 实例**
混跑；重活统一走 ``run_in_executor`` + 硬超时（与 dc 盘后流水线同款隔离）。

两个任务：
- ``intel_rss_poll``    准实时 RSS 摄取（P0）
- ``intel_daily_build`` 周一至周五收盘后构建（P1 抽取 → P2 画像 → P3 因子 → WS 广播），
  原先是**只打日志的空占位**，现已实装，编排在 ``app.intel.daily_build``。
"""
import asyncio
from functools import partial

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def _rss_poll_job() -> None:
    """RSS 轮询（P0）：拉取 → 规范化 → 去重 → 入库 → feed_health。

    重活在 run_in_executor 里同步执行（与 dc 盘后任务同款隔离），
    单源失败不拖垮整轮（失败记 feed_health，详见 service.run_rss_ingest）。
    """
    from app.intel import service as intel_service

    logger.info("定时任务启动: intel RSS 轮询", extra={"task": "intel_rss_poll"})
    try:
        summary = await asyncio.get_event_loop().run_in_executor(
            None, intel_service.run_rss_ingest
        )
        logger.info(
            f"intel RSS 轮询完成 源={summary.get('sources')} 拉取={summary.get('fetched')} "
            f"新增={summary.get('new')} 重复={summary.get('dup')} "
            f"转载={summary.get('reposts')} 失败源={summary.get('failed_sources')}",
            extra={
                "task": "intel_rss_poll",
                **{k: v for k, v in summary.items() if k != "details"},
            },
        )
    except Exception as e:  # 不阻断调度器
        logger.error(f"intel RSS 轮询失败: {e}", extra={"task": "intel_rss_poll"})


async def _daily_intel_build_job() -> None:
    """每日情报构建（周一至周五收盘后）：抽取 → 画像 → 因子 → WS 因子变更广播。

    重活（LLM 抽取 / 画像聚合 / 因子展开）在 executor 里同步执行并带硬超时，
    避免拖慢同实例的 dc 盘后流水线；WS 广播**必须回到事件循环线程**再发。

    任何异常都只记日志，不抛给调度器（一次构建失败不该打死整个 scheduler）。
    """
    from app.intel import daily_build

    logger.info("定时任务启动: intel 每日构建", extra={"task": "intel_daily_build"})
    timeout = max(60, int(getattr(settings, "INTEL_DAILY_BUILD_TIMEOUT_SEC", 3600)))
    try:
        summary = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, partial(daily_build.run_daily_build)
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error(f"intel 每日构建超时（>{timeout}s），本轮放弃",
                     extra={"task": "intel_daily_build"})
        return
    except Exception as e:  # noqa: BLE001
        logger.error(f"intel 每日构建失败: {type(e).__name__}: {e}",
                     extra={"task": "intel_daily_build"})
        return

    status = "ok" if not summary.get("errors") else "partial"
    logger.info(
        f"intel 每日构建完成({status}) {daily_build.format_summary(summary)}",
        extra={
            "task": "intel_daily_build",
            "status": status,
            "cost_cny": summary.get("cost_cny"),
            "errors": summary.get("errors"),
        },
    )

    codes = summary.get("factor_codes") or []
    if not codes:
        logger.info("intel 每日构建：本轮无因子行，跳过 factor_updated 广播",
                    extra={"task": "intel_daily_build"})
        return
    if not getattr(settings, "INTEL_DAILY_BUILD_EMIT_WS", True):
        return
    try:
        from app.ws.events import emit_factor_updated

        emit_factor_updated(codes, reason="intel_daily_build")
        summary["emitted"] = True
        logger.info(f"已广播 factor_updated: {codes}", extra={"task": "intel_daily_build"})
    except Exception as e:  # noqa: BLE001 —— 广播失败不影响已落库的数据
        logger.warning(f"factor_updated 广播失败（数据已落库）: {type(e).__name__}: {e}",
                       extra={"task": "intel_daily_build"})


def register_intel_jobs(scheduler: AsyncIOScheduler) -> None:
    """向全局调度器注册 intel 任务（仅在 INTEL_ENABLED=true 时被调用）"""
    poll_sec = max(30, int(settings.INTEL_RSS_POLL_SEC))
    scheduler.add_job(
        _rss_poll_job,
        trigger="interval",
        seconds=poll_sec,
        id="intel_rss_poll",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    if not getattr(settings, "INTEL_DAILY_BUILD_ENABLED", True):
        logger.info(
            "intel 每日构建任务未注册（INTEL_DAILY_BUILD_ENABLED=false）",
            extra={"task": "intel_register"},
        )
    else:
        hour = int(getattr(settings, "INTEL_DAILY_BUILD_HOUR", 19))
        minute = int(getattr(settings, "INTEL_DAILY_BUILD_MINUTE", 30))
        scheduler.add_job(
            _daily_intel_build_job,
            trigger="cron",
            hour=hour,
            minute=minute,
            day_of_week="mon-fri",
            id="intel_daily_build",
            misfire_grace_time=7200,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
    logger.info(
        "intel 定时任务已注册",
        extra={"task": "intel_register", "poll_sec": poll_sec},
    )
