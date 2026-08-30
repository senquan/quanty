"""API v1 路由聚合"""
from fastapi import APIRouter, Depends

from app.api.v1 import analytics, data, factor, health, metrics, pipeline, qos, raw, strategy
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
