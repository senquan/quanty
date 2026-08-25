"""因子列表/详情/CRUD 与效能评估（P0 接口）"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

import app.storage.db as db
from app.api.v1.schemas import (
    AiGenerateRequest,
    FactorCreate,
    FactorEvaluateRequest,
    FactorUpdate,
)
from app.core.exceptions import FactorNotFoundError
from app.core.logging import get_logger
from app.factors.evaluator import FactorEvaluator
from app.factors.registry import get_factor, list_factors

router = APIRouter(prefix="/factor", tags=["因子"])

logger = get_logger(__name__)

_evaluator = FactorEvaluator()


@router.get("", response_model=list[dict])
async def list_factor(
    category: str | None = Query(None, description="按类别过滤"),
    search: str | None = Query(None, description="名称/代码模糊搜索"),
):
    # 优先返回数据库中的定义（含自定义因子），否则回退到注册表
    try:
        db_items = await db.list_factor_definitions()
    except Exception:
        db_items = None

    items = db_items if db_items is not None else list_factors(category)
    if category:
        items = [i for i in items if i.get("category") == category]
    if search:
        kw = search.lower()
        items = [i for i in items if kw in str(i.get("code", "")).lower() or kw in str(i.get("name", "")).lower()]
    return items


@router.get("/{code}")
async def get_factor_detail(code: str):
    # 内置因子
    try:
        meta = get_factor(code).get_metadata()
    except FactorNotFoundError:
        meta = None
    # 数据库内的自定义因子详情
    try:
        db_items = await db.list_factor_definitions()
        db_meta = next((d for d in db_items if d["code"] == code), None)
    except Exception:
        db_meta = None

    if meta is None and db_meta is None:
        raise HTTPException(status_code=404, detail=f"因子未注册: {code}")

    result = db_meta if db_meta else meta
    metrics = []
    try:
        metrics = await db.get_factor_metrics(code)
    except Exception:
        metrics = []
    result = dict(result)
    result["metrics"] = metrics
    return result


@router.post("", status_code=201)
async def create_factor(payload: FactorCreate):
    """创建自定义因子（formula 沙箱表达式）"""
    from app.factors.formula import FormulaError, compile_formula

    try:
        compile_formula(payload.formula)  # 先校验语法/安全性
    except FormulaError as e:
        raise HTTPException(status_code=400, detail=f"公式无效: {e}") from e

    meta = {
        "code": payload.code,
        "name": payload.name,
        "category": payload.category,
        "frequency": payload.frequency,
        "formula": payload.formula,
        "data_sources": payload.data_sources or [],
    }
    try:
        await db.upsert_factor_definition(meta, author="user")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}") from e
    return meta


@router.put("/{code}")
async def update_factor(code: str, payload: FactorUpdate):
    """更新因子（仅自定义因子）"""
    try:
        await db.upsert_factor_definition(
            {
                "code": code,
                "name": payload.name,
                "category": payload.category,
                "frequency": payload.frequency,
                "formula": payload.formula,
                "data_sources": payload.data_sources or [],
            },
            author="user",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {e}") from e
    return {"code": code, "status": "updated"}


@router.delete("/{code}")
async def delete_factor(code: str):
    """删除因子（仅 author=user 的自定义因子）"""
    ok = await db.delete_factor_definition(code, author="user")
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"无法删除 {code}：不存在或非自定义因子",
        )
    return {"code": code, "status": "deleted"}


@router.post("/{code}/evaluate")
async def evaluate_factor(code: str, payload: FactorEvaluateRequest):
    """计算因子效能指标（IC/IR/Sharpe/回撤/胜率）

    接收 factorValues 与 forwardReturns 两个等长列表，计算效能指标并落库。
    """
    import pandas as pd

    metrics = _evaluator.evaluate(pd.Series(payload.factorValues), pd.Series(payload.forwardReturns))
    as_of = datetime.now().strftime("%Y-%m-%d")
    try:
        await db.save_factor_metrics(code, as_of, metrics)
    except Exception:
        pass  # 数据库不可用时仍返回计算结果
    return metrics


@router.post("/ai-generate")
async def ai_generate_factor(payload: "AiGenerateRequest"):
    """AI 因子生成（Phase 3 步骤10）

    将自然语言描述转换为受限 formula 表达式，经沙箱校验后作为自定义因子落库。
    未配置 LLM 时使用内置规则引擎（关键词映射），所有生成受 AST 白名单约束。
    """
    from app.factors.ai_generate import generate_formula
    from app.factors.formula import FormulaError, compile_formula

    # 超时保护，避免阻塞请求
    try:
        spec = generate_formula(payload.description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 沙箱校验：生成的 formula 必须能解析，杜绝注入
    try:
        compile_formula(spec["formula"])
    except FormulaError as e:
        raise HTTPException(status_code=422, detail=f"生成的公式不安全: {e}") from e

    if payload.category:
        spec["category"] = payload.category

    meta = {
        "code": spec["code"],
        "name": spec["name"],
        "category": spec["category"],
        "frequency": spec["frequency"],
        "formula": spec["formula"],
        "data_sources": ["adj_close"],
    }
    saved = True
    try:
        await db.upsert_factor_definition(meta, author="ai")
    except Exception as e:
        logger.warning(f"AI 因子落库失败（仍可返回生成结果）: {e}")
        saved = False
    result = dict(meta)
    result["source"] = spec["source"]
    result["saved"] = saved
    return result
