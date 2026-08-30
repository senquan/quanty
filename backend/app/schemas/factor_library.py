"""因子底册库请求 Schema（代理到清洗服务）"""
from pydantic import BaseModel, Field


class FactorCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    category: str = Field("custom", max_length=64)
    frequency: str = Field("1d", max_length=32)
    formula: str = Field(..., min_length=1)
    data_sources: list[str] = Field(default_factory=lambda: ["adj_close"])
    description: str | None = None


class FactorUpdateRequest(BaseModel):
    name: str | None = None
    category: str | None = None
    frequency: str | None = None
    formula: str | None = None
    data_sources: list[str] | None = None
    description: str | None = None


class FactorAiGenerateRequest(BaseModel):
    description: str = Field(..., min_length=1)
    category: str | None = None


class FactorCorrelationRequest(BaseModel):
    codes: list[str] = Field(..., min_length=2)
