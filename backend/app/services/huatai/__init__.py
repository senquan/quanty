# Huatai Securities API Integration Service
# ⚠️ 注意：此文件包含模拟交易API接口
# 实盘交易需要：
# 1. 在华泰证券官方申请API权限
# 2. 完成OAuth2.0认证
# 3. 遵守相关法律法规

from .huatai_trading import (
    Order, Position, Account,
    OrderType, OrderSide, OrderStatus, PositionSide,
    TradingService, HuataiSimulatorService,
    RiskControlManager,
    get_trading_service
)

__all__ = [
    'Order', 'Position', 'Account',
    'OrderType', 'OrderSide', 'OrderStatus', 'PositionSide',
    'TradingService', 'HuataiSimulatorService',
    'RiskControlManager',
    'get_trading_service'
]