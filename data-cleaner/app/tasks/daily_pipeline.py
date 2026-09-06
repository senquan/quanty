"""每日盘后流水线：拉取最新行情 → 因子库更新 → 因子效能评估

设计要点：
- 三个步骤**顺序执行**而非靠固定时间点错开，避免"数据还没拉完就开始算因子"
- 数据源常在收盘后延迟发布：若拉完后最新交易日没有推进，等待一段时间后重试
- 任一步骤失败不阻断后续（评估依赖因子库，因子库依赖行情），
  但会把每步结果记录到 steps 里，便于事后定位

用法：
    python run_daily_pipeline.py [--source alphafeed] [--wait-rounds 2] [--wait-minutes 15]
"""
import time
from datetime import datetime

from app.core.logging import get_logger
from app.storage.raw_store import repository
from app.tasks import backfill as backfill_task
from app.tasks import factor_build as factor_build_task
from app.tasks import factor_evaluate as factor_evaluate_task

logger = get_logger(__name__)

DEFAULT_WAIT_ROUNDS = 2      # 首次拉取未推进后，最多再尝试的次数
DEFAULT_WAIT_MINUTES = 15    # 每次重试前的等待分钟数


def market_latest_date() -> str | None:
    """当前因子库中最新交易日"""
    cov = repository.latest_day_coverage(days=1)
    return cov[0][0] if cov else None


def is_trading_day(day: datetime | None = None) -> bool:
    """粗粒度交易日判断：排除周六周日。

    A 股法定节假日不在此列——那种情况下拉取只会拿到空结果，
    代价等同一次全市场遍历，不会造成错误数据。
    """
    day = day or datetime.now()
    return day.weekday() < 5


def run_daily_pipeline(
    source: str = "alphafeed",
    wait_rounds: int = DEFAULT_WAIT_ROUNDS,
    wait_minutes: int = DEFAULT_WAIT_MINUTES,
    symbols: list[str] | None = None,
    force: bool = False,
) -> dict:
    """执行每日盘后流水线，返回分步汇总。

    :param symbols: 限定拉取的标的（调试用）；None 表示全市场
    :param force: 非交易日也强制执行
    """
    t0 = time.time()
    steps: list[dict] = []
    before = market_latest_date()

    if not force and not is_trading_day():
        # 周末没有新收盘数据，若照常遍历全市场只会拿到空结果并打满数据源限频
        summary = {
            "status": "skipped",
            "reason": "非交易日（周末），无需更新",
            "latest_before": before,
            "steps": steps,
            "duration_s": round(time.time() - t0, 1),
        }
        logger.info(
            "非交易日，跳过每日盘后流水线",
            extra={"task": "daily_pipeline", **summary},
        )
        return summary
    logger.info(
        "每日盘后流水线开始",
        extra={"task": "daily_pipeline", "latest_before": before},
    )
    # WS 进度推送（未启用时为 no-op，绝不阻断流水线）
    from app.ws import events as ws_events

    ws_events.emit_pipeline(
        "started", task="eod_pipeline", detail={"latest_before": before}
    )

    # ---- 1) 增量拉取最新行情 ----
    data_after = before
    rounds = max(1, wait_rounds)
    for i in range(rounds):
        result = backfill_task.backfill_universe(
            source=source, symbols=symbols, full=False
        )
        data_after = market_latest_date()
        steps.append(
            {
                "step": "backfill",
                "round": i + 1,
                "total": result.get("total"),
                "ok": result.get("ok"),
                "empty": result.get("empty"),
                "skip": result.get("skip"),
                "error": result.get("error"),
                "latest": data_after,
            }
        )
        if data_after and data_after != before:
            break
        if i < rounds - 1:
            logger.info(
                f"最新交易日仍为 {data_after}，{wait_minutes} 分钟后重试",
                extra={"task": "daily_pipeline", "step": "backfill"},
            )
            time.sleep(wait_minutes * 60)

    advanced = bool(data_after and data_after != before)

    # ---- 1.5) 基础数据刷新（估值/换手/市值/财报/涨跌停/停牌）----
    # 失败不阻断：factor_build 对缺列因子仍按旧逻辑给 NaN
    try:
        from app.tasks import fundamental_refresh as fundamental_refresh_task

        fund = fundamental_refresh_task.refresh_fundamental(trade_date=data_after)
        steps.append(
            {
                "step": "fundamental",
                "status": fund.get("status"),
                "daily_basic": (fund.get("daily") or {}).get("daily_basic"),
                "trading_status": (fund.get("daily") or {}).get("trading_status"),
                "finance_rows": (fund.get("growth") or {}).get("rows"),
            }
        )
    except Exception as e:  # noqa: BLE001
        steps.append({"step": "fundamental", "status": "error", "reason": str(e)[:120]})
        logger.warning(f"基础数据刷新失败（不阻断）: {e}", extra={"task": "daily_pipeline"})

    # ---- 2) 因子库更新 ----
    fb = factor_build_task.build_factor_library()
    steps.append(
        {
            "step": "factor_build",
            "status": fb.get("status"),
            "symbols_ok": fb.get("symbols_ok"),
            "factors_computed": fb.get("factors_computed"),
            "custom_computed": fb.get("custom_computed"),
            "dates": fb.get("dates"),
            "files": fb.get("files"),
            "duration_s": fb.get("duration_s"),
        }
    )

    ws_events.emit_pipeline(
        "step",
        task="eod_pipeline",
        step="factor_build",
        detail={
            "status": fb.get("status"),
            "symbols_ok": fb.get("symbols_ok"),
            "factors_computed": fb.get("factors_computed"),
            "duration_s": fb.get("duration_s"),
        },
    )

    # ---- 3) 因子效能评估 ----
    ev = factor_evaluate_task.evaluate_all_factors()
    steps.append(
        {
            "step": "factor_evaluate",
            "status": ev.get("status"),
            "factors_evaluated": ev.get("factors_evaluated"),
            "factors_skipped": ev.get("factors_skipped"),
            "duration_s": ev.get("duration_s"),
        }
    )

    # ---- 因子变更广播（根基：副本同步通道）----
    # 放在效能评估**之后**：此时因子值与指标都已是最新的，backend 同步一次即可拿到
    # 完整口径（只推 code + 版本，真实数据仍由 backend 经 HTTP 增量拉取）。
    try:
        from app.factors.registry import list_factors

        codes = [m.get("code") for m in list_factors() if m.get("code")]
        ws_events.emit_factor_updated(
            codes, version=str(data_after or ""), reason="eod_pipeline"
        )
    except Exception as e:  # noqa: BLE001 - 广播失败绝不阻断流水线
        logger.warning(f"因子变更广播失败（backend 将靠每日对账兜底）: {e}")

    ws_events.emit_pipeline(
        "finished",
        task="eod_pipeline",
        detail={
            "latest_after": data_after,
            "data_advanced": advanced,
            "duration_s": round(time.time() - t0, 1),
        },
    )

    summary = {
        "status": "done",
        "source": source,
        "latest_before": before,
        "latest_after": data_after,
        "data_advanced": advanced,
        "steps": steps,
        "duration_s": round(time.time() - t0, 1),
    }
    logger.info(
        "每日盘后流水线完成",
        extra={"task": "daily_pipeline", **{k: v for k, v in summary.items() if k != "steps"}},
    )
    return summary
