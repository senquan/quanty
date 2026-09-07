from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, func, or_
from typing import List, Optional
from datetime import datetime, timedelta
import pandas as pd

from app.core.database import get_db
from app.models.quant import Strategy, BacktestResult as BacktestResultModel
from app.models.user import User
from app.schemas.quant import (
    StrategyCreate, StrategyResponse, StrategyUpdate, BacktestRequest, 
    BacktestResult, ValidationResult, MarketDataResponse, TradeInfo
)
from app.schemas.response import Response
from app.api.api_v1.endpoints.auth import get_current_user
# R5:回测撮合已全部迁 dc,这两个类从 backtest_engine.py 拆出来单独立户 ——
# 它们服务的是策略 CRUD 校验与行情展示,不是回测。
from app.services.market_data import DataManager
from app.services.strategy_validator import StrategyValidator
from app.services.script_backtest_proxy import (
    ScriptBacktestProxyError, ScriptBacktestRefused, backtest_styles, run_script_backtest,
)

router = APIRouter()


@router.post("/strategies", response_model=Response[StrategyResponse])
async def create_strategy(
    strategy_data: StrategyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建策略"""
    validator = StrategyValidator()
    validation = validator.validate_strategy(strategy_data.code)
    
    if not validation['valid']:
        raise HTTPException(
            status_code=400, 
            detail=f"策略代码验证失败: {', '.join(validation['errors'])}"
        )
    
    strategy = Strategy(
        name=strategy_data.name,
        description=strategy_data.description,
        code=strategy_data.code,
        user_id=current_user.id
    )
    
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)
    
    return Response.success(data=strategy)


@router.get("/strategies", response_model=Response[List[StrategyResponse]])
async def get_strategies(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取策略列表"""
    query = select(Strategy).filter(
        Strategy.user_id == current_user.id
    )
    if search:
        query = query.filter(
            or_(
                Strategy.name.ilike(f"%{search}%"),
                Strategy.description.ilike(f"%{search}%")
            )
        )
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return Response.success(data=result.scalars().all())


@router.get("/strategies/{strategy_id}", response_model=Response[StrategyResponse])
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取单个策略"""
    result = await db.execute(
        select(Strategy).filter(
            Strategy.id == strategy_id,
            Strategy.user_id == current_user.id
        )
    )
    strategy = result.scalars().first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    return Response.success(data=strategy)


@router.put("/strategies/{strategy_id}", response_model=Response[StrategyResponse])
async def update_strategy(
    strategy_id: int,
    strategy_data: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新策略"""
    result = await db.execute(
        select(Strategy).filter(
            Strategy.id == strategy_id,
            Strategy.user_id == current_user.id
        )
    )
    strategy = result.scalars().first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    if strategy_data.code:
        validator = StrategyValidator()
        validation = validator.validate_strategy(strategy_data.code)
        
        if not validation['valid']:
            raise HTTPException(
                status_code=400, 
                detail=f"策略代码验证失败: {', '.join(validation['errors'])}"
            )
    
    update_data = strategy_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(strategy, field, value)
    
    strategy.updated_at = func.now()
    
    await db.commit()
    await db.refresh(strategy)
    
    return Response.success(data=strategy)


@router.delete("/strategies/{strategy_id}", response_model=Response)
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除策略"""
    result = await db.execute(
        select(Strategy).filter(
            Strategy.id == strategy_id,
            Strategy.user_id == current_user.id
        )
    )
    strategy = result.scalars().first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    await db.execute(
        delete(BacktestResultModel).where(BacktestResultModel.strategy_id == strategy_id)
    )
    
    await db.delete(strategy)
    await db.commit()
    
    return Response.success(msg="策略已删除")


@router.post("/validate-strategy", response_model=Response[ValidationResult])
async def validate_strategy(strategy_code: str):
    """验证策略代码"""
    validator = StrategyValidator()
    return Response.success(data=validator.validate_strategy(strategy_code))


@router.get("/market-data", response_model=Response[MarketDataResponse])
async def get_market_data(
    symbol: str = "AAPL",
    data_source: str = "yahoo",
    start_date: str = None,
    end_date: str = None
):
    """获取市场数据"""
    try:
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')
        
        data_manager = DataManager()
        data = data_manager.get_data(data_source, symbol, start_date, end_date)
        
        result = MarketDataResponse(
            symbol=symbol,
            data_source=data_source,
            data=data.reset_index().to_dict('records'),
            columns=list(data.columns)
        )
        return Response.success(data=result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/backtest", response_model=Response[BacktestResult])
async def run_backtest(
    backtest_request: BacktestRequest, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """运行回测（转发 dc）。

    **计算不在 backend**：A 股行情在 dc 的 ``factor.raw_bars``，两库隔离拿不到，
    所以脚本策略回测一律转发 ``POST {dc}/api/v1/backtest/script``（§0 归口决策 / R3）。
    backend 在这里只做三件事：鉴权（策略归属）、转发、落历史。

    闸口在 dc 侧。dc 的 422 是「这个回测不成立」，detail 为
    ``{"reason", "remedy"}`` —— **原样透传**，不能包成 500：
    包成 500 用户只会以为服务挂了，而去翻根本没有错误的日志。
    """
    result = await db.execute(
        select(Strategy).filter(
            Strategy.id == backtest_request.strategy_id,
            Strategy.user_id == current_user.id
        )
    )
    strategy = result.scalars().first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    payload = {
        "symbol": backtest_request.symbol,
        "start": backtest_request.start_date.strftime('%Y-%m-%d'),
        "end": backtest_request.end_date.strftime('%Y-%m-%d'),
        "code": strategy.code,
        "style": backtest_request.style,
        "initial_capital": backtest_request.initial_capital,
        "allow_short": backtest_request.allow_short,
        "price_field": backtest_request.price_field,
        "apply_market_rules": backtest_request.apply_market_rules,
    }

    try:
        bt = await run_script_backtest(db, payload)
    except ScriptBacktestRefused as e:
        # 422 而不是 500：这是「这个请求不成立」，不是服务端出错
        raise HTTPException(status_code=422, detail=e.as_dict()) from e
    except ScriptBacktestProxyError as e:
        raise HTTPException(status_code=502, detail=f"回测服务不可用：{e}") from e

    warnings = list(bt.get("warnings", []))

    # 历史落库失败不该让已经算完的结果白跑一趟 —— 记进 warnings，不阻断返回。
    # （原实现里落库异常会整体 500：计算花了几十秒，最后只因为写历史失败全丢。）
    try:
        backtest_result_model = BacktestResultModel(
            strategy_id=backtest_request.strategy_id,
            start_date=backtest_request.start_date,
            end_date=backtest_request.end_date,
            total_return=bt['total_return'],
            sharpe_ratio=bt['sharpe_ratio'],
            max_drawdown=bt['max_drawdown'],
            win_rate=bt['win_rate'],
            trades_count=bt['total_trades']
        )
        db.add(backtest_result_model)
        await db.commit()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"回测结果已算出，但写入历史失败：{e}")

    trades = [
        TradeInfo(
            type=trade['type'],
            price=trade['price'],
            quantity=trade['quantity'],
            timestamp=trade['timestamp']
        )
        for trade in bt.get('trades', [])
    ]

    backtest_result = BacktestResult(
        strategy_id=backtest_request.strategy_id,
        total_return=bt['total_return'],
        sharpe_ratio=bt['sharpe_ratio'],
        max_drawdown=bt['max_drawdown'],
        win_rate=bt['win_rate'],
        total_trades=bt['total_trades'],
        final_capital=bt['final_capital'],
        trades=trades,
        daily_returns=bt.get('daily_returns', []),
        portfolio_values=bt.get('portfolio_values', []),
        portfolio_dates=bt.get('portfolio_dates', []),
        # 限制与警告必须跟数字一起回到前端 —— 分开看等于没看
        limits=list(bt.get('limits', [])),
        warnings=warnings,
        rejections=bt.get('rejections', []),
        total_fees=float(bt.get('total_fees', 0.0)),
        plan=bt.get('plan'),
        final_position=int(bt.get('final_position', 0)),
        cash=float(bt.get('cash', 0.0)),
        data=bt.get('data'),
    )
    
    return Response.success(data=backtest_result)


@router.get("/backtest/styles", response_model=Response[dict])
async def get_backtest_styles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """回测口径表（转发 dc）。

    口径认哪些值、每个口径最少要多少根 bar，事实来源在 dc 的 ``gate.py``；
    backend 不做第二份定义，只转发，避免前端下拉与闸口对不上。
    """
    try:
        return Response.success(data=await backtest_styles(db))
    except ScriptBacktestProxyError as e:
        raise HTTPException(status_code=502, detail=f"回测服务不可用：{e}") from e


@router.get("/backtest-history/{strategy_id}", response_model=Response)
async def get_backtest_history(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取策略回测历史"""
    result = await db.execute(
        select(Strategy).filter(
            Strategy.id == strategy_id,
            Strategy.user_id == current_user.id
        )
    )
    strategy = result.scalars().first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    history_result = await db.execute(
        select(BacktestResultModel)
        .where(BacktestResultModel.strategy_id == strategy_id)
        .order_by(BacktestResultModel.created_at.desc())
    )
    history = history_result.scalars().all()
    
    return Response.success(data=history)