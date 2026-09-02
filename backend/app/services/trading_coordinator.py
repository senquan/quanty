"""交易协调层：模式路由 + 券商撮合 + 落库持久化

职责：
- 按 mode（paper / live）路由到对应 BrokerAdapter；
- 下单后把订单 / 成交 / 持仓 / 现金落库，使状态不随进程重启丢失；
- 查询时把券商侧状态同步进 DB，页面统一从 DB 读取（含 last_price 与盈亏）；
- 进程重启后，用 DB 回灌模拟盘的内存撮合状态。

注意：BrokerAdapter 的查询/下单为同步接口（与既有 huatai_trading 一致），
实盘适配器会发起 HTTP 请求；若后续实盘调用量增长，再统一改为 run_in_executor。
"""
import json
import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.quant import Strategy
from app.models.trading import (
    MODE_PAPER,
    ORDER_CANCELLED,
    ORDER_FILLED,
    ORDER_REJECTED,
    TradingAccount,
    TradingPosition,
)
from app.services import trading_repository as repo
from app.services.broker.factory import get_broker, normalize_mode
from app.services.broker.simulated import SimulatedBroker
from app.services.huatai_trading import Order as BrokerOrder
from app.services.huatai_trading import OrderSide, OrderType

logger = logging.getLogger(__name__)


def _new_client_order_id(mode: str) -> str:
    prefix = "P" if mode == MODE_PAPER else "L"
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid4().hex[:6].upper()}"


