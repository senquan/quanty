"""策略调仓编排（由 data-cleaner 迁至 backend）

职责归位：data-cleaner 只负责"算目标持仓"（`POST /strategy/scores`，纯计算、无副作用）
与"行情中继"（`POST /raw/latest-prices`），编排 / 资金分配 / 下单 / 记录均由
backend（交易中心）完成。

流程：
    1. 拉启用策略      proxy.list_strategies(active_only=True)
    2. 算目标持仓      proxy.scores(config)          ← dc 只做计算
    3. 取最新价        market_proxy.latest_prices()  ← dc 行情中继
    4. 读持仓/现金     本地 DB 直读（原实现需两次 dc→backend HTTP 往返）
    5. 算净买卖清单    按 REBALANCE_CASH_USAGE / REBALANCE_LOT_SIZE
    6. 下单            TradingCoordinator.place_order(source="strategy")
    7. 写调仓记录      幂等 upsert（按 strategy_id + rebalance_date + mode）
"""
import logging
from datetime import datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.trading import MODE_PAPER
from app.services import factor_strategy_proxy as proxy
from app.services import trading_repository as repo
from app.services.market_proxy import MarketProxyError, latest_prices
from app.services.trading_coordinator import TradingCoordinator

logger = logging.getLogger(__name__)


# ---------------- 调仓时点判断（迁自 data-cleaner） ----------------

def is_rebalance_day(config: dict, today: datetime) -> bool:
    if today.weekday() >= 5:  # 周末不调仓
        return False
    reb = config.get("rebalance") or {}
    freq = reb.get("freq", "weekly")
    if freq == "monthly":
        return today.day == 1
    if freq == "every_n_days":
        n = max(1, int(reb.get("every_n_days", 5) or 5))
        return (today.date() - datetime(2020, 1, 1).date()).days % n == 0
    return today.weekday() == 0  # weekly：周一


def time_reached(config: dict, now: datetime) -> bool:
    tt = (config.get("trade_time") or "").strip()
    if not tt:
        return True
    try:
        hh, mm = tt.split(":")
        return now.time() >= time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        return True


# ---------------- 资金分配（可配置，默认沿用原逻辑） ----------------

def _cash_usage() -> float:
    return float(getattr(settings, "REBALANCE_CASH_USAGE", 0.95))


def _lot_size() -> int:
    return max(1, int(getattr(settings, "REBALANCE_LOT_SIZE", 100)))


def compute_desired_quantities(cash: float, prices: dict[str, float]) -> dict[str, int]:
    """等权分配可用资金，按整手数向下取整（默认沿用 data-cleaner 原逻辑）。"""
    if cash <= 0 or not prices:
        return {}
    lot = _lot_size()
    per = cash * _cash_usage() / len(prices)
    desired: dict[str, int] = {}
    for symbol, price in prices.items():
        if price and price > 0:
            desired[symbol] = max(0, int(per / price / lot) * lot)
    return desired


# ---------------- 单策略调仓 ----------------

