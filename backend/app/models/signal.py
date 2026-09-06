"""信号与成交回报（**预留通道**的落库表）

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §6 / §7）：

- **两种模式**（本次仅建模 + 落库，**不实现信号生成**）：
  - 模式 A（信号模式）：backend 提交策略 → dc 计算 → 推 `signal.generated`
    → **backend 走风控与最终决策** → 执行。
  - 模式 B（直接成交）：dc 直接撮合成交 → 回推 `execution.report`
    → backend 记账 / 感知持仓变化。
- **幂等**：WS 是"至少一次投递"，故两张表都用**业务唯一键**兜底：
  - `signals`：`UNIQUE (strategy_id, as_of)` —— 同一策略同一信号日只留一条；
  - `execution_reports`：`UNIQUE (report_id)`。
- 类型选择遵循 PostgreSQL 规范：金额用 `NUMERIC`（绝不用浮点）、
  字符串用 `TEXT`、半结构化用 JSON。

时间与 JSONB 的两点**有意偏离**（与项目现状保持一致，避免同一库内风格撕裂）：
- 时间列沿用项目既有的 `timestamp without time zone` + `datetime.now()`
  （而非 `TIMESTAMPTZ`）。系统统一运行于 Asia/Shanghai，且 `cleaner_services` /
  `factor_registry` 已是该风格；注意 asyncpg 会拒绝带时区值写入无时区列，
  故入口侧必须先归一为朴素时间（见 `app/ws/handlers._parse_datetime`）。
- JSON 列沿用项目既有的 `JSON`（而非 `JSONB`），避免与 alembic 现状不一致。
  若后续启用 JSONB，建议同步加 GIN 索引。

注意：本模块只定义表结构；生成信号的计算逻辑仍在 dc 侧，本轮不实现。
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 与 cleaner.py 保持一致：backend 现有表统一用 JSON 列（JSONB 需 PG 方言扩展，
# 此处沿用项目既有风格，避免与 alembic 现状不一致）
_JSON = JSON


class Signal(Base):
    """dc 推送的交易信号（模式 A）。

    落库后由 backend 的风控与最终决策流程消费；本表只负责"收到并留痕"。
    """

    __tablename__ = "signals"
    __table_args__ = (
        # 业务幂等键：同一策略 + 同一信号日只允许一条（防 WS 重复推送造成重复决策）
        UniqueConstraint("strategy_id", "as_of", name="uq_signals_strategy_as_of"),
        Index("ix_signals_status_received", "status", "received_at"),
        Index("ix_signals_service_code", "service_code"),
        Index("ix_signals_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    #: 来源 dc 实例（连接注册表键）
    instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: 归属清洗服务（对应 cleaner_services.service_code）
    service_code: Mapped[str] = mapped_column(Text, nullable=False)
    #: 策略标识（backend 侧策略 ID）
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: 信号日 T
    as_of: Mapped[datetime] = mapped_column(Date, nullable=False)
    #: 拟交易日 T+1（可空，由 backend 决策时推导）
    trade_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)

    #: 目标持仓明细 [{symbol, score, weight, direction, industry, z_scores}]
    holdings: Mapped[dict | list] = mapped_column(_JSON, nullable=False, default=list)
    #: 诊断信息 {hard_filter, weights}，便于解释"为什么选它"
    diagnostics: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    #: 持仓条数（冗余，便于快速统计与告警）
    holdings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: 处理状态：pending 待风控 → approved/rejected → executed 已执行 / expired 过期
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    #: WS 消息 id（用于追溯与排重排查）
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "service_code": self.service_code,
            "strategy_id": self.strategy_id,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "holdings": self.holdings,
            "diagnostics": self.diagnostics,
            "holdings_count": self.holdings_count,
            "status": self.status,
            "message_id": self.message_id,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }


class ExecutionReport(Base):
    """dc 直接撮合成交后的成交回报（模式 B）。

    落库后 backend 可据此感知持仓与账本变化。
    """

    __tablename__ = "execution_reports"
    __table_args__ = (
        Index("ix_exec_reports_strategy", "strategy_id", "executed_at"),
        Index("ix_exec_reports_service_code", "service_code"),
        Index("ix_exec_reports_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    #: dc 侧幂等键（来自 WS payload），全表唯一防重复记账
    report_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    service_code: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 成交时间（dc 侧产生）
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    #: 成交明细 [{symbol, direction, qty, price, amount, fee, order_id}]
    fills: Mapped[dict | list] = mapped_column(_JSON, nullable=False, default=list)
    fill_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 金额用 NUMERIC，绝不用浮点
    gross_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    fee_total: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)

    #: received 已入库 / applied 已回写持仓 / rejected 被拒绝
    status: Mapped[str] = mapped_column(Text, nullable=False, default="received")
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "report_id": self.report_id,
            "instance_id": self.instance_id,
            "service_code": self.service_code,
            "strategy_id": self.strategy_id,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "fills": self.fills,
            "fill_count": self.fill_count,
            "gross_amount": float(self.gross_amount) if self.gross_amount is not None else None,
            "fee_total": float(self.fee_total) if self.fee_total is not None else None,
            "status": self.status,
            "message_id": self.message_id,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
        }
