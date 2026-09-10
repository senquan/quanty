"""增量历史回填任务

- backfill_symbol：单标的历史增量（或全量）拉取并 upsert 到 raw_bars
- backfill_universe：遍历全 A 股代码池做增量更新（每日调度用）
自动处理：首次全量 2010 起、之后只拉 [latest+1day, today]；限频 429 退避。
"""
from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from time import sleep, time as _time
from typing import Callable

from app.core.logging import get_logger
from app.ingestion.registry import get_source
from app.ingestion.universe import get_a_share_universe
from app.storage.raw_store import repository

logger = get_logger(__name__)

_DEFAULT_FULL_START = "2010-01-01"

# ---------- 修复单飞锁（WS 命令 与 8:30 定时任务 共用） ----------
# 修复是重操作（全市场增量，实测数十分钟且受数据源限频），必须互斥：
# 同一时刻只允许一个修复在跑，避免两路并发打满限频、互相拖垮。
_repair_guard = Lock()
_repair_running = False


def try_begin_repair() -> bool:
    """尝试占用修复名额；已有修复在跑则返回 False（调用方应回 busy）。"""
    global _repair_running
    with _repair_guard:
        if _repair_running:
            return False
        _repair_running = True
        return True


def end_repair() -> None:
    """释放修复名额（与 try_begin_repair 配对，可跨线程调用）。"""
    global _repair_running
    with _repair_guard:
        _repair_running = False


def is_repair_running() -> bool:
    """当前是否有修复在进行（供 coverage.repair.status）。"""
    return _repair_running

# 单标的最大重试次数（网络抖动）
_MAX_RETRY = 3

# 命中限频后的最大重试次数（限频退避是预期路径，给足重试余量）
_MAX_RATE_RETRY = 20

# 网络抖动特征：SSL 断连、chunked 读取中断、连接重置、超时等
_TRANSIENT_MARKERS = (
    "SSL",
    "UNEXPECTED_EOF",
    "incomplete chunked read",
    "peer closed connection",
    "Connection aborted",
    "Connection reset",
    "ConnectionReset",
    "RemoteDisconnected",
    "Server disconnected",
    "timed out",
    "Timeout",
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectionTimeout",
)

# 限频特征：pandadata 真实返回 500010「每分钟请求次数超限」，
# 旧逻辑只匹配「限频」匹配不到，导致批量静默失败（2026-09-07/09-08 复盘）。
_RATE_LIMIT_MARKERS = (
    "限频",
    "500010",
    "每分钟请求次数超限",
    "请求次数超限",
    "rate limit",
    "too many requests",
    "429",
)


def _is_transient(msg: str) -> bool:
    """判断是否为可重试的瞬时网络错误。"""
    return any(m in msg for m in _TRANSIENT_MARKERS)


def _is_rate_limited(msg: str) -> bool:
    """判断是否为数据源限频错误（命中后需退避 + 收紧全局速率）。"""
    low = msg.lower()
    return any(m.lower() in low for m in _RATE_LIMIT_MARKERS)


# ---------- 全局请求节流（pandadata 每分钟请求次数上限） ----------
# 实测触限约 225/min；初值保守低于该值，命中限频后自适应下调。
_RATE_LIMIT_PER_MIN = 100
_RATE_WINDOW = 60.0
_rate_lock = Lock()
_rate_ts: deque[float] = deque()


def _spend_permit() -> None:
    """pandadata 发起请求前调用：必要时休眠以平滑到 _RATE_LIMIT_PER_MIN/分钟。"""
    while True:
        with _rate_lock:
            now = _time()
            while _rate_ts and now - _rate_ts[0] >= _RATE_WINDOW:
                _rate_ts.popleft()
            if len(_rate_ts) < _RATE_LIMIT_PER_MIN:
                _rate_ts.append(now)
                return
            wait = _RATE_WINDOW - (now - _rate_ts[0]) + 0.05
        sleep(max(wait, 0))


