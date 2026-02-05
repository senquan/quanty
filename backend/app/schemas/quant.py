from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class StrategyBase(BaseModel):
    name: str
    description: Optional[str] = None

class StrategyCreate(StrategyBase):
    code: str  # 策略代码

class StrategyResponse(StrategyBase):
    id: int
    user_id: int
    code: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class BacktestRequest(BaseModel):
    strategy_id: int
    symbol: str = "AAPL"
    data_source: str = "yahoo"
    start_date: datetime
    end_date: datetime
    initial_capital: float = 100000.0

class TradeInfo(BaseModel):
    type: str
    price: float
    quantity: int
    timestamp: datetime

class BacktestResult(BaseModel):
    strategy_id: int
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    final_capital: float
    trades: List[TradeInfo]
    daily_returns: List[float]
    portfolio_values: List[float]

class ValidationResult(BaseModel):
    valid: bool
    errors: List[str]
    warnings: List[str]

class MarketDataResponse(BaseModel):
    symbol: str
    data_source: str
    data: List[Dict[str, Any]]
    columns: List[str]