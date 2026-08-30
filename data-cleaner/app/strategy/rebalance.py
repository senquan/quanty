"""因子策略调仓执行（模拟盘）

流程：读取启用策略 → 判断今日/此时是否到点 → 算目标持仓 → 取最新收盘价
→ 取模拟账户当前持仓 → 计算净买卖清单 → 经内部端点调主后端下单 → 写执行记录。

执行在独立线程（run_in_executor）中跑，复用 db.run_async 固定事件循环，
与现有流水线任务一致。
"""
from datetime import datetime, time
import traceback

import httpx

import app.storage.db as db
from app.core.config import settings
from app.core.logging import get_logger
from app.strategy import engine
from app.strategy import store as strat_store

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 后端内部调用
# --------------------------------------------------------------------------- #
def _backend_url() -> str:
    base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
    return base.rstrip("/")


def _internal_headers() -> dict:
    token = getattr(settings, "STRATEGY_INTERNAL_TOKEN", "")
    h = {}
    if token:
        h["X-Internal-Token"] = token
    return h


def _unwrap(resp):
    """后端统一用 Response.success(data=...) 包裹，这里拆出 data 字段（与前端 requestClient 一致）。"""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _backend_get(path: str) -> dict | None:
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{_backend_url()}{path}", headers=_internal_headers())
            if r.status_code >= 400:
                logger.warning(f"后端内部GET失败 {path}: {r.status_code} {r.text[:120]}")
                return None
            return _unwrap(r)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"后端内部GET异常 {path}: {e}")
        return None


def _backend_post(path: str, payload: dict) -> dict | None:
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(f"{_backend_url()}{path}", headers=_internal_headers(), json=payload)
            if r.status_code >= 400:
                logger.warning(f"后端内部POST失败 {path}: {r.status_code} {r.text[:120]}")
                return None
            return _unwrap(r)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"后端内部POST异常 {path}: {e}")
        return None


# --------------------------------------------------------------------------- #
# 取价
# --------------------------------------------------------------------------- #
def latest_prices(symbols: list[str]) -> dict[str, float]:
    """取各标的最新前复权收盘价（factor.raw_bars）。"""
    import app.storage.db as db

    if not symbols:
        return {}
    rows = db.run_async(_fetch_latest_prices(symbols))
    return rows or {}


async def _fetch_latest_prices(symbols: list[str]) -> dict[str, float]:
    from sqlalchemy import text

    async with db.current_session() as s:
        res = await s.execute(
            text(
                "SELECT DISTINCT ON (symbol) symbol, close FROM factor.raw_bars "
                "WHERE symbol = ANY(:syms) ORDER BY symbol, timestamp DESC"
            ),
            {"syms": list(symbols)},
        )
        return {r[0]: float(r[1]) for r in res.all()}


# --------------------------------------------------------------------------- #
# 时间判断
# --------------------------------------------------------------------------- #
def _is_rebalance_day(config: dict, today: datetime) -> bool:
    if today.weekday() >= 5:  # 周末跳过
        return False
    reb = config.get("rebalance") or {}
    freq = reb.get("freq", "weekly")
    if freq == "monthly":
        return today.day == 1
    if freq == "every_n_days":
        n = max(1, int(reb.get("every_n_days", 5) or 5))
        return (today.date() - datetime(2020, 1, 1).date()).days % n == 0
    # weekly：周一
    return today.weekday() == 0


def _time_reached(config: dict, now: datetime) -> bool:
    tt = (config.get("trade_time") or "").strip()
    if not tt:
        return True
    try:
        hh, mm = tt.split(":")
        return now.time() >= time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        return True


