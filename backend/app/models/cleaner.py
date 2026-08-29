"""清洗服务注册 + 因子聚合注册表（阶段 B）

- CleanerService: 主后端登记的外部清洗服务（host / key / 状态 / 最近心跳）
- FactorRegistry: 聚合因子底册，记录每个因子由哪个 service 提供，避免重复入库
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CleanerService(Base):
    __tablename__ = "cleaner_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))          # 不含末尾斜杠，如 http://host:8100
    api_key: Mapped[str] = mapped_column(String(256))           # X-API-Key，AES 存储占位
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # online|offline|degraded|unknown
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    qos: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # 最近一次 QoS 快照
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    factors: Mapped[list["FactorRegistry"]] = relationship(
        "FactorRegistry", back_populates="service", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "service_code": self.service_code,
            "name": self.name,
            "base_url": self.base_url,
            "status": self.status,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "qos": self.qos,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FactorRegistry(Base):
    __tablename__ = "factor_registry"
    __table_args__ = (
        UniqueConstraint("service_code", "factor_code", name="uq_service_factor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_code: Mapped[str] = mapped_column(String(64), ForeignKey("cleaner_services.service_code"), index=True)
    factor_code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)   # 后台勾选入库
    last_sync: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # 清洗服务原始口径
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    service: Mapped["CleanerService"] = relationship("CleanerService", back_populates="factors")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "service_code": self.service_code,
            "factor_code": self.factor_code,
            "name": self.name,
            "category": self.category,
            "frequency": self.frequency,
            "description": self.description,
            "formula": self.formula,
            "data_source": self.data_source,
            "is_enabled": self.is_enabled,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
        }
