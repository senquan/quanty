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
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.trading import MODE_PAPER
from app.services import cleaner_gateway as gw
from app.services import portfolio_valuation_service
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


async def _factor_sync_job() -> None:
    """定期刷新已入库因子的口径与效能指标（读本地底册不依赖本任务）。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await gw.sync_all_services(db)
        logger.info("因子同步完成 %s", result)
    except Exception as e:  # noqa: BLE001  不阻断调度器
        logger.error("因子同步失败: %s", e)


async def _poll_qos_job() -> None:
    """每 30s 刷新已登记清洗服务的存活状态，驱动因子可用性（available）实时反映。"""
    from app.models.cleaner import CleanerService
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db:
            svcs = (
                await db.execute(
                    select(CleanerService).where(CleanerService.is_active.is_(True))
                )
            ).scalars().all()
            for svc in svcs:
                await gw.poll_qos(svc)
                db.add(svc)
            await db.commit()
    except Exception as e:  # noqa: BLE001  不阻断调度器
        logger.error("清洗服务状态轮询失败: %s", e)


async def _portfolio_valuation_job() -> None:
    """交易日盘后：从 dc 拉行情 → 更新持仓市值 → 记录组合日快照。"""
    modes = [MODE_PAPER]
    if getattr(settings, "ENABLE_LIVE_TRADING", False):
        modes.append("live")
    for mode in modes:
        try:
            async with AsyncSessionLocal() as db:
                result = await portfolio_valuation_service.run_eod_valuation(db, mode=mode)
            logger.info("盘后估值完成 mode=%s %s", mode, result)
        except Exception as e:  # noqa: BLE001  不阻断调度器
            logger.error("盘后估值失败 mode=%s: %s", mode, e)


def register_jobs() -> None:
    # 各任务按独立开关注册；两个开关都关闭时调度器不启动
    if getattr(settings, "ENABLE_TRADING_SCHEDULER", False):
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

    if getattr(settings, "ENABLE_FACTOR_SYNC", False):
        interval = max(5, int(getattr(settings, "FACTOR_SYNC_INTERVAL_MIN", 60)))
        scheduler.add_job(
            _factor_sync_job,
            trigger=IntervalTrigger(minutes=interval, timezone="Asia/Shanghai"),
            id="factor_sync",
            misfire_grace_time=600,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    # 清洗服务存活轮询：默认开启，使因子可用性随 dc 上下线自动刷新。
    # 即便 ENABLE_TRADING_SCHEDULER / ENABLE_FACTOR_SYNC 都关，本任务也可独立运行。
    if getattr(settings, "ENABLE_CLEANER_POLL", True):
        poll_sec = max(5, int(getattr(settings, "CLEANER_POLL_INTERVAL_SEC", 30)))
        scheduler.add_job(
            _poll_qos_job,
            trigger=IntervalTrigger(seconds=poll_sec, timezone="Asia/Shanghai"),
            id="cleaner_poll",
            misfire_grace_time=60,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    # 组合盘后估值：交易日 15:30 从 dc 拉行情、更新持仓市值、记录日快照。
    # 默认开启；关闭 ENABLE_PORTFOLIO_VALUATION 可跳过。
    if getattr(settings, "ENABLE_PORTFOLIO_VALUATION", True):
        scheduler.add_job(
            _portfolio_valuation_job,
            trigger=CronTrigger(
                day_of_week="mon-fri", hour=15, minute=30, timezone="Asia/Shanghai"
            ),
            id="portfolio_valuation",
            misfire_grace_time=1800,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )


def start_scheduler() -> None:
    register_jobs()
    jobs = [j.id for j in scheduler.get_jobs()]
    if not jobs:
        logger.info("无启用中的定时任务，调度器未启动")
        return
    scheduler.start()
    logger.info("调度器已启动", extra={"status": "scheduler_started", "jobs": jobs})


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
