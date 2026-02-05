from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base

class Menu(Base):
    """菜单模型：表示系统中的菜单项，支持目录、菜单和按钮三种类型。"""
    __tablename__ = "menus"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    type = Column(Integer, default=1)
    path = Column(String(200), nullable=False)
    label = Column(String(50), nullable=False)
    component = Column(String(200))
    icon = Column(String(50))
    oidx = Column(Integer, default=0)
    parent_id = Column(Integer, default=None)
    link = Column(String(100))
    is_enabled = Column(Boolean, default=True)
    is_cached = Column(Boolean, default=False)
    is_hidden = Column(Boolean, default=False)
    is_embedded = Column(Boolean, default=False)
    is_tag_fixed = Column(Boolean, default=False)
    is_tag_hidden = Column(Boolean, default=False)
    is_full_screen = Column(Boolean, default=False)
    permission = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 关系
    # permissions = relationship("RolePermission", back_populates="menu")