async def rebalance_one(
    db: AsyncSession, strategy: dict, mode: str = MODE_PAPER, force: bool = False
) -> dict:
    """执行单个策略的调仓。force=True 时跳过"到点 / 防重"判断（用于手动触发）。"""
    sid = strategy.get("id")
    name = strategy.get("name")
    config = strategy.get("config") or {}
    owner = strategy.get("owner")
    user_id = int(owner) if owner and str(owner).isdigit() else None

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    def payload(status: str, detail: dict, **kw) -> dict:
        return {
            "strategy_id": sid,
            "strategy_name": name,
            "rebalance_date": today_str,
            "mode": mode,
            "status": status,
            "detail": detail,
            **kw,
        }

    async def fail(reason: str) -> dict:
        await _write(db, payload("error", {"error": reason}))
        return {"strategy_id": sid, "status": "error", "reason": reason}

    try:
        if not force:
            # 防重：今日已执行过则跳过（与唯一约束共同构成幂等保障）
            if await repo.get_rebalance(
                db, strategy_id=sid, rebalance_date=today_str, mode=mode
            ):
                return {"strategy_id": sid, "status": "skipped", "reason": "今日已执行"}

        coordinator = TradingCoordinator(db, mode, strategy_id=sid)

        # 2) 目标持仓（data-cleaner 只做计算）
        target = await proxy.scores(db, config)
        if not target or "error" in target:
            return await fail((target or {}).get("error") or "目标持仓计算失败")

        symbols = [h["symbol"] for h in (target.get("holdings") or []) if h.get("symbol")]
        if not symbols:
            return await fail("无可买标的")

        # 3) 取最新价（data-cleaner 行情中继）
        try:
            prices = await latest_prices(db, symbols)
        except MarketProxyError as e:
            return await fail(f"取价失败: {e}")

        symbols = [s for s in symbols if prices.get(s)]
        if not symbols:
            return await fail("无可用收盘价")

        # 4) 本地读现金与持仓（不再跨服务 HTTP 往返）
        account, positions = await coordinator.sync_state()
        cash = float(account.cash_balance or 0)
        if cash <= 0:
            return await fail(f"可用资金不足: {cash}")

        desired = compute_desired_quantities(cash, {s: prices[s] for s in symbols})
        current = {
            p.symbol: int(p.quantity or 0)
            for p in positions
            if (p.side or "LONG") == "LONG"
        }

        # 5) 净买卖清单：补仓/减仓 + 清仓目标外的持仓
        orders: list[dict] = []
        for s in symbols:
            diff = desired.get(s, 0) - current.get(s, 0)
            if diff > 0:
                orders.append({"symbol": s, "side": "BUY", "quantity": diff})
            elif diff < 0:
                orders.append({"symbol": s, "side": "SELL", "quantity": -diff})
        for s, q in current.items():
            if s not in desired and q > 0:
                orders.append({"symbol": s, "side": "SELL", "quantity": q})

        # 6) 下单
        placed = 0
        amount = 0.0
        detail_orders: list[dict] = []
        for o in orders:
            if o["quantity"] <= 0:
                continue
            order = await coordinator.place_order(
                symbol=o["symbol"],
                side=o["side"],
                quantity=o["quantity"],
                order_type="LIMIT",
                price=round(prices.get(o["symbol"], 0) or 0, 4),
                source="strategy",
                strategy_id=sid,
                user_id=user_id,
            )
            if order.status == "FILLED":
                placed += 1
                amount += (order.price or 0) * o["quantity"]
            detail_orders.append(
                {
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "quantity": o["quantity"],
                    "status": order.status,
                    "message": order.message,
                }
            )

        status = "success" if placed else "error"
        await _write(
            db,
            payload(
                status,
                {"orders": detail_orders[:50], "target_count_asked": len(symbols)},
                trade_date=today_str,
                target_count=len(symbols),
                orders_placed=placed,
                amount=round(amount, 2),
            ),
        )
        return {
            "strategy_id": sid,
            "status": status,
            "target_count": len(symbols),
            "orders_placed": placed,
            "amount": round(amount, 2),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("策略调仓失败 strategy=%s", sid)
        await _write(db, payload("error", {"error": str(e)[:200]}))
        return {"strategy_id": sid, "status": "error", "reason": str(e)[:200]}


async def _write(db: AsyncSession, payload: dict) -> None:
    try:
        await repo.upsert_rebalance(db, **payload)
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("写调仓记录失败 strategy=%s", payload.get("strategy_id"))
        await db.rollback()


# ---------------- 批量扫描 ----------------

async def scan_and_rebalance(
    db: AsyncSession, mode: str = MODE_PAPER, force: bool = False
) -> dict:
    """扫描启用策略，对到点且未执行的执行调仓。"""
    await repo.ensure_trading_tables()
    try:
        strategies = await proxy.list_strategies(db, active_only=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("拉取启用策略失败: %s", e)
        return {"active": 0, "due": 0, "results": [], "error": str(e)[:200]}

    now = datetime.now()
    due = [
        s
        for s in strategies
        if force
        or (
            is_rebalance_day(s.get("config") or {}, now)
            and time_reached(s.get("config") or {}, now)
        )
    ]
    results = [await rebalance_one(db, s, mode=mode, force=force) for s in due]

    logger.info(
        "策略调仓扫描完成 active=%s due=%s done=%s",
        len(strategies),
        len(due),
        len(results),
    )
    return {"active": len(strategies), "due": len(due), "results": results}
