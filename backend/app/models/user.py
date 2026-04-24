from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(100), unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    phone = Column(String(20))
    gander = Column(Integer, default=0)
    realname = Column(String(20))
    is_active = Column(Integer, default=1)  # 0=disabled, 1=active
    role_id = Column(Integer, ForeignKey("roles.id"))  # H1-3: 添加角色关联
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 关系
    # strategies = relationship("_strategy", back_populates="user")
    user_roles = relationship("RolePermission", back_populates="user", uselist=False)