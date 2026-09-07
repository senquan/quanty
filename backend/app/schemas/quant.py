from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class StrategyBase(BaseModel):
    name: str
    description: Optional[str] = None

class StrategyCreate(StrategyBase):
    code: str  # 策略代码

class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None

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
    symbol: str = "600519.SH"
    #: ⚠️ 已废弃（R3）：回测已迁 dc，dc 只有 A 股行情（factor.raw_bars），
    #: 本字段不再参与取数，保留仅为兼容旧前端请求体。
    data_source: str = "yahoo"
    start_date: datetime
    end_date: datetime
    initial_capital: float = 100000.0
    #: 口径：long（长线 ≥480 根）/ swing（短线波段 ≥240 根）/ intraday（做T，暂不支持）
    style: str = "swing"
    #: 声明策略会做空。A 股下只能为 False，闸口会拦。
    allow_short: bool = False
    #: False = 不套用任何市场规则（T+0、无涨跌停、零费用），仅供纯算法验证
    apply_market_rules: bool = True
    #: 复权口径：qfq（真实价，整手/最低佣金/涨跌停判定准，历史值会漂）/
    #: hfq（锚最早日，历史值永不改变、结果可复现，名义价非真实价）
    price_field: str = "qfq"

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
    #: 组合价值对应的交易日（与 portfolio_values 等长）—— 没有它画不出净值曲线
    portfolio_dates: List[str] = []
    #: 闸口给出的限制说明 —— 必须与结果一起呈现，否则看到的只是没有语境的数字
    limits: List[str] = []
    warnings: List[str] = []
    #: 被市场规则挡下的信号（T+1 / 涨跌停 / 整手 / 停牌 / 资金不足）
    rejections: List[Dict[str, Any]] = []
    total_fees: float = 0.0
    plan: Optional[Dict[str, Any]] = None
    #: 期末仍持有的股数（未平仓部分）
    final_position: int = 0
    #: 期末可用现金（与 final_capital 的区别：后者含持仓市值）
    cash: float = 0.0
    #: 数据侧元信息：实际 bar 数、复权口径、hfq 系数 k、区间首末日
    data: Optional[Dict[str, Any]] = None

class ValidationResult(BaseModel):
    valid: bool
    errors: List[str]
    warnings: List[str]

class MarketDataResponse(BaseModel):
    symbol: str
    data_source: str
    data: List[Dict[str, Any]]
    columns: List[str]

# ============ 自选股 ============
class WatchlistItemBase(BaseModel):
    code: str = Field(..., description="股票代码，如 600519.SH")
    name: Optional[str] = Field(None, description="股票名称（可选）")
    note: Optional[str] = Field(None, description="用户备注（可选）")

class WatchlistItemCreate(WatchlistItemBase):
    """新增自选股"""

class WatchlistItemUpdate(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None

class WatchlistItemResponse(WatchlistItemBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class WatchlistBulkCreate(BaseModel):
    items: List[WatchlistItemBase]