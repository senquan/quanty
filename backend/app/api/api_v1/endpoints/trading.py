"""
华泰证券交易API接口
⚠️ 注意：此为模拟交易API
实盘交易需要：
1. 在华泰证券官方申请API权限
2. 完成OAuth2.0认证
3. 遵守相关法律法规
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from enum import Enum

from app.services.huatai_trading import (
    Order, Position, Account,
    OrderType, OrderSide, OrderStatus,
    get_trading_service, TradingService
)
from app.api.api_v1.endpoints.auth import get_current_user
from app.models.user import User

router = APIRouter()

# ============ 请求/响应模型 ============

class OrderRequest(BaseModel):
    """下单请求"""
    symbol: str = Field(..., description="股票代码，如 600519.SH")
    order_type: str = Field(..., description="订单类型: MARKET, LIMIT")
    side: str = Field(..., description="买卖方向: BUY, SELL")
    quantity: int = Field(..., gt=0, description="数量")
    price: Optional[float] = Field(None, ge=0, description="价格，限价单必填")

class OrderResponse(BaseModel):
    """订单响应"""
    order_id: str
    symbol: str
    order_type: str
    side: str
    quantity: int
    price: Optional[float]
    filled_quantity: int
    status: str
    created_at: str
    filled_at: Optional[str]
    commission: float

class PositionResponse(BaseModel):
    """持仓响应"""
    symbol: str
    side: str
    quantity: int
    avg_price: float
    market_value: float
    unrealized_pnl: float

class AccountResponse(BaseModel):
    """账户响应"""
    account_id: str
    total_assets: float
    cash_balance: float
    frozen_cash: float
    positions: List[PositionResponse]
    daily_pnl: float
    daily_commission: float

class CancelOrderRequest(BaseModel):
    """撤单请求"""
    order_id: str

class MarketDataResponse(BaseModel):
    """行情数据响应"""
    symbol: str
    name: str
    price: float
    change: float
    change_pct: float

class TradeHistoryResponse(BaseModel):
    """交易历史响应"""
    trade_id: str
    symbol: str
    side: str
    price: float
    quantity: int
    amount: float
    commission: float
    trade_time: str

class DailyReportResponse(BaseModel):
    """日报表响应"""
    date: str
    account_id: str
    opening_balance: float
    closing_balance: float
    daily_pnl: float
    daily_pnl_pct: float
    total_commission: float
    order_count: int
    filled_order_count: int

# ============ API接口 ============

@router.get("/account", response_model=AccountResponse)
async def get_account(
    current_user: User = Depends(get_current_user)
):
    """获取账户信息"""
    service = get_trading_service()
    account = service.get_account()
    
    return AccountResponse(
        account_id=account.account_id,
        total_assets=account.total_assets,
        cash_balance=account.cash_balance,
        frozen_cash=account.frozen_cash,
        positions=[
            PositionResponse(
                symbol=pos.symbol,
                side=pos.side.value,
                quantity=pos.quantity,
                avg_price=pos.avg_price,
                market_value=pos.market_value,
                unrealized_pnl=pos.unrealized_pnl
            )
            for pos in account.positions
        ],
        daily_pnl=account.daily_pnl,
        daily_commission=account.daily_commission
    )

@router.get("/positions", response_model=List[PositionResponse])
async def get_positions(
    current_user: User = Depends(get_current_user)
):
    """获取持仓列表"""
    service = get_trading_service()
    positions = service.get_positions()
    
    return [
        PositionResponse(
            symbol=pos.symbol,
            side=pos.side.value,
            quantity=pos.quantity,
            avg_price=pos.avg_price,
            market_value=pos.market_value,
            unrealized_pnl=pos.unrealized_pnl
        )
        for pos in positions
    ]

@router.post("/orders", response_model=OrderResponse)
async def place_order(
    order_request: OrderRequest,
    current_user: User = Depends(get_current_user)
):
    """下单"""
    service = get_trading_service()
    
    try:
        # 创建订单对象
        order_type = OrderType.MARKET if order_request.order_type == "MARKET" else OrderType.LIMIT
        side = OrderSide.BUY if order_request.side == "BUY" else OrderSide.SELL
        
        order = Order(
            order_id="",  # 会在服务中生成
            symbol=order_request.symbol,
            order_type=order_type,
            side=side,
            quantity=order_request.quantity,
            price=order_request.price
        )
        
        # 下单
        filled_order = service.place_order(order)
        
        return OrderResponse(
            order_id=filled_order.order_id,
            symbol=filled_order.symbol,
            order_type=filled_order.order_type.value,
            side=filled_order.side.value,
            quantity=filled_order.quantity,
            price=filled_order.price,
            filled_quantity=filled_order.filled_quantity,
            status=filled_order.status.value,
            created_at=filled_order.created_at.isoformat(),
            filled_at=filled_order.filled_at.isoformat() if filled_order.filled_at else None,
            commission=filled_order.commission
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: str,
    current_user: User = Depends(get_current_user)
):
    """撤单"""
    service = get_trading_service()
    
    success = service.cancel_order(order_id)
    
    if success:
        return {"message": "撤单成功", "order_id": order_id}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="撤单失败，订单不存在或状态不允许撤单"
        )

@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    current_user: User = Depends(get_current_user)
):
    """查询订单状态"""
    service = get_trading_service()
    order = service.get_order_status(order_id)
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在"
        )
    
    return OrderResponse(
        order_id=order.order_id,
        symbol=order.symbol,
        order_type=order.order_type.value,
        side=order.side.value,
        quantity=order.quantity,
        price=order.price,
        filled_quantity=order.filled_quantity,
        status=order.status.value,
        created_at=order.created_at.isoformat(),
        filled_at=order.filled_at.isoformat() if order.filled_at else None,
        commission=order.commission
    )

@router.get("/market/quotes", response_model=List[MarketDataResponse])
async def get_market_quotes(
    symbols: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """获取实时行情"""
    service = get_trading_service()
    
    available_symbols = service.get_available_symbols()
    
    return [
        MarketDataResponse(
            symbol=s['symbol'],
            name=s['name'],
            price=s['price'],
            change=0,  # 模拟数据
            change_pct=0  # 模拟数据
        )
        for s in available_symbols
    ]

@router.get("/market/price/{symbol}")
async def get_market_price(
    symbol: str,
    current_user: User = Depends(get_current_user)
):
    """获取单个标的实时价格"""
    service = get_trading_service()
    price = service.get_market_price(symbol)
    
    if price == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"标的 {symbol} 不存在"
        )
    
    return {"symbol": symbol, "price": price, "timestamp": datetime.now().isoformat()}

@router.get("/trade-history", response_model=List[TradeHistoryResponse])
async def get_trade_history(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """获取交易历史"""
    service = get_trading_service()
    trades = service.get_trade_history(start_date, end_date)
    
    return [
        TradeHistoryResponse(
            trade_id=trade['trade_id'],
            symbol=trade['symbol'],
            side=trade['side'],
            price=trade['price'],
            quantity=trade['quantity'],
            amount=trade['amount'],
            commission=trade['commission'],
            trade_time=trade['trade_time']
        )
        for trade in trades
    ]

@router.get("/daily-report", response_model=DailyReportResponse)
async def get_daily_report(
    current_user: User = Depends(get_current_user)
):
    """获取日报表"""
    service = get_trading_service()
    report = service.get_daily_report()
    
    return DailyReportResponse(
        date=report['date'],
        account_id=report['account_id'],
        opening_balance=report['opening_balance'],
        closing_balance=report['closing_balance'],
        daily_pnl=report['daily_pnl'],
        daily_pnl_pct=report['daily_pnl_pct'],
        total_commission=report['total_commission'],
        order_count=report['order_count'],
        filled_order_count=report['filled_order_count']
    )

@router.get("/available-symbols")
async def get_available_symbols(
    current_user: User = Depends(get_current_user)
):
    """获取可交易标的列表"""
    service = get_trading_service()
    symbols = service.get_available_symbols()
    
    return {"symbols": symbols, "total": len(symbols)}

@router.get("/risk-settings")
async def get_risk_settings(
    current_user: User = Depends(get_current_user)
):
    """获取风险设置"""
    service = get_trading_service()
    
    return {
        "max_position_pct": service.risk_manager.max_position_pct,
        "max_order_value": service.risk_manager.max_order_value,
        "max_daily_loss": service.risk_manager.max_daily_loss,
        "min_cash_balance": service.risk_manager.min_cash_balance
    }