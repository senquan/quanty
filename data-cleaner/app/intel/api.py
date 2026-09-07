"""intel REST 路由（占位骨架）

本文件不含业务逻辑，仅定义开关门控下可被 frontend / backend 调用的占位端点。
实际实现在后续阶段（P0 RSS / P1 理解层 / P2 画像 / P3 因子化）逐步填充。

路由规划：
  - GET /intel/health   公开健康检查（监控用，始终挂载，由 main.py 的 /api/v1 前缀 + 本文件 prefix 合成）
  - GET /intel/status   受保护占位（需 X-API-Key），返回模块启用状态与待办清单

完整端点清单见设计文档 §9：sources / documents / feed_health / authors / factors / style。
"""
from fastapi import APIRouter, Depends

from app.core.security import verify_api_key
from app.intel.core.config import settings
from app.intel.core.logging import get_logger

logger = get_logger(__name__)

# 公开路由（无 X-API-Key）
public_router = APIRouter(tags=["intel-系统"])
# 受保护路由（需 X-API-Key，与 dc 其他内部端点一致）
router = APIRouter(tags=["intel"], dependencies=[Depends(verify_api_key)])


@public_router.get("/health")
async def intel_health() -> dict:
    """intel 模块健康检查（公开）

    注意：本端点始终可用（即使模块逻辑未实现），用于确认 dc 已挂载 /intel 路由。
    模块是否真正启用以 ``INTEL_ENABLED`` 为准。
    """
    return {
        "status": "healthy",
        "service": "data-cleaner",
        "module": "intel",
        "enabled": bool(settings.INTEL_ENABLED),
    }


@router.get("/status")
async def intel_status() -> dict:
    """intel 模块状态（占位，受保护）

    TODO(P0-P3): 返回摄取源数、待处理文档数、最近因子构建时间等。
    """
    logger.info("intel /status 被调用（占位）", extra={"module": "intel"})
    return {
        "module": "intel",
        "enabled": bool(settings.INTEL_ENABLED),
        "llm_provider": settings.INTEL_LLM_PROVIDER,
        "todo": [
            "P0 摄取层（RSS）",
            "P1 理解层 + LLM 抽取",
            "P2 作者/来源画像",
            "P3 INTL_* 因子化",
        ],
    }
