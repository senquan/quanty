"""
华泰证券模拟交易服务
注意：此模块仅供学习和测试使用
实盘交易需要：
1. 在华泰证券官方申请API权限
2. 遵守相关法律法规
3. 实现完整的风险控制
"""
import uuid
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"  # 市价单
    LIMIT = "LIMIT"    # 限价单

class OrderSide(Enum):
    """买卖方向"""
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(Enum):
    """订单状态"""
    PENDING = "PENDING"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

class PositionSide(Enum):
    """持仓方向"""
    LONG = "LONG"
    SHORT = "SHORT"

@dataclass
class Order:
    """订单类"""
    order_id: str
    symbol: str
    order_type: OrderType
    side: OrderSide
    quantity: int
    price: Optional[float] = None
    filled_quantity: int = 0
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    commission: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'order_type': self.order_type.value,
            'side': self.side.value,
            'quantity': self.quantity,
            'price': self.price,
            'filled_quantity': self.filled_quantity,
            'status': self.status.value,
            'created_at': self.created_at.isoformat(),
            'filled_at': self.filled_at.isoformat() if self.filled_at else None,
            'commission': self.commission
        }

@dataclass
class Position:
    """持仓类"""
    symbol: str
    side: PositionSide
    quantity: int
    avg_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'side': self.side.value,
            'quantity': self.quantity,
            'avg_price': self.avg_price,
            'market_value': self.market_value,
            'unrealized_pnl': self.unrealized_pnl
        }

@dataclass
class Account:
    """账户类"""
    account_id: str
    total_assets: float
    cash_balance: float
    frozen_cash: float = 0.0
    positions: List[Position] = field(default_factory=list)
    daily_pnl: float = 0.0
    daily_commission: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            'account_id': self.account_id,
            'total_assets': self.total_assets,
            'cash_balance': self.cash_balance,
            'frozen_cash': self.frozen_cash,
            'positions': [p.to_dict() for p in self.positions],
            'daily_pnl': self.daily_pnl,
            'daily_commission': self.daily_commission
        }

class RiskControlManager:
    """风险控制管理器"""
    
    def __init__(self, 
                 max_position_pct: float = 0.3,
                 max_order_value: float = 100000,
                 max_daily_loss: float = 0.05,
                 min_cash_balance: float = 10000):
        
        self.max_position_pct = max_position_pct  # 单只股票最大仓位比例
        self.max_order_value = max_order_value   # 单笔最大订单金额
        self.max_daily_loss = max_daily_loss      # 日最大亏损比例
        self.min_cash_balance = min_cash_balance  # 最小现金余额
        
    def validate_order(self, order: Order, account: Account) -> tuple[bool, str]:
        """验证订单是否通过风控检查"""
        
        # 1. 检查订单金额
        order_value = order.price * order.quantity if order.price else 0
        if order_value > self.max_order_value:
            return False, f"订单金额 {order_value} 超过最大限制 {self.max_order_value}"
        
        # 2. 检查持仓比例
        total_assets = account.total_assets
        current_position_value = sum(
            pos.market_value for pos in account.positions 
            if pos.symbol == order.symbol
        )
        
        if order.side == OrderSide.BUY:
            new_position_value = current_position_value + order_value
            if new_position_value / total_assets > self.max_position_pct:
                return False, f"买入后持仓比例 {new_position_value/total_assets:.2%} 超过限制 {self.max_position_pct:.2%}"
        
        # 3. 检查现金余额
        if order.side == OrderSide.BUY and account.cash_balance < order_value:
            return False, f"现金余额不足: {account.cash_balance} < {order_value}"
        
        # 4. 检查最小现金余额
        remaining_cash = account.cash_balance - order_value
        if remaining_cash < self.min_cash_balance:
            return False, f"买入后现金余额 {remaining_cash} 低于最低要求 {self.min_cash_balance}"
        
        return True, "订单验证通过"
    
    def validate_cancel(self, order: Order, account: Account) -> tuple[bool, str]:
        """验证是否可以撤单"""
        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
            return False, f"订单状态为 {order.status.value}，不可撤单"
        return True, "可撤单"

