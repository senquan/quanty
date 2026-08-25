"""API v1 路由聚合"""
from fastapi import APIRouter

from app.api.v1 import analytics, data, factor, health, metrics, pipeline

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(factor.router)
api_router.include_router(analytics.router)
api_router.include_router(data.router)
api_router.include_router(pipeline.router)
api_router.include_router(metrics.router)
