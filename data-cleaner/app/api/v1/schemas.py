"""API v1 Pydantic 模型"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class FactorMeta(BaseModel):
    code: str
    name: str
    category: str
    frequency: str
    dataSources: list[str] | None = None


class FactorDetail(FactorMeta):
    formula: str | None = None
    author: str = "system"
    status: str = "active"
    createdAt: datetime | None = None


class PipelineRunRequest(BaseModel):
    """手动触发清洗+因子计算任务的请求"""
    source: str  # yfinance / ccxt / csv
    symbol: str = ""
    start: str
    end: str
    freq: str = "1d"
    # csv 专用
    csvPath: str | None = None


class PipelineRunResponse(BaseModel):
    status: str
    rowsIn: int
    rowsOut: int
    durationMs: int
    report: list[dict[str, Any]]


class DataBarsQuery(BaseModel):
    symbol: str
    start: str | None = None
    end: str | None = None
    freq: str = "1d"


class FactorCreate(BaseModel):
    """创建自定义因子"""
    code: str
    name: str
    category: str
    frequency: str = "Daily"
    formula: str  # 沙箱表达式，见 app/factors/formula.py
    data_sources: list[str] | None = None


class FactorUpdate(BaseModel):
    """更新自定义因子"""
    name: str | None = None
    category: str | None = None
    frequency: str | None = None
    formula: str | None = None
    data_sources: list[str] | None = None


class FactorEvaluateRequest(BaseModel):
    factorValues: list[float]
    forwardReturns: list[float]


class FactorBatchItem(BaseModel):
    """批量评估中的单个因子"""
    code: str
    factorValues: list[float]
    forwardReturns: list[float]


class FactorBatchEvaluateRequest(BaseModel):
    """批量因子效能评估请求"""
    items: list[FactorBatchItem]
    asOf: str | None = None  # 指定 as_of 日期(YYYY-MM-DD)，缺省用当天


class CorrelationRequest(BaseModel):
    """因子相关性矩阵请求"""
    codes: list[str]


class BacktestRequest(BaseModel):
    """多因子组合回测请求"""
    codes: list[str]
    weights: list[float]


class AiGenerateRequest(BaseModel):
    """AI 因子生成请求（自然语言 -> 因子 formula）"""
    description: str  # 自然语言描述，如 "过去20天的动量因子"
    category: str | None = None  # 可选，强制类别
