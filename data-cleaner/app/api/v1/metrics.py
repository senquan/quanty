"""Prometheus 风格指标端点（Phase 4 可观测性）"""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core import metrics

router = APIRouter(tags=["监控"])


@router.get("/metrics", response_class=PlainTextResponse)
async def get_metrics():
    return metrics.render()
