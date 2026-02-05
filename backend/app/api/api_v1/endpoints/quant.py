from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta
import pandas as pd

from app.core.database import get_db
from app.models.quant import Strategy, BacktestResult as BacktestResultModel
from app.models.user import User
from app.schemas.quant import (
    StrategyCreate, StrategyResponse, BacktestRequest, 
    BacktestResult, ValidationResult, MarketDataResponse, TradeInfo
)
from app.api.api_v1.endpoints.auth import get_current_user
from app.services.backtest_engine import (
    BacktestEngine, DataManager, StrategyValidator
)
from app.services.performance_analyzer import PerformanceAnalyzer

router = APIRouter()

@router.post("/strategies", response_model=StrategyResponse)
async def create_strategy(
    strategy_data: StrategyCreate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 验证策略代码
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
    db.commit()
    db.refresh(strategy)
    
    return strategy

@router.get("/strategies", response_model=List[StrategyResponse])
async def get_strategies(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    strategies = db.query(Strategy).filter(
        Strategy.user_id == current_user.id
    ).offset(skip).limit(limit).all()
    return strategies

@router.get("/strategies/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    strategy = db.query(Strategy).filter(
        Strategy.id == strategy_id,
        Strategy.user_id == current_user.id
    ).first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    return strategy

@router.post("/validate-strategy", response_model=ValidationResult)
async def validate_strategy(strategy_code: str):
    """验证策略代码"""
    validator = StrategyValidator()
    return validator.validate_strategy(strategy_code)

@router.get("/market-data")
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
        
        return MarketDataResponse(
            symbol=symbol,
            data_source=data_source,
            data=data.reset_index().to_dict('records'),
            columns=list(data.columns)
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/backtest", response_model=BacktestResult)
async def run_backtest(
    backtest_request: BacktestRequest, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """运行回测"""
    try:
        # 获取策略
        strategy = db.query(Strategy).filter(
            Strategy.id == backtest_request.strategy_id,
            Strategy.user_id == current_user.id
        ).first()
        
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        # 获取市场数据
        data_manager = DataManager()
        data = data_manager.get_data(
            backtest_request.data_source,
            backtest_request.symbol,
            backtest_request.start_date.strftime('%Y-%m-%d'),
            backtest_request.end_date.strftime('%Y-%m-%d')
        )
        
        # 运行回测引擎
        engine = BacktestEngine(initial_capital=backtest_request.initial_capital)
        results = engine.execute_strategy(strategy.code, data)
        metrics = engine.calculate_metrics(results)
        
        # 保存回测结果到数据库
        backtest_result = BacktestResultModel(
            strategy_id=backtest_request.strategy_id,
            start_date=backtest_request.start_date,
            end_date=backtest_request.end_date,
            total_return=metrics['total_return'],
            sharpe_ratio=metrics['sharpe_ratio'],
            max_drawdown=metrics['max_drawdown'],
            win_rate=metrics['win_rate'],
            trades_count=metrics['total_trades']
        )
        
        db.add(backtest_result)
        db.commit()
        db.refresh(backtest_result)
        
        # 转换交易数据
        trades = [
            TradeInfo(
                type=trade['type'],
                price=trade['price'],
                quantity=trade['quantity'],
                timestamp=trade['timestamp']
            )
            for trade in results['trades']
        ]
        
        # 高级性能分析
        analyzer = PerformanceAnalyzer(
            portfolio_values=results.get('portfolio_values', []),
            trades=results['trades']
        )
        advanced_metrics = analyzer.comprehensive_analysis()
        
        return BacktestResult(
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
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")

@router.get("/backtest-history/{strategy_id}")
async def get_backtest_history(
    strategy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取策略回测历史"""
    # 验证策略所有权
    strategy = db.query(Strategy).filter(
        Strategy.id == strategy_id,
        Strategy.user_id == current_user.id
    ).first()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    # 获取回测历史
    history = db.query(BacktestResultModel).filter(
        BacktestResultModel.strategy_id == strategy_id
    ).order_by(BacktestResultModel.created_at.desc()).all()
    
    return history