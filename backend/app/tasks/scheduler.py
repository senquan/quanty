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
    """刷新清洗服务存活状态（**WS 长连接的兜底探针**）。

    启用 WS 且已建立长连接的实例由 `event.status` 推送维持状态（秒级），
    本任务仅对**未连接**的实例兜底轮询，避免 30s 高频无效请求
    （设计文档 §10 Phase 2）。
    """
    from app.models.cleaner import CleanerService
    from sqlalchemy import select

    connected: set[str] = set()
    ws_on = getattr(settings, "WS_ENABLED", False)
    if ws_on and getattr(settings, "CLEANER_POLL_SKIP_CONNECTED", True):
        try:
            from app.ws.registry import registry

            connected = registry.connected_service_codes()
        except Exception as e:  # noqa: BLE001  注册表不可用时不阻塞轮询
            logger.warning("读取 WS 连接注册表失败，按未连接处理: %s", e)

    try:
        async with AsyncSessionLocal() as db:
            svcs = (
                await db.execute(
                    select(CleanerService).where(CleanerService.is_active.is_(True))
                )
            ).scalars().all()
            skipped = 0
            for svc in svcs:
                if svc.service_code in connected:
                    skipped += 1
                    continue
                await gw.poll_qos(svc)
                db.add(svc)
            await db.commit()
            if skipped:
                logger.debug("存活轮询跳过 %s 个已建立长连接的服务", skipped)
    except Exception as e:  # noqa: BLE001  不阻断调度器
        logger.error("清洗服务状态轮询失败: %s", e)


async def _factor_reconcile_job() -> None:
    """因子副本每日对账：发现缺失 / 冗余 / 陈旧（设计文档 §5.3）。

    dc 是因子源(SoT)，backend 为副本；定时全量同步改为"广播触发增量同步"后，
    必须靠对账兜底，否则副本可能**静默漂移**。
    """
    from app.services import factor_replica_sync

    try:
        result = await factor_replica_sync.reconcile()
        logger.info(
            "因子副本对账完成 missing=%s orphaned=%s stale=%s",
            result.get("missing_total"),
            result.get("orphaned_total"),
            result.get("stale_total"),
        )
    except Exception as e:  # noqa: BLE001  不阻断调度器
        logger.error("因子副本对账失败: %s", e)


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


# service_code → 上次告警时刻（防刷屏）
_WS_ALERT_LAST_LOGGED: dict[str, float] = {}


async def _ws_disconnect_watch_job() -> None:
    """WS 断连告警：已注册且活跃的清洗服务长时间未连入则告警。

    背景：dc 的 WS 客户端若静默停止重连，backend 侧只表现为 `connections=0`，
    此前**无人察觉**；而覆盖度检测 / 修复命令通道会随之静默失效
    （2026-09-10 / 09-11 两次线上现象）。本任务是唯一的主动发现手段。
    """
    if not getattr(settings, "WS_ENABLED", False):
        return
    if not getattr(settings, "ENABLE_WS_DISCONNECT_ALERT", True):
        return
    threshold = int(getattr(settings, "WS_DISCONNECT_ALERT_SEC", 300))
    every = int(getattr(settings, "WS_DISCONNECT_ALERT_EVERY_SEC", 600))

    from sqlalchemy import select

    from app.models.cleaner import CleanerService

    try:
        from app.ws.registry import registry as ws_registry
    except Exception as e:  # noqa: BLE001  注册表不可用不阻断调度器
        logger.warning("WS 注册表不可用，跳过断连告警: %s", e)
        return

    try:
        async with AsyncSessionLocal() as db:
            svcs = (
                await db.execute(
                    select(CleanerService).where(CleanerService.is_active.is_(True))
                )
            ).scalars().all()
            codes = [s.service_code for s in svcs]
    except Exception as e:  # noqa: BLE001
        logger.warning("读取清洗服务失败，跳过断连告警: %s", e)
        return

    import time

    now = time.time()
    for a in ws_registry.disconnect_report(codes, threshold):
        code = a["service_code"]
        last = _WS_ALERT_LAST_LOGGED.get(code)
        if last is not None and (now - last) < every:
            continue  # 同一服务按最小间隔重复告警，避免刷屏
        _WS_ALERT_LAST_LOGGED[code] = now
        logger.warning(
            "WS 断连告警：清洗服务 %s 已失联 %.0fs（阈值 %ss，%s）——"
            "覆盖度检测 / 修复命令通道不可用，请检查 dc 进程与 WS 配置",
            code,
            a["down_seconds"],
            threshold,
            "曾连接过" if a["ever_connected"] else "本进程内从未连上",
        )


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
    # 启用 WS 后本任务退化为「未连接实例的兜底探针」，降频到 5min（§10 Phase 2）。
    if getattr(settings, "ENABLE_CLEANER_POLL", True):
        poll_sec = max(5, int(getattr(settings, "CLEANER_POLL_INTERVAL_SEC", 30)))
        if getattr(settings, "WS_ENABLED", False) and getattr(
            settings, "CLEANER_POLL_SKIP_CONNECTED", True
        ):
            poll_sec = max(poll_sec, 300)
        scheduler.add_job(
            _poll_qos_job,
            trigger=IntervalTrigger(seconds=poll_sec, timezone="Asia/Shanghai"),
            id="cleaner_poll",
            misfire_grace_time=60,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    # WS 断连告警：仅在启用 WS 时注册（未启用时没有"应连接"的预期，告警无意义）。
    if getattr(settings, "WS_ENABLED", False) and getattr(
        settings, "ENABLE_WS_DISCONNECT_ALERT", True
    ):
        alert_sec = max(10, int(getattr(settings, "WS_DISCONNECT_ALERT_CHECK_SEC", 60)))
        scheduler.add_job(
            _ws_disconnect_watch_job,
            trigger=IntervalTrigger(seconds=alert_sec, timezone="Asia/Shanghai"),
            id="ws_disconnect_watch",
            misfire_grace_time=60,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    # 因子副本每日对账（根基配套）：比对 dc 因子集合与本地副本。
    # 需配合 ENABLE_FACTOR_SYNC 使用；冗余仅标记告警、不自动删除。
    if getattr(settings, "ENABLE_FACTOR_RECONCILE", False):
        from apscheduler.triggers.cron import CronTrigger as _CronTrigger

        scheduler.add_job(
            _factor_reconcile_job,
            trigger=_CronTrigger(
                day_of_week="mon-fri",
                hour=int(getattr(settings, "FACTOR_RECONCILE_HOUR", 7)),
                minute=int(getattr(settings, "FACTOR_RECONCILE_MINUTE", 30)),
                timezone="Asia/Shanghai",
            ),
            id="factor_reconcile",
            misfire_grace_time=3600,
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
