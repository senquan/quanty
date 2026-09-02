"""券商适配层抽象（实盘接口）

模拟盘与实盘共用同一套接口，`TradingCoordinator` 按 mode 路由，上层（API / 页面）
无需感知券商差异。

返回类型复用 `app.services.huatai_trading` 的 Order / Position / Account，
避免为同一概念重复建模。
"""
from abc import ABC, abstractmethod

from app.models.trading import MODE_PAPER
from app.services.huatai_trading import Account, Order, Position


class BrokerError(Exception):
    """券商不可用或交互失败（未配置凭证、鉴权失败、连接失败等）"""


class BrokerAdapter(ABC):
    """券商适配器接口。

    约定：
    - `place_order` 被拒时**不抛异常**，返回 status=REJECTED 的 Order，
      并通过动态属性 `order.message` 附带原因（拒单是业务结果，非系统异常）。
    - 仅当券商不可用（无凭证/连不上/鉴权失败）时使用 BrokerError 或返回不可用原因。
    """

    mode: str = MODE_PAPER
    broker_code: str = "simulated"

    @abstractmethod
    def is_ready(self) -> tuple[bool, str]:
        """是否可用：返回 (True, 说明) 或 (False, 不可用原因)"""

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def place_order(self, order: Order) -> Order: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order | None: ...

    @abstractmethod
    def get_market_price(self, symbol: str) -> float: ...