def _tighten_rate() -> None:
    """命中限频时下调全局速率并清空窗口，避免反复撞墙。"""
    global _RATE_LIMIT_PER_MIN
    with _rate_lock:
        _RATE_LIMIT_PER_MIN = max(20, _RATE_LIMIT_PER_MIN // 2)
        _rate_ts.clear()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def backfill_symbol(
    source: str, symbol: str, full: bool = False, today: str | None = None
) -> dict:
    """增量（或全量）拉取单标的并入库。返回进度摘要。"""
    today = today or _today()
    # R1c（2026-09-06）：pandadata 是全市场主力源，但 SDK 明确不支持北交所
    # （报「后缀必须为SH或SZ」）。北交所标的自动改走 akshare，避免静默失败。
    if source == "pandadata" and symbol.upper().endswith(".BJ"):
        source_resolved = "akshare"
    else:
        source_resolved = source
    src = get_source(source_resolved)
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
        if source_resolved == "pandadata":
            _spend_permit()
        try:
            raw = src.fetch(symbol, start, today, "1d")
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if _is_rate_limited(msg) and retry < _MAX_RATE_RETRY:
                # pandadata 500010 每分钟请求次数超限：退避一个配额窗口，
                # 等每分钟计数重置，并收紧全局速率后重试。
                _tighten_rate()
                wait = 60
                logger.warning(
                    f"{symbol} 命中限频，速率下调并{wait}s后重试 "
                    f"({retry + 1}/{_MAX_RATE_RETRY}): {msg[:40]}"
                )
                sleep(wait)
                retry += 1
                continue
            if _is_transient(msg) and retry < _MAX_RETRY:
                # SSL 断连 / chunked 读取中断等属网络抖动，重试即可恢复
                wait = 2**retry
                logger.warning(
                    f"{symbol} 网络抖动，{wait}s 后重试 ({retry + 1}/{_MAX_RETRY}): {msg[:60]}"
                )
                sleep(wait)
                retry += 1
                continue
            return {"symbol": symbol, "status": "error", "rows": 0, "reason": msg[:120]}

    if raw is None or raw.empty:
        return {"symbol": symbol, "status": "empty", "rows": 0, "reason": f"{start}~{today}"}
    rows = repository.upsert(raw)
    return {"symbol": symbol, "status": "ok", "rows": rows, "from": start, "to": today}


def backfill_universe(
    source: str = "pandadata",
    symbols: list[str] | None = None,
    full: bool = False,
    batch_size: int = 200,
    progress_key: str = "raw_backfill_progress",
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """遍历全 A 股（或指定列表）做增量更新。返回汇总。

    :param on_progress: 每 batch 回调一次进度（供 WS 命令回推）；须线程安全。
    """
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
            if on_progress is not None:
                try:
                    on_progress(
                        {
                            "done": i,
                            "total": total,
                            "ok": ok,
                            "empty": empty,
                            "skip": skip,
                            "err": err,
                        }
                    )
                except Exception as e:  # noqa: BLE001 - 进度回调失败不得中断回填
                    logger.warning(f"backfill on_progress 回调失败（已忽略）: {e}")
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


def _expected_last_trading_day(reference: datetime.date) -> datetime.date:
    """返回 reference 之前（不含）最近一个交易日。

    仅排除周末（与 is_trading_day 一致）；法定节假日即便漏跑，拉数也只会
    拿到空集、不会污染数据，无需在此特殊处理。最多往前看 15 个自然日，
    覆盖春节/国庆等长假期。
    """
    from app.tasks.daily_pipeline import is_trading_day

    d = reference - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(datetime(d.year, d.month, d.day)):
            return d
        d -= timedelta(days=1)
    return reference - timedelta(days=1)


def check_coverage(min_ratio: float = 0.95) -> dict:
    """检查最新交易日覆盖度是否达标。

    判定不达标的两种情形：
    1. 最新交易日标的数 < 上一交易日 * min_ratio（当天更新漏了一批）
    2. 最新交易日早于"应已收盘的最近交易日"(expected)，或自然日距今超过
       _STALE_DAYS 天（服务宕机导致漏跑）
    """
    cov = repository.latest_day_coverage(days=2)
    if not cov:
        return {"ok": False, "need_repair": True, "reason": "无数据", "coverage": []}

    latest, latest_count = cov[0]
    prev, prev_count = (cov[1] if len(cov) > 1 else (None, 0))

    latest_date = datetime.strptime(latest, "%Y-%m-%d").date()
    gap = (datetime.now().date() - latest_date).days
    ratio = round(latest_count / prev_count, 4) if prev_count else None

    # 盲区修复（2026-09-07 复盘）：旧逻辑只按"自然日 gap > _STALE_DAYS"判定
    # 停滞，周末后漏掉单个交易日时 gap 恰好等于 4，会被误判为"正常"而跳过
    # 补齐。改为：latest 早于"应已收盘的最近交易日"(expected) 即视为停滞，
    # 并保留 gap 阈值作为长宕机兜底。
    expected = _expected_last_trading_day(datetime.now().date())
    stale = latest_date < expected or gap > _STALE_DAYS
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
    source: str = "pandadata",
    min_ratio: float = 0.95,
    *,
    force: bool = False,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """校验最新交易日覆盖度；不达标（或 force）则跑一轮增量补齐，并回读结果。

    :param force: 即使校验通过也强制执行一轮补齐（供 backend 主动指令回填）。
    :param on_progress: 回填进度回调（每 batch 调用一次）；须线程安全。
    """
    result = check_coverage(min_ratio)
    if not result["need_repair"] and not force:
        logger.info("覆盖度校验通过", extra=result)
        return result

    logger.info(f"覆盖度不达标（或强制执行），触发增量补齐: {result}")
    summary = backfill_universe(source=source, full=False, on_progress=on_progress)
    result["repair"] = {
        k: summary.get(k)
        for k in ("status", "total", "ok", "empty", "skip", "error")
    }
    result["after"] = check_coverage(min_ratio)
    logger.info("覆盖度补齐完成", extra=result)
    return result
