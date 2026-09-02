"""
交易接口（模拟盘 paper / 实盘 live）

两种模式共用同一套接口，通过 `mode` 参数（默认 paper）区分，
由 BrokerAdapter 适配层 + TradingCoordinator 落库：
- 模拟盘：内存撮合（华泰模拟服务），持仓与成交落库，进程重启后从 DB 回灌；
- 实盘：东方财富妙想适配器，默认 BROKER_DRY_RUN=True 不发起真实请求。

⚠️ 实盘需先在券商申请 API 权限并配置凭证，且须确认成交双校验语义
（下单成功 ≠ 成交，见 app/services/broker/mx.py）。
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.api_v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.trading import (
    PortfolioDailyValue,
    TradingOrder,
    TradingPosition,
    TradingRebalanceRecord,
    TradingTrade,
)
from app.models.user import User
from app.schemas.response import Response
from app.services import trading_repository as repo
from app.services.broker.factory import describe_modes
from app.services.huatai_trading import get_trading_service
from app.services.rebalance_service import scan_and_rebalance
from app.services.trading_coordinator import TradingCoordinator, ensure_tables

router = APIRouter()
logger = logging.getLogger(__name__)


# ============ 请求/响应模型 ============

class OrderRequest(BaseModel):
    """下单请求"""

    symbol: str = Field(..., description="股票代码，如 600519.SH")
    order_type: str = Field("LIMIT", description="订单类型: MARKET, LIMIT")
    side: str = Field(..., description="买卖方向: BUY, SELL")
    quantity: int = Field(..., gt=0, description="数量")
    price: Optional[float] = Field(None, ge=0, description="价格，限价单必填")
    mode: str = Field("paper", description="交易模式: paper, live")


# ============ 序列化 ============

def _order_dict(o: TradingOrder) -> dict:
    return {
        "order_id": o.client_order_id,
        "broker_order_id": o.broker_order_id,
        "symbol": o.symbol,
        "side": o.side,
        "order_type": o.order_type,
        "quantity": o.quantity,
        "price": o.price,
        "filled_quantity": o.filled_quantity,
        "status": o.status,
        "message": o.message,
        "source": o.source,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "filled_at": o.filled_at.isoformat() if o.filled_at else None,
    }


def _position_dict(p: TradingPosition) -> dict:
    pnl_pct = (
        round((p.last_price - p.avg_price) / p.avg_price * 100, 2) if p.avg_price else 0.0
    )
    return {
        "symbol": p.symbol,
        "side": p.side,
        "quantity": p.quantity,
        "avg_price": round(p.avg_price, 4),
        "last_price": round(p.last_price, 4),
        "prev_close": round(p.prev_close, 4) if p.prev_close else None,
        "market_value": round(p.market_value, 2),
        "unrealized_pnl": round(p.unrealized_pnl, 2),
        "pnl_percent": pnl_pct,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _rebalance_dict(r: TradingRebalanceRecord) -> dict:
    detail = None
    if r.detail:
        try:
            detail = json.loads(r.detail)
        except ValueError:  # 脏数据不应让列表整体失败
            detail = None
    return {
        "strategy_id": r.strategy_id,
        "strategy_name": r.strategy_name,
        "mode": r.mode,
        "rebalance_date": _iso(r.rebalance_date),
        "trade_date": _iso(r.trade_date),
        "target_count": r.target_count,
        "orders_placed": r.orders_placed,
        "amount": r.amount,
        "status": r.status,
        "detail": detail,
    }


def _trade_dict(t: TradingTrade) -> dict:
    return {
        "trade_id": t.id,
        "order_id": t.order_id,
        "symbol": t.symbol,
        "side": t.side,
        "price": round(t.price, 4),
        "quantity": t.quantity,
        "amount": round(t.amount, 2),
        "commission": round(t.commission, 2),
        "trade_time": t.trade_time.isoformat() if t.trade_time else None,
    }


# ============ 模式与概览 ============

@router.get("/mode", summary="各交易模式可用性")
async def get_mode(_: User = Depends(get_current_user)):
    modes = describe_modes()
    default = getattr(settings, "BROKER_MODE", "paper")
    return Response.success(data={"default": default, "modes": modes})


@router.get("/overview", summary="量化概览")
async def get_overview(
    mode: str = Query("paper", description="paper / live"),
    strategy_id: Optional[int] = Query(None, description="策略ID；不传则返回模式共享账户概览"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    overview = await TradingCoordinator(db, mode, strategy_id=strategy_id).get_overview()
    return Response.success(data=overview)


@router.get("/portfolio/values", summary="组合每日市值与收益（盘后估值快照）")
async def get_portfolio_values(
    mode: str = Query("paper", description="paper / live"),
    strategy_id: Optional[int] = Query(None, description="策略ID；不传返回模式级聚合快照"),
    limit: int = Query(120, ge=1, le=500, description="返回最近 N 个交易日"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """读 portfolio_daily_values，供 dashboard 直接画市值曲线 / 收益，无需实时算。

    由 backend 盘后定时任务（portfolio_valuation_service.run_eod_valuation）
    从 data-cleaner 拉行情后写入；传 strategy_id 返回该策略（独立资金池）序列，
    不传返回模式级聚合；升序返回便于前端绘制时间序列。
    """
    await ensure_tables()
    conditions = [PortfolioDailyValue.mode == mode]
    if strategy_id is not None:
        conditions.append(PortfolioDailyValue.strategy_id == strategy_id)
    else:
        conditions.append(PortfolioDailyValue.strategy_id.is_(None))
    rows = (
        await db.execute(
            select(PortfolioDailyValue)
            .where(*conditions)
            .order_by(PortfolioDailyValue.value_date.desc())
            .limit(limit)
        )
    ).scalars().all()
    data = [
        {
            "strategy_id": r.strategy_id,
            "value_date": r.value_date.isoformat(),
            "cash_balance": r.cash_balance,
            "market_value": r.market_value,
            "total_assets": r.total_assets,
            "daily_return": r.daily_return,
            "cumulative_return": r.cumulative_return,
        }
        for r in reversed(rows)
    ]
    return Response.success(data=data)


# ============ 账户 / 持仓 ============

@router.get("/account", summary="账户信息")
async def get_account(
    mode: str = Query("paper"),
    strategy_id: Optional[int] = Query(None, description="策略ID；不传则返回模式共享账户"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    detail = await TradingCoordinator(db, mode, strategy_id=strategy_id).get_account_detail()
    detail["positions"] = [_position_dict(p) for p in detail["positions"]]
    return Response.success(data=detail)


@router.get("/positions", summary="持仓列表")
async def get_positions(
    mode: str = Query("paper"),
    strategy_id: Optional[int] = Query(None, description="策略ID；不传则返回模式共享账户持仓"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    positions = await TradingCoordinator(db, mode, strategy_id=strategy_id).list_positions()
    return Response.success(data=[_position_dict(p) for p in positions])


# ============ 订单 / 成交 ============

@router.get("/orders", summary="订单列表")
async def get_orders(
    mode: str = Query("paper"),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    orders = await TradingCoordinator(db, mode).list_orders(status=status_filter, limit=limit)
    return Response.success(data=[_order_dict(o) for o in orders])


@router.post("/orders", summary="下单")
async def place_order(
    order_request: OrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_tables()
    coordinator = TradingCoordinator(db, order_request.mode)
    order = await coordinator.place_order(
        symbol=order_request.symbol,
        side=order_request.side,
        order_type=order_request.order_type,
        quantity=order_request.quantity,
        price=order_request.price,
        user_id=getattr(current_user, "id", None),
    )
    data = _order_dict(order)
    if order.status == "REJECTED":
        # 拒单是业务结果，用 200 + status 表达；同时给出可读原因
        data["message"] = order.message or "下单被拒"
    return Response.success(data=data)


@router.get("/orders/{order_id}", summary="查询订单")
async def get_order(
    order_id: str,
    mode: str = Query("paper"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    order = await repo.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    return Response.success(data=_order_dict(order))


@router.delete("/orders/{order_id}", summary="撤单")
async def cancel_order(
    order_id: str,
    mode: str = Query("paper"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    order = await TradingCoordinator(db, mode).cancel_order(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    data = _order_dict(order)
    if order.status != "CANCELLED":
        return Response.error(code=400, msg=order.message or "撤单失败", data=data)
    return Response.success(data=data, msg="撤单成功")


@router.get("/trades", summary="成交记录")
async def get_trades(
    mode: str = Query("paper"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await ensure_tables()
    trades = await TradingCoordinator(db, mode).list_trades(start=start, end=end, limit=limit)
    return Response.success(data=[_trade_dict(t) for t in trades])


@router.get("/rebalances", summary="调仓记录")
async def get_rebalances(
    mode: str = Query("paper"),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """读本地表 trading_rebalance_records。

    原实现需逐策略转发 data-cleaner 的 /executions（N+1 请求）；
    调仓编排归位 backend 后直接读本地库。
    """
    await ensure_tables()
    rows = await repo.list_rebalances(db, mode=mode, limit=limit)
    return Response.success(data=[_rebalance_dict(r) for r in rows])


@router.post("/rebalances/trigger", summary="手动触发调仓")
async def trigger_rebalance(
    mode: str = Query("paper"),
    force: bool = Query(False, description="跳过调仓时点判断与当日防重"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """手动触发一次全量扫描调仓（替代原 data-cleaner 的手动调仓端点）。"""
    await ensure_tables()
    summary = await scan_and_rebalance(db, mode=mode, force=force)
    return Response.success(data=summary)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# ============ 行情与风控（沿用模拟撮合服务） ============

@router.get("/market/quotes", summary="可交易标的行情")
async def get_market_quotes(_: User = Depends(get_current_user)):
    service = get_trading_service()
    data = [
        {
            "symbol": s["symbol"],
            "name": s["name"],
            "price": s["price"],
            "change": 0,
            "change_pct": 0,
        }
        for s in service.get_available_symbols()
    ]
    return Response.success(data=data)


@router.get("/market/price/{symbol}", summary="单个标的价格")
async def get_market_price(symbol: str, _: User = Depends(get_current_user)):
    price = get_trading_service().get_market_price(symbol)
    if price == 0:
        raise HTTPException(status_code=404, detail=f"标的 {symbol} 不存在")
    return Response.success(
        data={"symbol": symbol, "price": price, "timestamp": datetime.now().isoformat()}
    )


@router.get("/available-symbols", summary="可交易标的列表")
async def get_available_symbols(_: User = Depends(get_current_user)):
    symbols = get_trading_service().get_available_symbols()
    return Response.success(data={"symbols": symbols, "total": len(symbols)})


@router.get("/symbols/metadata", summary="标的主数据（代码→中文名）")
async def get_symbols_metadata(
    symbols: str | None = Query(None, description="逗号分隔的代码列表；缺省返回全量"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """返回标的代码 → 中文名等展示信息（只读 backend 标的主数据表 instruments）。

    本接口**不触发**对 data-cleaner 的查询 / 回填：名字由盘后估值
    （portfolio_valuation_service.run_eod_valuation）在更新行情时顺带落库。
    这里只做查询；查不到的代码前端回退成裸代码即可，不阻塞加载。
    """
    want = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    await repo.ensure_trading_tables()
    local = await repo.get_instruments(db, want)
    return Response.success(
        data={"symbols": list(local.values()), "total": len(local)}
    )


@router.get("/risk-settings", summary="风险设置")
async def get_risk_settings(_: User = Depends(get_current_user)):
    service = get_trading_service()
    rm = service.risk_manager
    # max_order_value 为按当前总资产换算出的有效上限（比例 × 总资产）
    total_assets = service.get_account().total_assets
    return Response.success(
        data={
            "max_position_pct": rm.max_position_pct,
            "max_order_pct": rm.max_order_pct,
            "max_order_value": round(rm.max_order_value(total_assets), 2),
            "max_daily_loss": rm.max_daily_loss,
            "min_cash_balance": rm.min_cash_balance,
        }
    )


@router.get("/daily-report", summary="日报表")
async def get_daily_report(_: User = Depends(get_current_user)):
    return Response.success(data=get_trading_service().get_daily_report())


@router.get("/trade-history", summary="交易历史（模拟撮合服务内存视图）")
async def get_trade_history(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _: User = Depends(get_current_user),
):
    return Response.success(
        data=get_trading_service().get_trade_history(start_date, end_date)
    )


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
    mode: str = "paper"


@router.post("/orders/internal", summary="内部下单（策略调仓）")
async def internal_place_order(
    payload: InternalOrderRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_internal_token),
):
    await ensure_tables()
    coordinator = TradingCoordinator(db, payload.mode)
    order = await coordinator.place_order(
        symbol=payload.symbol,
        side=payload.side,
        order_type=payload.order_type,
        quantity=payload.quantity,
        price=payload.price,
        source="strategy",
        user_id=payload.user_id,
    )
    return Response.success(
        data={
            "order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "filled_quantity": order.filled_quantity,
            "status": order.status,
            "message": order.message or "",
        }
    )


@router.get("/account/internal", summary="内部账户查询")
async def internal_account(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_internal_token),
):
    await ensure_tables()
    overview = await TradingCoordinator(db, "paper").get_overview()
    return Response.success(
        data={
            "cash_balance": overview["cash_balance"],
            "total_asset": overview["total_assets"],
            "market_value": overview["market_value"],
            "realized_pnl": overview["total_pnl"],
            "unrealized_pnl": overview["unrealized_pnl"],
            "daily_pnl": overview["total_pnl"],
        }
    )


@router.get("/positions/internal", summary="内部持仓查询")
async def internal_positions(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_internal_token),
):
    """策略调仓读取当前持仓。

    注意：字段名 `market_price` 是 data-cleaner 调仓侧的既有约定
    （app/strategy/rebalance.py 读取 symbol / quantity / market_price），不要改名。
    """
    await ensure_tables()
    positions = await TradingCoordinator(db, "paper").list_positions()
    return Response.success(
        data=[
            {
                "symbol": p.symbol,
                "side": p.side,
                "quantity": p.quantity,
                "avg_price": p.avg_price,
                "market_price": p.last_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
            }
            for p in positions
        ]
    )
