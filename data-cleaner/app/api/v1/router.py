"""API v1 路由聚合"""
from fastapi import APIRouter, Depends

from app.api.v1 import (
    analytics,
    backtest,
    data,
    factor,
    health,
    metrics,
    pipeline,
    qos,
    raw,
    strategy,
)
from app.core.config import settings
from app.core.security import verify_api_key

# 受保护接口：factors / pipeline / raw 需要 X-API-Key（阶段 A 接入认证）
_api_key_dep = [Depends(verify_api_key)]

api_router = APIRouter()
api_router.include_router(health.router)                       # 公开
api_router.include_router(factor.router, dependencies=_api_key_dep)   # 受保护
api_router.include_router(analytics.router)
api_router.include_router(data.router)
api_router.include_router(pipeline.router, dependencies=_api_key_dep)  # 受保护
api_router.include_router(raw.router, dependencies=_api_key_dep)       # 受保护
api_router.include_router(metrics.router)                       # 公开
api_router.include_router(qos.router)                           # 公开（供监控轮询）
api_router.include_router(strategy.router, dependencies=_api_key_dep)  # 受保护
api_router.include_router(backtest.router, dependencies=_api_key_dep)   # 受保护（脚本策略回测，R3）

# 市场情报模块（intel）：dc 内可选子模块，仅 INTEL_ENABLED=true 时挂载。
# 关闭时不 import app.intel.*，故其重型依赖（LLM SDK 等）不会被加载，dc 行为不变。
if settings.INTEL_ENABLED:
    from app.intel.api import public_router as intel_public_router
    from app.intel.api import router as intel_router

    api_router.include_router(intel_public_router, prefix="/intel")  # 公开（/health）
    api_router.include_router(intel_router, prefix="/intel")        # 受保护（/status 等）
