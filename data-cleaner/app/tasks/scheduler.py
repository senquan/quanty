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


async def _daily_eod_pipeline_job() -> None:
    """每日盘后流水线：增量拉取行情 → 因子库更新 → 因子效能评估。

    三个步骤在同一任务内**顺序执行**（原先是 18:30 拉数据、19:30 算因子两个独立
    定时任务靠时间点错开，数据没拉完就可能开始算因子）。数据源延迟发布时会自动
    等待重试若干轮。
    """
    from app.core.config import settings
    from app.tasks import daily_pipeline as daily_pipeline_task

    source = getattr(settings, "RAW_BACKFILL_SOURCE", "alphafeed")
    logger.info(
        "定时任务启动: 每日盘后流水线",
        extra={"task": "scheduled_eod_pipeline", "source": source},
    )
    try:
        import asyncio

        summary = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: daily_pipeline_task.run_daily_pipeline(source=source),
        )
        logger.info(
            f"定时任务完成 数据 {summary.get('latest_before')} -> {summary.get('latest_after')} "
            f"(推进={summary.get('data_advanced')}) 步骤数={len(summary.get('steps', []))} "
            f"{summary.get('duration_s')}s",
            extra={
                "task": "scheduled_eod_pipeline",
                **{k: v for k, v in summary.items() if k != "steps"},
            },
        )
    except Exception as e:  # 不阻断调度器
        logger.error(f"定时任务失败: {e}", extra={"task": "scheduled_eod_pipeline"})


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

        # 若确实补了数据，因子库与评估要跟着刷新，否则补的数据要等到下一个
        # 交易日盘后才反映到因子上
        if result.get("repair"):
            from app.tasks import factor_build as factor_build_task
            from app.tasks import factor_evaluate as factor_evaluate_task

            fb = await asyncio.get_event_loop().run_in_executor(
                None, factor_build_task.build_factor_library
            )
            ev = await asyncio.get_event_loop().run_in_executor(
                None, factor_evaluate_task.evaluate_all_factors
            )
            logger.info(
                f"补齐后因子刷新完成 因子库={fb.get('files')} 文件 "
                f"评估={ev.get('factors_evaluated')}/{ev.get('factors_total')}",
                extra={
                    "task": "scheduled_verify_backfill",
                    "factor_build": fb.get("status"),
                    "factor_evaluate": ev.get("status"),
                },
            )
    except Exception as e:  # 不阻断调度器
        logger.error(f"覆盖度校验失败: {e}", extra={"task": "scheduled_verify_backfill"})


async def _weekly_metrics_job() -> None:
    """每周定时：用已落库的因子值重算全部因子效能指标（IC/IR/Sharpe/回撤/胜率）"""
    from app.tasks import factor_evaluate as factor_evaluate_task

    logger.info("定时任务启动: 因子效能重算", extra={"task": "scheduled_metrics"})
    try:
        import asyncio

        summary = await asyncio.get_event_loop().run_in_executor(
            None, factor_evaluate_task.evaluate_all_factors
        )
        logger.info(
            f"因子效能重算完成 评估={summary.get('factors_evaluated')}"
            f"/{summary.get('factors_total')} 跳过={summary.get('factors_skipped')} "
            f"{summary.get('duration_s')}s",
            extra={"task": "scheduled_metrics", **summary},
        )
    except Exception as e:  # 不阻断调度器
        logger.error(f"因子效能重算失败: {e}", extra={"task": "scheduled_metrics"})


async def _heartbeat_job() -> None:
    """每 30s: 心跳写入 Redis factor:status（存活探针/实时状态）"""
    from app.storage import cache

    await cache.publish_status({"status": "alive", "ts": datetime.now().isoformat()})


async def _industry_refresh_job() -> None:
    """每日刷新行业分类缓存（供行业中性化）。

    原仅周六刷新 —— 新上市/退市、行业重分类隔夜即生效，中性化与上市天数过滤
    才能及时反映。refresh_industries 本身为「全量拉取 + upsert 幂等」，等价于
    每日增量刷新（仅变化的行被更新）。
    """
    from app.industry import store as industry_store

    logger.info("定时任务启动: 行业分类刷新", extra={"task": "scheduled_industry"})
    try:
        import asyncio

        summary = await asyncio.get_event_loop().run_in_executor(
            None, industry_store.refresh_industries
        )
        logger.info(
            f"行业分类刷新完成: {summary.get('count')} 条，来源 {summary.get('source')}",
            extra={"task": "scheduled_industry", **summary},
        )
    except Exception as e:  # 不阻断调度器
        logger.error(f"行业分类刷新失败: {e}", extra={"task": "scheduled_industry"})


def register_jobs() -> None:
    # 每日盘后流水线：拉数据 → 因子更新 → 效能评估（顺序执行）
    # max_instances=1 + coalesce：任务耗时长（全市场增量可达数小时），
    # 防止上一轮没跑完又被触发，堆积成并发请求打满数据源限频
    scheduler.add_job(
        _daily_eod_pipeline_job,
        trigger="cron",
        hour=18,
        minute=30,
        day_of_week="mon-fri",
        id="daily_eod_pipeline",
        # 服务晚启动（重启/宕机恢复）2 小时内仍补跑一次，避免整天漏数据
        misfire_grace_time=7200,
        max_instances=1,
        coalesce=True,
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
        max_instances=1,
        coalesce=True,
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
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _heartbeat_job,
        trigger="interval",
        seconds=30,
        id="heartbeat",
        replace_existing=True,
    )
    scheduler.add_job(
        _industry_refresh_job,
        trigger="cron",
        day_of_week="mon-fri",
        hour=18,
        minute=40,
        id="industry_refresh",
        misfire_grace_time=7200,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # 注：策略调仓定时任务已迁至 backend（交易中心）承载。
    # data-cleaner 只保留 /strategy/scores（算目标持仓）与 /raw/latest-prices（行情中继）。
    # 启动后若行业表为空，立即补刷一次（避免中性化退化/无数据）
    try:
        from datetime import timedelta

        from apscheduler.triggers.date import DateTrigger

        scheduler.add_job(
            _industry_refresh_job,
            trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=20)),
            id="industry_refresh_bootstrap",
            replace_existing=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"行业刷新启动任务注册失败（可忽略）: {e}")


def start_scheduler() -> None:
    register_jobs()
    scheduler.start()
    logger.info("调度器已启动", extra={"status": "scheduler_started"})


def shutdown_scheduler() -> None:
    scheduler.shutdown(wait=False)