# --------------------------------------------------------------------------- #
# 单策略调仓
# --------------------------------------------------------------------------- #
def rebalance_one(row: dict) -> dict:
    sid = row["id"]
    config = row.get("config") or {}
    owner = row.get("owner")
    u = int(owner) if owner and str(owner).isdigit() else 1
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    try:
        # 防重复：今日已有执行记录则跳过
        existing = db.run_async(strat_store.get_execution(sid, today_str))
        if existing:
            return {"strategy_id": sid, "status": "skipped", "reason": "今日已执行"}

        target = engine.compute_target(config)
        if "error" in target:
            _record(sid, today_str, None, 0, 0, 0.0, "error", {"error": target["error"]})
            return {"strategy_id": sid, "status": "error", "reason": target["error"]}

        holdings = target.get("holdings") or []
        if not holdings:
            _record(sid, today_str, None, 0, 0, 0.0, "error", {"error": "无可买标的"})
            return {"strategy_id": sid, "status": "error", "reason": "无可买标的"}

        symbols = [h["symbol"] for h in holdings]
        prices = latest_prices(symbols)
        # 过滤掉无价格的标的
        symbols = [s for s in symbols if prices.get(s)]
        if not symbols:
            _record(sid, today_str, None, 0, 0, 0.0, "error", {"error": "无可用收盘价"})
            return {"strategy_id": sid, "status": "error", "reason": "无可用收盘价"}

        # 当前持仓
        acct = _backend_get("/api/v1/trading/account/internal")
        pos_resp = _backend_get("/api/v1/trading/positions/internal")
        cash = float((acct or {}).get("cash_balance", 0) or 0)
        current = {}
        for p in (pos_resp or []):
            if (p.get("side") or "LONG") == "LONG":
                current[p["symbol"]] = int(p.get("quantity", 0) or 0)

        n = len(symbols)
        per = cash * 0.95 / n if cash > 0 else 0.0
        desired = {}
        for s in symbols:
            if per > 0 and prices[s] > 0:
                desired[s] = max(0, int(per / prices[s] / 100) * 100)

        # 净买卖清单
        orders = []
        for s in symbols:
            diff = desired.get(s, 0) - current.get(s, 0)
            if diff > 0:
                orders.append({"symbol": s, "side": "BUY", "quantity": diff, "price": prices[s]})
            elif diff < 0:
                orders.append({"symbol": s, "side": "SELL", "quantity": -diff, "price": prices[s]})
        for s, q in current.items():
            if s not in desired and q > 0:
                orders.append({"symbol": s, "side": "SELL", "quantity": q, "price": prices.get(s, 0)})

        placed = 0
        amount = 0.0
        detail_orders = []
        for o in orders:
            if o["quantity"] <= 0 or not o["price"]:
                continue
            payload = {
                "symbol": o["symbol"],
                "order_type": "LIMIT",
                "side": o["side"],
                "quantity": o["quantity"],
                "price": round(o["price"], 4),
                "user_id": u,
            }
            resp = _backend_post("/api/v1/trading/orders/internal", payload)
            if resp:
                placed += 1
                amount += o["quantity"] * o["price"]
            detail_orders.append({"order": payload, "ok": bool(resp)})

        status = "success" if placed else "error"
        _record(
            sid, today_str, today_str, len(symbols), placed, amount, status,
            {"orders": detail_orders[:50], "target_count_asked": len(symbols)},
        )
        return {
            "strategy_id": sid, "status": status,
            "target_count": len(symbols), "orders_placed": placed,
            "amount": round(amount, 2),
        }
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()[:600]
        logger.error(f"调仓失败 strategy={sid}: {e}\n{tb}")
        _record(sid, today_str, None, 0, 0, 0.0, "error", {"error": str(e)[:200], "trace": tb})
        return {"strategy_id": sid, "status": "error", "reason": str(e)[:200]}


def _record(sid, rd, td, tc, op, amt, status, detail) -> None:
    db.run_async(
        strat_store.save_execution(sid, rd, td, tc, op, amt, status, detail)
    )


# --------------------------------------------------------------------------- #
# 定时扫描
# --------------------------------------------------------------------------- #
async def scan_and_rebalance() -> dict:
    """扫描启用策略，对今日到点且未执行的执行调仓。"""
    import asyncio

    rows = await strat_store.list_strategies(active_only=True)
    now = datetime.now()
    due = [
        r for r in rows
        if _is_rebalance_day(r.get("config") or {}, now)
        and _time_reached(r.get("config") or {}, now)
    ]
    results = []
    loop = asyncio.get_event_loop()
    for r in due:
        try:
            summary = await loop.run_in_executor(None, rebalance_one, r)
            results.append(summary)
        except Exception as e:  # noqa: BLE001
            logger.error(f"策略 {r.get('id')} 调仓异常: {e}")
            results.append({"strategy_id": r.get("id"), "status": "error", "reason": str(e)[:200]})
    logger.info(
        "策略调仓扫描完成",
        extra={"active": len(rows), "due": len(due), "done": len(results)},
    )
    return {"active": len(rows), "due": len(due), "results": results}
