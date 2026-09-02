"""清洗服务 + 因子注册表的请求/响应 Schema（阶段 B）"""
from datetime import datetime

from pydantic import BaseModel, Field


# ---------------- 清洗服务 ----------------
class CleanerServiceCreate(BaseModel):
    service_code: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    base_url: str = Field(..., max_length=512)
    api_key: str = Field(..., min_length=1, max_length=256)


class CleanerServiceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class CleanerServiceOut(BaseModel):
    id: int
    service_code: str
    name: str
    base_url: str
    status: str
    last_heartbeat: datetime | None = None
    qos: dict | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------- 因子注册表 ----------------
class FactorRegistryOut(BaseModel):
    id: int
    service_code: str
    factor_code: str
    name: str
    category: str | None = None
    frequency: str | None = None
    description: str | None = None
    formula: str | None = None
    data_source: str | None = None
    is_enabled: bool
    last_sync: datetime | None = None
    metrics: dict | None = None
    metrics_synced_at: datetime | None = None


class FactorListQuery(BaseModel):
    """批量勾选入库请求体"""
    service_code: str | None = None
    factor_codes: list[str] | None = None   # 为空表示操作该 service 下全部
    is_enabled: bool = True


# ---------------- 远端因子库（分页选择入库） ----------------
class FactorImportRequest(BaseModel):
    """勾选入库请求体：factor_codes 为空表示全量导入"""
    factor_codes: list[str] = Field(default_factory=list)
    is_enabled: bool = True


class RemoteFactorOut(BaseModel):
    """清洗服务侧因子条目 + 本地入库状态"""
    code: str
    name: str = ""
    category: str | None = None
    frequency: str | None = None
    description: str | None = None
    formula: str | None = None
    data_source: str | None = None
    imported: bool = False          # 是否已在 factor_registry
    is_enabled: bool = False        # 是否已勾选入库


class RemoteFactorPage(BaseModel):
    items: list[RemoteFactorOut]
    total: int
    page: int
    page_size: int


class ConnectionTestResult(BaseModel):
    ok: bool
    status: str | None = None                # data-cleaner 返回的 online/degraded
    factor_count: int | None = None
    message: str | None = None