class TradingService(ABC):
    """交易服务抽象基类"""
    
    @abstractmethod
    def get_account(self) -> Account:
        """获取账户信息"""
        pass
    
    @abstractmethod
    def get_positions(self) -> List[Position]:
        """获取持仓"""
        pass
    
    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """下单"""
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        pass
    
    @abstractmethod
    def get_order_status(self, order_id: str) -> Optional[Order]:
        """查询订单状态"""
        pass
    
    @abstractmethod
    def get_market_price(self, symbol: str) -> float:
        """获取市场价格"""
        pass

class HuataiSimulatorService(TradingService):
    """华泰证券模拟交易服务"""
    
    def __init__(self, initial_capital: float = 1000000):
        self.account_id = f"HUATAI_SIM_{uuid.uuid4().hex[:8]}"
        self.initial_capital = initial_capital
        
        # 初始化账户
        self.account = Account(
            account_id=self.account_id,
            total_assets=initial_capital,
            cash_balance=initial_capital,
            frozen_cash=0.0
        )
        
        # 订单字典
        self.orders: Dict[str, Order] = {}
        
        # 持仓字典
        self.positions: Dict[str, Position] = {}
        
        # 风控管理器
        self.risk_manager = RiskControlManager(
            max_position_pct=0.3,
            max_order_value=100000,
            max_daily_loss=0.05,
            min_cash_balance=10000
        )
        
        # 市场价格模拟
        self.market_prices: Dict[str, float] = {
            '600519.SH': 1900.0,  # 茅台
            '000001.SH': 3400.0,  # 上证指数
            '300750.SZ': 240.0,   # 宁德时代
            '600036.SH': 45.0,    # 招商银行
            '000651.SZ': 58.0,   # 格力电器
        }
        
        logger.info(f"模拟交易服务初始化完成，账户ID: {self.account_id}")
    
    def get_account(self) -> Account:
        """获取账户信息"""
        self._update_account_value()
        return self.account
    
    def get_positions(self) -> List[Position]:
        """获取持仓"""
        return list(self.positions.values())
    
    def place_order(self, order: Order) -> Order:
        """下单"""
        # 风控检查
        valid, message = self.risk_manager.validate_order(order, self.account)
        if not valid:
            order.status = OrderStatus.REJECTED
            logger.warning(f"订单被风控拦截: {message}")
            return order
        
        # 生成订单ID
        order.order_id = f"ORD_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"
        
        # 冻结资金
        if order.price:
            frozen_amount = order.price * order.quantity
            self.account.frozen_cash += frozen_amount
        
        # 存储订单
        self.orders[order.order_id] = order
        
        logger.info(f"订单已提交: {order.to_dict()}")
        
        # 模拟订单成交（立即成交）
        self._simulate_order_filled(order)
        
        return order
    
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False
        
        order = self.orders[order_id]
        valid, message = self.risk_manager.validate_cancel(order, self.account)
        
        if not valid:
            logger.warning(f"撤单失败: {message}")
            return False
        
        # 解冻资金
        if order.price and order.status == OrderStatus.PENDING:
            self.account.frozen_cash -= order.price * order.quantity
            self.account.cash_balance += order.price * order.quantity
        
        order.status = OrderStatus.CANCELLED
        logger.info(f"订单已撤销: {order_id}")
        return True
    
    def get_order_status(self, order_id: str) -> Optional[Order]:
        """查询订单状态"""
        return self.orders.get(order_id)
    
    def get_market_price(self, symbol: str) -> float:
        """获取市场价格"""
        # 模拟价格波动
        if symbol in self.market_prices:
            # 随机波动 ±0.5%
            import random
            base_price = self.market_prices[symbol]
            fluctuation = random.uniform(-0.005, 0.005)
            return round(base_price * (1 + fluctuation), 2)
        return 0.0
    
    def get_available_symbols(self) -> List[Dict]:
        """获取可交易标的"""
        return [
            {'symbol': '600519.SH', 'name': '贵州茅台', 'price': self.market_prices.get('600519.SH', 1900)},
            {'symbol': '000001.SH', 'name': '上证指数', 'price': self.market_prices.get('000001.SH', 3400)},
            {'symbol': '300750.SZ', 'name': '宁德时代', 'price': self.market_prices.get('300750.SZ', 240)},
            {'symbol': '600036.SH', 'name': '招商银行', 'price': self.market_prices.get('600036.SH', 45)},
            {'symbol': '000651.SZ', 'name': '格力电器', 'price': self.market_prices.get('000651.SZ', 58)},
        ]
    
    def _simulate_order_filled(self, order: Order):
        """模拟订单成交"""
        if order.status == OrderStatus.REJECTED:
            return
        
        # 更新订单状态
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.filled_at = datetime.now()
        
        # 计算手续费（假设万三）
        commission = order.price * order.quantity * 0.0003
        order.commission = commission
        
        # 更新账户
        self.account.frozen_cash -= order.price * order.quantity
        self.account.daily_commission += commission
        
        if order.side == OrderSide.BUY:
            # 买入：增加持仓，减少现金
            self._update_position(order, is_buy=True)
            self.account.cash_balance -= (order.price * order.quantity + commission)
            
        else:
            # 卖出：减少持仓，增加现金
            self._update_position(order, is_buy=False)
            self.account.cash_balance += (order.price * order.quantity - commission)
        
        # 更新账户总资产
        self._update_account_value()
        
        logger.info(f"订单已成交: {order.order_id}, 成交价: {order.price}, 数量: {order.quantity}")
    
    def _update_position(self, order: Order, is_buy: bool):
        """更新持仓"""
        symbol = order.symbol
        position_key = f"{symbol}_{order.side.value}"
        
        if is_buy:
            if position_key in self.positions:
                pos = self.positions[position_key]
                # 计算新的平均成本
                total_cost = pos.avg_price * pos.quantity + order.price * order.quantity
                pos.quantity += order.quantity
                pos.avg_price = total_cost / pos.quantity
            else:
                self.positions[position_key] = Position(
                    symbol=symbol,
                    side=PositionSide.LONG,
                    quantity=order.quantity,
                    avg_price=order.price
                )
        else:
            if position_key in self.positions:
                pos = self.positions[position_key]
                pos.quantity -= order.quantity
                if pos.quantity <= 0:
                    del self.positions[position_key]
    
    def _update_account_value(self):
        """更新账户总资产"""
        total_position_value = sum(pos.market_value for pos in self.positions.values())
        self.account.total_assets = self.account.cash_balance + total_position_value
        
        # 更新持仓市值和浮动盈亏
        for pos in self.positions.values():
            current_price = self.get_market_price(pos.symbol)
            pos.market_value = pos.quantity * current_price
            pos.unrealized_pnl = (current_price - pos.avg_price) * pos.quantity
    
    def get_daily_report(self) -> Dict:
        """获取日报表"""
        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'account_id': self.account_id,
            'opening_balance': self.initial_capital,
            'closing_balance': self.account.total_assets,
            'daily_pnl': self.account.total_assets - self.initial_capital,
            'daily_pnl_pct': (self.account.total_assets - self.initial_capital) / self.initial_capital * 100,
            'total_commission': self.account.daily_commission,
            'order_count': len(self.orders),
            'filled_order_count': len([o for o in self.orders.values() if o.status == OrderStatus.FILLED]),
        }
    
    def get_trade_history(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """获取交易历史"""
        trades = []
        for order in self.orders.values():
            if order.status == OrderStatus.FILLED:
                trades.append({
                    'trade_id': order.order_id,
                    'symbol': order.symbol,
                    'side': order.side.value,
                    'price': order.price,
                    'quantity': order.quantity,
                    'amount': order.price * order.quantity,
                    'commission': order.commission,
                    'trade_time': order.filled_at.isoformat() if order.filled_at else None
                })
        return trades

# 创建全局模拟交易服务实例
simulator_service = HuataiSimulatorService(initial_capital=1000000)

def get_trading_service() -> TradingService:
    """获取交易服务实例"""
    return simulator_service