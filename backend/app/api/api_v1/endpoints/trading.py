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
from fastapi import APIRouter, Depends, HTTPException, status, Header
from enum import Enum

from app.services.huatai_trading import (
    Order, Position, Account,
    OrderType, OrderSide, OrderStatus,
    get_trading_service, TradingService
)
from app.api.api_v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.models.user import User
from app.schemas.response import Response

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

@router.get("/account", response_model=Response[AccountResponse])
async def get_account(
    current_user: User = Depends(get_current_user)
):
    """获取账户信息"""
    service = get_trading_service()
    account = service.get_account()
    
    data = AccountResponse(
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
    return Response.success(data=data)

@router.get("/positions", response_model=Response[List[PositionResponse]])
async def get_positions(
    current_user: User = Depends(get_current_user)
):
    """获取持仓列表"""
    service = get_trading_service()
    positions = service.get_positions()
    
    data = [
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
    return Response.success(data=data)

@router.post("/orders", response_model=Response[OrderResponse])
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
        
        data = OrderResponse(
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
        return Response.success(data=data)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.delete("/orders/{order_id}", response_model=Response)
async def cancel_order(
    order_id: str,
    current_user: User = Depends(get_current_user)
):
    """撤单"""
    service = get_trading_service()
    
    success = service.cancel_order(order_id)
    
    if success:
        return Response.success(msg="撤单成功")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="撤单失败，订单不存在或状态不允许撤单"
        )

@router.get("/orders/{order_id}", response_model=Response[OrderResponse])
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
    
    data = OrderResponse(
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
    return Response.success(data=data)

@router.get("/market/quotes", response_model=Response[List[MarketDataResponse]])
async def get_market_quotes(
    symbols: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """获取实时行情"""
    service = get_trading_service()
    
    available_symbols = service.get_available_symbols()
    
    data = [
        MarketDataResponse(
            symbol=s['symbol'],
            name=s['name'],
            price=s['price'],
            change=0,  # 模拟数据
            change_pct=0  # 模拟数据
        )
        for s in available_symbols
    ]
    return Response.success(data=data)

@router.get("/market/price/{symbol}", response_model=Response)
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
    
    return Response.success(data={"symbol": symbol, "price": price, "timestamp": datetime.now().isoformat()})

@router.get("/trade-history", response_model=Response[List[TradeHistoryResponse]])
async def get_trade_history(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """获取交易历史"""
    service = get_trading_service()
    trades = service.get_trade_history(start_date, end_date)
    
    data = [
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
    return Response.success(data=data)

@router.get("/daily-report", response_model=Response[DailyReportResponse])
async def get_daily_report(
    current_user: User = Depends(get_current_user)
):
    """获取日报表"""
    service = get_trading_service()
    report = service.get_daily_report()
    
    data = DailyReportResponse(
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
    return Response.success(data=data)

@router.get("/available-symbols", response_model=Response)
async def get_available_symbols(
    current_user: User = Depends(get_current_user)
):
    """获取可交易标的列表"""
    service = get_trading_service()
    symbols = service.get_available_symbols()
    
    return Response.success(data={"symbols": symbols, "total": len(symbols)})

@router.get("/risk-settings", response_model=Response)
async def get_risk_settings(
    current_user: User = Depends(get_current_user)
):
    """获取风险设置"""
    service = get_trading_service()
    
    return Response.success(data={
        "max_position_pct": service.risk_manager.max_position_pct,
        "max_order_value": service.risk_manager.max_order_value,
        "max_daily_loss": service.risk_manager.max_daily_loss,
        "min_cash_balance": service.risk_manager.min_cash_balance
    })


# ---------------------------------------------------------------------------
# 内部端点（策略调仓定时任务调用，经 X-Internal-Token 鉴权，不走用户 JWT）
# ---------------------------------------------------------------------------
async def verify_internal_token(x_internal_token: str = Header(None)) -> bool:
    expected = getattr(settings, "STRATEGY_INTERNAL_TOKEN", "")
    if not expected or x_internal_token != expected:
        raise HTTPException(status_code=403, detail="内部令牌无效")
    return True


class InternalOrderRequest(BaseModel):
    symbol: str
    order_type: str
    side: str
    quantity: int
    price: float | None = None
    user_id: int | None = None


@router.post("/orders/internal", summary="内部下单（策略调仓）")
async def internal_place_order(
    payload: InternalOrderRequest,
    _: bool = Depends(verify_internal_token),
):
    """策略调仓专用下单入口：复用模拟撮合与风控，不校验用户会话。"""
    order_type = OrderType(payload.order_type.upper())
    side = OrderSide(payload.side.upper())
    order = Order(
        order_id=f"INT-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        symbol=payload.symbol,
        order_type=order_type,
        side=side,
        quantity=payload.quantity,
        price=payload.price,
    )
    filled = get_trading_service().place_order(order)
    if filled is None:
        return Response.error(code=400, msg=f"下单被拒或失败: {payload.symbol}")
    symbol = filled.symbol.decode() if isinstance(filled.symbol, bytes) else str(filled.symbol)
    return Response.success(data={
        "symbol": symbol,
        "side": getattr(filled.side, "value", str(filled.side)),
        "quantity": filled.quantity,
        "price": float(filled.price) if filled.price is not None else None,
        "status": getattr(filled.status, "value", str(filled.status)),
        "message": getattr(filled, "message", "") or "",
    })


@router.get("/account/internal", summary="内部账户查询")
async def internal_account(_: bool = Depends(verify_internal_token)):
    service = get_trading_service()
    acct = service.get_account()
    return Response.success(data={
        "cash_balance": acct.cash_balance,
        "total_asset": acct.total_assets,
        "market_value": acct.total_assets - acct.cash_balance,
        "realized_pnl": acct.daily_pnl,
        "unrealized_pnl": 0.0,
        "daily_pnl": acct.daily_pnl,
    })


@router.get("/positions/internal", summary="内部持仓查询")
async def internal_positions(_: bool = Depends(verify_internal_token)):
    service = get_trading_service()
    positions = service.get_positions()
    data = [
        {
            "symbol": p.symbol,
            "side": p.side.value if isinstance(p.side, Enum) else p.side,
            "quantity": p.quantity,
            "avg_price": p.avg_price,
            "market_price": p.market_price,
            "market_value": p.market_value,
            "unrealized_pnl": p.unrealized_pnl,
        }
        for p in positions
    ]
    return Response.success(data=data)