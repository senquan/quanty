"""模拟盘适配器：包装独立的内存撮合引擎实例（每策略一份 = 独立资金池）。

仅做委派，不修改撮合/风控语义；落库由 TradingCoordinator 负责。
"""
from app.models.trading import MODE_PAPER
from app.services.broker.base import BrokerAdapter
from app.services.huatai_trading import (
    Account,
    HuataiSimulatorService,
    Order,
    OrderStatus,
    Position,
    PositionSide,
)


class SimulatedBroker(BrokerAdapter):
    mode = MODE_PAPER
    broker_code = "simulated"

    def __init__(self, strategy_id: int | None = None) -> None:
        # 每个策略持有一份独立的模拟撮合引擎实例，初始资金 1000000。
        # 进程重启后由 TradingCoordinator.sync_state 从 DB 回灌该策略的现金与持仓。
        self._svc = HuataiSimulatorService(initial_capital=1_000_000)
        self.strategy_id = strategy_id
        # 是否已从 DB 回灌过。必须以此为准而非"内存是否为空"：
        # 清仓后内存同样为空，若按空判断会又把 DB 的旧持仓回灌回去。
        self.restored = False

    def is_ready(self) -> tuple[bool, str]:
        return True, "模拟盘就绪"

    def get_account(self) -> Account:
        return self._svc.get_account()

    def get_positions(self) -> list[Position]:
        return self._svc.get_positions()

    def place_order(self, order: Order) -> Order:
        result = self._svc.place_order(order)
        # 模拟器拒单时只置 status，不回传原因；补取一次风控结论，
        # 使拒单原因能落库并在委托列表/前端展示（此时账户状态未变，结论一致）。
        if result.status == OrderStatus.REJECTED and not getattr(result, "message", None):
            _, reason = self._svc.risk_manager.validate_order(order, self._svc.account)
            result.message = reason
        return result

    def cancel_order(self, order_id: str) -> bool:
        return self._svc.cancel_order(order_id)

    def get_order_status(self, order_id: str) -> Order | None:
        return self._svc.get_order_status(order_id)

    def get_market_price(self, symbol: str) -> float:
        return self._svc.get_market_price(symbol)

    def get_available_symbols(self) -> list[dict]:
        return self._svc.get_available_symbols()

    def restore(
        self, *, cash: float, positions: list[tuple[str, int, float]]
    ) -> None:
        """从 DB 回灌内存状态，使模拟盘持仓不随进程重启丢失。

        positions: [(symbol, quantity, avg_price), ...]
        """
        self._svc.account.cash_balance = cash
        self._svc.positions.clear()
        for symbol, quantity, avg_price in positions:
            if quantity <= 0:
                continue
            self._svc.positions[f"{symbol}_{PositionSide.LONG.value}"] = Position(
                symbol=symbol,
                side=PositionSide.LONG,
                quantity=quantity,
                avg_price=avg_price,
            )
        self._svc._update_account_value()
        self.restored = True
        # 记录回灌时所基于的撮合服务对象。用于识别"回灌之后模拟服务单例被重建"
        # 的情况：此时 restored 仍为 True，但内存已变回初始空状态，若照常以内存
        # 为准回写 DB，会把 DB 持仓清空、现金刷回初始值。
        self._restored_svc = self._svc

    def is_restore_valid(self) -> bool:
        """当前内存状态是否确实来自一次 DB 回灌（且底层服务未被替换）。"""
        return bool(self.restored) and getattr(self, "_restored_svc", None) is self._svc
