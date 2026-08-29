"""增量历史回填任务

- backfill_symbol：单标的历史增量（或全量）拉取并 upsert 到 raw_bars
- backfill_universe：遍历全 A 股代码池做增量更新（每日调度用）
自动处理：首次全量 2010 起、之后只拉 [latest+1day, today]；限频 429 退避。
"""
from datetime import datetime, timedelta
from time import sleep

from app.core.logging import get_logger
from app.ingestion.registry import get_source
from app.ingestion.universe import get_a_share_universe
from app.storage.raw_store import repository

logger = get_logger(__name__)

_DEFAULT_FULL_START = "2010-01-01"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def backfill_symbol(
    source: str, symbol: str, full: bool = False, today: str | None = None
) -> dict:
    """增量（或全量）拉取单标的并入库。返回进度摘要。"""
    today = today or _today()
    src = get_source(source)
    latest = repository.get_latest_date(symbol)
    if full or not latest:
        start = _DEFAULT_FULL_START
    else:
        start = (
            datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
    if start > today:
        return {"symbol": symbol, "status": "skip", "rows": 0, "reason": "up-to-date"}

    retry = 0
    while True:
        try:
            raw = src.fetch(symbol, start, today, "1d")
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "限频" in msg and retry < 3:
                # 提取 retry_after 毫秒
                import re

                m = re.search(r"(\d+)ms", msg)
                wait = int(m.group(1)) / 1000 if m else 2
                logger.warning(f"{symbol} 限频，{wait}s 后重试")
                sleep(wait)
                retry += 1
                continue
            return {"symbol": symbol, "status": "error", "rows": 0, "reason": msg[:120]}

    if raw is None or raw.empty:
        return {"symbol": symbol, "status": "empty", "rows": 0, "reason": f"{start}~{today}"}
    rows = repository.upsert(raw)
    return {"symbol": symbol, "status": "ok", "rows": rows, "from": start, "to": today}


def backfill_universe(
    source: str = "alphafeed",
    symbols: list[str] | None = None,
    full: bool = False,
    batch_size: int = 200,
    progress_key: str = "raw_backfill_progress",
) -> dict:
    """遍历全 A 股（或指定列表）做增量更新。返回汇总。"""
    if symbols is None:
        try:
            symbols = get_a_share_universe()
        except RuntimeError as e:
            return {"status": "error", "reason": str(e)}

    total = len(symbols)
    ok = empty = skip = err = 0
    errors: list[str] = []
    for i, sym in enumerate(symbols, 1):
        res = backfill_symbol(source, sym, full=full)
        st = res["status"]
        if st == "ok":
            ok += 1
        elif st == "empty":
            empty += 1
        elif st == "skip":
            skip += 1
        else:
            err += 1
            errors.append(f"{sym}:{res.get('reason','')}")
        if i % batch_size == 0:
            logger.info(
                "backfill progress",
                extra={"done": i, "total": total, "ok": ok, "err": err},
            )
    summary = {
        "status": "done",
        "source": source,
        "full": full,
        "total": total,
        "ok": ok,
        "empty": empty,
        "skip": skip,
        "error": err,
        "finishedAt": _today(),
        "sampleErrors": errors[:10],
    }
    logger.info("backfill universe finished", extra=summary)
    return summary


# ---------- 覆盖度校验 / 自动补齐 ----------

# 最新交易日距今超过该天数视为数据停滞（跨周末/节假日取 4 天容错）
_STALE_DAYS = 4


def check_coverage(min_ratio: float = 0.95) -> dict:
    """检查最新交易日覆盖度是否达标。

    判定不达标的两种情形：
    1. 最新交易日标的数 < 上一交易日 * min_ratio（当天更新漏了一批）
    2. 最新交易日距今超过 _STALE_DAYS 天（服务宕机导致漏跑）
    """
    cov = repository.latest_day_coverage(days=2)
    if not cov:
        return {"ok": False, "need_repair": True, "reason": "无数据", "coverage": []}

    latest, latest_count = cov[0]
    prev, prev_count = (cov[1] if len(cov) > 1 else (None, 0))

    gap = (datetime.now().date() - datetime.strptime(latest, "%Y-%m-%d").date()).days
    ratio = round(latest_count / prev_count, 4) if prev_count else None

    stale = gap > _STALE_DAYS
    thin = bool(prev_count) and latest_count < prev_count * min_ratio

    return {
        "ok": not (stale or thin),
        "need_repair": stale or thin,
        "latest": latest,
        "latest_count": latest_count,
        "prev": prev,
        "prev_count": prev_count,
        "ratio": ratio,
        "stale_days": gap,
        "reason": ("数据停滞" if stale else ("覆盖度不足" if thin else "正常")),
    }


def verify_and_repair(
    source: str = "alphafeed", min_ratio: float = 0.95
) -> dict:
    """校验最新交易日覆盖度；不达标则跑一轮增量补齐，并回读结果。"""
    result = check_coverage(min_ratio)
    if not result["need_repair"]:
        logger.info("覆盖度校验通过", extra=result)
        return result

    logger.info(f"覆盖度不达标，触发增量补齐: {result}")
    summary = backfill_universe(source=source, full=False)
    result["repair"] = {
        k: summary.get(k)
        for k in ("status", "total", "ok", "empty", "skip", "error")
    }
    result["after"] = check_coverage(min_ratio)
    logger.info("覆盖度补齐完成", extra=result)
    return result
