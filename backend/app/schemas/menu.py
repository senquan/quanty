from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class MenuBase(BaseModel):
    name: str
    path: Optional[str] = None
    component: Optional[str] = None
    label: Optional[str] = None
    icon: Optional[str] = None
    parent_id: int = 0
    is_enabled: bool = True
    permission: Optional[str] = None

class MenuCreate(MenuBase):
    pass

class MenuUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    label: Optional[str] = None
    component: Optional[str] = None
    icon: Optional[str] = None
    parent_id: Optional[int] = None
    is_enabled: Optional[bool] = None
    permission: Optional[str] = None

class MenuInDB(MenuBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True