class TradingCoordinator:
    def __init__(self, session: AsyncSession, mode: str | None = None, strategy_id: int | None = None):
        self.session = session
        self.mode = normalize_mode(mode or getattr(settings, "BROKER_MODE", MODE_PAPER))
        self.strategy_id = strategy_id
        self._strategy = None
        self.broker = get_broker(self.mode, strategy_id)

    # ---------------- 账户与状态同步 ----------------
    def _strategy_capital(self) -> float | None:
        """从策略配置(config.capital / 兼容 initial_capital)读取该策略初始资金池规模。"""
        if self._strategy is None or not self._strategy.code:
            return None
        try:
            cfg = json.loads(self._strategy.code)
        except (ValueError, TypeError):
            return None
        if not isinstance(cfg, dict):
            return None
        cap = cfg.get("capital")
        if cap is None:
            cap = cfg.get("initial_capital")
        try:
            return float(cap) if cap is not None else None
        except (ValueError, TypeError):
            return None

    async def _ensure_account(self) -> TradingAccount:
        initial = float(getattr(settings, "TRADING_INITIAL_CAPITAL", 1000000))
        if self.strategy_id is not None:
            # 加载策略配置中的初始资金池规模（若有）
            strat = (
                await self.session.execute(
                    select(Strategy).where(Strategy.id == self.strategy_id)
                )
            ).scalars().first()
            self._strategy = strat
            cap = self._strategy_capital()
            if cap:
                initial = cap
        return await repo.get_or_create_account(
            self.session,
            mode=self.mode,
            broker=self.broker.broker_code,
            initial_capital=initial,
            strategy_id=self.strategy_id,
        )

    async def _restore_broker_state(self, account: TradingAccount) -> None:
        """进程重启后用 DB 回灌模拟盘内存状态（实盘状态在券商侧，无需回灌）。

        回灌条件从原来的「每个进程一次（restored 标志）」改为「回灌仍然有效
        （is_restore_valid）」：若回灌之后模拟撮合服务单例被重建（模块重导入等），
        restored 仍为 True 但内存已变回初始空状态，此时必须重新回灌，否则
        后续 sync_state 会以空内存为准把 DB 持仓清空、现金刷回初始值。
        """
        if not isinstance(self.broker, SimulatedBroker):
            return
        if self.broker.is_restore_valid():
            return  # 已回灌且底层撮合服务未被替换
        rows = await repo.list_positions(
            self.session, self.mode, account_id=account.id, strategy_id=self.strategy_id
        )
        logger.info("模拟盘内存回灌: cash=%s, 持仓 %s 条", account.cash_balance, len(rows))
        self.broker.restore(
            cash=account.cash_balance,
            positions=[(r.symbol, r.quantity, r.avg_price) for r in rows],
        )

    async def sync_state(self) -> tuple[TradingAccount, list[TradingPosition]]:
        """把券商侧账户与持仓同步落库，返回 (账户, 持仓列表)。"""
        account = await self._ensure_account()
        await self._restore_broker_state(account)

        info = self.broker.get_account()
        account.account_id = info.account_id or account.account_id
        await repo.update_account_balances(
            self.session, account, cash=info.cash_balance, frozen=info.frozen_cash
        )

        seen: set[tuple[str, str]] = set()
        for p in self.broker.get_positions():
            side = getattr(p.side, "value", str(p.side))
            last_price = self.broker.get_market_price(p.symbol) or (
                p.market_value / p.quantity if p.quantity else p.avg_price
            )
            await repo.upsert_position(
                self.session,
                account_id=account.id,
                mode=self.mode,
                symbol=p.symbol,
                side=side,
                quantity=p.quantity,
                avg_price=p.avg_price,
                last_price=last_price,
                strategy_id=self.strategy_id,
            )
            seen.add((p.symbol, side))

        # 券商侧已清仓、DB 仍残留的行 → 清理
        # 安全闸：仅当内存确实来自一次有效的 DB 回灌时才允许删除。
        # 否则内存可能是空/陈旧的，照删会把 DB 全部持仓清空（历史上
        # 600036 持仓即因此丢失）。不可信时跳过删除并告警。
        if isinstance(self.broker, SimulatedBroker) and not self.broker.is_restore_valid():
            logger.warning("模拟盘内存状态不可信（未完成有效 DB 回灌），跳过持仓删除以免覆盖 DB")
        else:
            for row in await repo.list_positions(
                self.session, self.mode, account_id=account.id, strategy_id=self.strategy_id
            ):
                if (row.symbol, row.side) not in seen:
                    await self.session.delete(row)

        await self.session.commit()
        return account, await repo.list_positions(
            self.session, self.mode, account_id=account.id, strategy_id=self.strategy_id
        )

    # ---------------- 下单 / 撤单 ----------------
    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "LIMIT",
        price: float | None = None,
        source: str = "manual",
        strategy_id: int | None = None,
        user_id: int | None = None,
    ):
        strategy_id = strategy_id if strategy_id is not None else self.strategy_id
        account = await self._ensure_account()
        await self._restore_broker_state(account)

        side_u = side.upper()
        otype_u = order_type.upper()
        model = await repo.create_order(
            self.session,
            account_id=account.id,
            mode=self.mode,
            client_order_id=_new_client_order_id(self.mode),
            symbol=symbol,
            side=side_u,
            order_type=otype_u,
            quantity=int(quantity),
            price=float(price) if price else None,
            source=source,
            strategy_id=strategy_id,
        )
        if user_id is not None and account.user_id is None:
            account.user_id = user_id

        broker_order = BrokerOrder(
            order_id="",
            symbol=symbol,
            order_type=OrderType.MARKET if otype_u == "MARKET" else OrderType.LIMIT,
            side=OrderSide.BUY if side_u == "BUY" else OrderSide.SELL,
            quantity=int(quantity),
            price=float(price) if price else None,
        )

        try:
            result = self.broker.place_order(broker_order)
        except Exception as e:  # noqa: BLE001  # 券商不可用视为拒单，落库留痕
            await repo.update_order(
                self.session, model, status=ORDER_REJECTED, message=str(e)[:200]
            )
            await self.session.commit()
            return model

        status = getattr(result.status, "value", str(result.status)).upper()
        message = getattr(result, "message", "") or ""
        filled_qty = int(getattr(result, "filled_quantity", 0) or 0)
        await repo.update_order(
            self.session,
            model,
            status=status,
            filled_quantity=filled_qty,
            broker_order_id=getattr(result, "order_id", "") or None,
            message=message or None,
        )

        if status == ORDER_FILLED and filled_qty > 0:
            await repo.add_trade(
                self.session,
                order_id=model.id,
                account_id=account.id,
                mode=self.mode,
                symbol=symbol,
                side=side_u,
                price=float(getattr(result, "price", 0) or price or 0),
                quantity=filled_qty,
                commission=float(getattr(result, "commission", 0) or 0),
                strategy_id=strategy_id,
            )

        await self.sync_state()  # 同步持仓与现金（内部 commit）
        return model

    async def cancel_order(self, client_order_id: str):
        model = await repo.get_order(self.session, client_order_id)
        if model is None:
            return None
        ok = self.broker.cancel_order(model.broker_order_id or client_order_id)
        if ok:
            await repo.update_order(
                self.session, model, status=ORDER_CANCELLED, message="已撤单"
            )
        else:
            await repo.update_order(
                self.session, model, message="撤单失败：订单不存在或状态不允许撤单"
            )
        await self.session.commit()
        return model

    # ---------------- 查询 ----------------
    def _metrics(
        self, account: TradingAccount, positions: list[TradingPosition]
    ) -> dict:
        market_value = sum(p.market_value for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)
        total_assets = account.cash_balance + market_value
        pnl = total_assets - account.initial_capital
        return {
            "mode": self.mode,
            "broker": self.broker.broker_code,
            "account_id": account.account_id,
            "initial_capital": round(account.initial_capital, 2),
            "total_assets": round(total_assets, 2),
            "market_value": round(market_value, 2),
            "cash_balance": round(account.cash_balance, 2),
            "frozen_cash": round(account.frozen_cash, 2),
            "total_pnl": round(pnl, 2),
            "total_pnl_pct": round(pnl / account.initial_capital * 100, 2)
            if account.initial_capital
            else 0.0,
            "unrealized_pnl": round(unrealized, 2),
            "position_count": len(positions),
        }

    async def get_overview(self) -> dict:
        account, positions = await self.sync_state()
        return self._metrics(account, positions)

    async def get_account_detail(self) -> dict:
        """账户详情（含持仓列表），一次同步即可，避免重复 sync_state。"""
        account, positions = await self.sync_state()
        metrics = self._metrics(account, positions)
        return {
            "account_id": account.account_id,
            "mode": account.mode,
            "broker": account.broker,
            "total_assets": metrics["total_assets"],
            "cash_balance": metrics["cash_balance"],
            "frozen_cash": metrics["frozen_cash"],
            "market_value": metrics["market_value"],
            "total_pnl": metrics["total_pnl"],
            "total_pnl_pct": metrics["total_pnl_pct"],
            "unrealized_pnl": metrics["unrealized_pnl"],
            "positions": positions,
        }

    async def list_positions(self) -> list[TradingPosition]:
        _, positions = await self.sync_state()
        return positions

    async def list_orders(self, status: str | None = None, limit: int = 50):
        return await repo.list_orders(self.session, self.mode, status=status, limit=limit)

    async def list_trades(self, start: str | None = None, end: str | None = None, limit: int = 200):
        return await repo.list_trades(self.session, self.mode, start=start, end=end, limit=limit)


async def ensure_tables() -> None:
    await repo.ensure_trading_tables()
