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
from app.services.backtest_engine import (
    BacktestEngine, DataManager, StrategyValidator
)
from app.services.performance_analyzer import PerformanceAnalyzer

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
    """运行回测"""
    try:
        result = await db.execute(
            select(Strategy).filter(
                Strategy.id == backtest_request.strategy_id,
                Strategy.user_id == current_user.id
            )
        )
        strategy = result.scalars().first()
        
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        data_manager = DataManager()
        data = data_manager.get_data(
            backtest_request.data_source,
            backtest_request.symbol,
            backtest_request.start_date.strftime('%Y-%m-%d'),
            backtest_request.end_date.strftime('%Y-%m-%d')
        )
        
        engine = BacktestEngine(initial_capital=backtest_request.initial_capital)
        results = engine.execute_strategy(strategy.code, data)
        metrics = engine.calculate_metrics(results)
        
        backtest_result_model = BacktestResultModel(
            strategy_id=backtest_request.strategy_id,
            start_date=backtest_request.start_date,
            end_date=backtest_request.end_date,
            total_return=metrics['total_return'],
            sharpe_ratio=metrics['sharpe_ratio'],
            max_drawdown=metrics['max_drawdown'],
            win_rate=metrics['win_rate'],
            trades_count=metrics['total_trades']
        )
        
        db.add(backtest_result_model)
        await db.commit()
        await db.refresh(backtest_result_model)
        
        trades = [
            TradeInfo(
                type=trade['type'],
                price=trade['price'],
                quantity=trade['quantity'],
                timestamp=trade['timestamp']
            )
            for trade in results['trades']
        ]
        
        analyzer = PerformanceAnalyzer(
            portfolio_values=results.get('portfolio_values', []),
            trades=results['trades']
        )
        analyzer.comprehensive_analysis()
        
        backtest_result = BacktestResult(
            strategy_id=backtest_request.strategy_id,
            total_return=metrics['total_return'],
            sharpe_ratio=metrics['sharpe_ratio'],
            max_drawdown=metrics['max_drawdown'],
            win_rate=metrics['win_rate'],
            total_trades=metrics['total_trades'],
            final_capital=metrics['final_capital'],
            trades=trades,
            daily_returns=results.get('daily_returns', []),
            portfolio_values=results.get('portfolio_values', [])
        )
        
        return Response.success(data=backtest_result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")


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