"""交易域持久化模型（模拟盘 / 实盘）

模拟盘与实盘共用同一套结构，统一以 `mode` 字段区分（paper / live）：
- 概览页与交易管理页无需区分模式即可展示与操作；
- 落库后持仓、订单、成交不再随进程重启丢失（原实现在 huatai_trading 内存单例中）。
"""
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.core.database import Base

MODE_PAPER = "paper"
MODE_LIVE = "live"

# 订单状态（与 huatai_trading.OrderStatus 的 value 保持一致）
ORDER_PENDING = "PENDING"
ORDER_FILLED = "FILLED"
ORDER_CANCELLED = "CANCELLED"
ORDER_REJECTED = "REJECTED"


class TradingAccount(Base):
    """交易账户（资金池）：按 (mode, strategy_id) 唯一。

    每个策略对应一个独立资金池，相当于一只基金产品；strategy_id 为 NULL 表示
    该模式下未绑定策略的共享账户（历史遗留 / 手动交易）。
    """

    __tablename__ = "trading_accounts"
    __table_args__ = (
        UniqueConstraint("mode", "strategy_id", name="uq_trading_account_mode_strategy"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    strategy_id = Column(Integer, nullable=True, index=True)
    mode = Column(String(16), nullable=False, default=MODE_PAPER, index=True)
    broker = Column(String(32), nullable=False, default="simulated")
    account_id = Column(String(64), nullable=False)  # 券商/模拟器返回的账户号
    initial_capital = Column(Float, nullable=False, default=0.0)
    cash_balance = Column(Float, nullable=False, default=0.0)
    frozen_cash = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TradingPosition(Base):
    """当前持仓（每账户每标的一行，卖清后删除）

    strategy_id 冗余存储，便于按策略直接归因与查询，不必先经账户反查。
    """

    __tablename__ = "trading_positions"
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", "side", name="uq_position_acc_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, index=True)
    strategy_id = Column(Integer, nullable=True, index=True)
    mode = Column(String(16), nullable=False, default=MODE_PAPER, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False, default="LONG")
    quantity = Column(Integer, nullable=False, default=0)
    avg_price = Column(Float, nullable=False, default=0.0)
    last_price = Column(Float, nullable=False, default=0.0)
    prev_close = Column(Float, nullable=False, default=0.0)  # 上一交易日收盘价；盘后估值重定价前快照
    market_value = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TradingOrder(Base):
    """下单记录（含被拒单与撤单）"""

    __tablename__ = "trading_orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_order_client_id"),
        Index("ix_trading_orders_mode_created", "mode", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, index=True)
    mode = Column(String(16), nullable=False, default=MODE_PAPER, index=True)
    client_order_id = Column(String(64), nullable=False)  # 幂等：防重复提交
    broker_order_id = Column(String(64), nullable=True)  # 券商侧订单号
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # BUY / SELL
    order_type = Column(String(8), nullable=False, default="LIMIT")  # MARKET / LIMIT
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)
    filled_quantity = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default=ORDER_PENDING, index=True)
    message = Column(Text, nullable=True)  # 拒单/失败原因
    source = Column(String(16), nullable=False, default="manual")  # manual / strategy
    strategy_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    filled_at = Column(DateTime(timezone=True), nullable=True)


class TradingTrade(Base):
    """成交明细（一笔订单可多笔成交）

    strategy_id 冗余存储，便于按策略归因。
    """

    __tablename__ = "trading_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, nullable=False, index=True)
    account_id = Column(Integer, nullable=False, index=True)
    strategy_id = Column(Integer, nullable=True, index=True)
    mode = Column(String(16), nullable=False, default=MODE_PAPER, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    commission = Column(Float, nullable=False, default=0.0)
    trade_time = Column(DateTime(timezone=True), server_default=func.now())


class TradingRebalanceRecord(Base):
    """调仓执行记录（backend 侧持有）

    原由 data-cleaner 写入 `factor.factor_strategy_executions`；职责归位后
    由 backend 编排并落本地库，并补上 `mode` 字段以区分模拟盘/实盘。

    `uq_rebalance_strategy_date_mode` 是重复调仓的幂等兜底：即使调度器被多个
    实例同时触发，同一 (策略, 调仓日, 模式) 也只会有一条记录。
    """

    __tablename__ = "trading_rebalance_records"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id", "rebalance_date", "mode", name="uq_rebalance_strategy_date_mode"
        ),
        Index("ix_rebalance_records_date", "rebalance_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    strategy_name = Column(String(128), nullable=True)
    mode = Column(String(16), nullable=False, default=MODE_PAPER)
    rebalance_date = Column(Date, nullable=False)
    trade_date = Column(Date, nullable=True)
    target_count = Column(Integer, nullable=True)
    orders_placed = Column(Integer, nullable=True)
    amount = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="success")  # success/error/skipped
    detail = Column(Text, nullable=True)  # JSON 字符串：订单明细 / 错误信息
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PortfolioDailyValue(Base):
    """组合（账户 / 模式）每日盘后市值与收益快照

    由 backend 定时任务（见 portfolio_valuation_service.run_eod_valuation）在
    交易日盘后从 data-cleaner 拉最新价（market_proxy.latest_prices）后写入，
    供 dashboard 直接读取市值曲线与收益率，无需实时重算。

    `strategy_id` 预留：当前持仓按 mode 共享一个账户，组合 = 账户级；
    若后续持仓按策略拆分，可按 strategy_id 记录每策略组合快照。
    """

    __tablename__ = "portfolio_daily_values"
    __table_args__ = (
        UniqueConstraint("mode", "strategy_id", "value_date", name="uq_portfolio_value"),
        Index("ix_portfolio_value_date", "value_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String(16), nullable=False, default=MODE_PAPER, index=True)
    strategy_id = Column(Integer, nullable=True, index=True)  # NULL = 账户级组合
    value_date = Column(Date, nullable=False)
    cash_balance = Column(Float, nullable=False, default=0.0)
    market_value = Column(Float, nullable=False, default=0.0)
    total_assets = Column(Float, nullable=False, default=0.0)
    daily_return = Column(Float, nullable=True)       # 相对前一交易日
    cumulative_return = Column(Float, nullable=True)  # 相对初始资金
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Instrument(Base):
    """标的主数据（backend 自持）：代码 → 中文名等展示信息。

    名字源来自 data-cleaner 的只读元数据接口（GET /strategy/instruments/metadata，
    数据源自 factor.industries），首次缺失时由 backend /trading/symbols/metadata
    懒回填并缓存到此表。与 market_proxy 取价同一边界：backend 存、dc 供。
    """

    __tablename__ = "instruments"
    __table_args__ = (Index("ix_instruments_symbol", "symbol"),)

    symbol = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False, default="")
    exchange = Column(String(8), nullable=True)        # SH / SZ / BJ，按 symbol 后缀推导
    industry = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
