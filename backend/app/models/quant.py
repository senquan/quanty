from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base

class Strategy(Base):
    __tablename__ = "strategies"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    code = Column(Text, nullable=False)  # 策略代码
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    # user = relationship("User", back_populates="strategies")
    # backtest_results = relationship("BacktestResult", back_populates="strategy")

class BacktestResult(Base):
    __tablename__ = "backtest_results"
    
    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    total_return: float = Column(Float)  # 总收益率
    sharpe_ratio: float = Column(Float)  # 夏普比率
    max_drawdown: float = Column(Float)  # 最大回撤
    win_rate: float = Column(Float)  # 胜率
    trades_count = Column(Integer)  # 交易次数
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    # strategy = relationship("Strategy", back_populates="backtest_results")

