"""健康检查路由"""
from fastapi import APIRouter

router = APIRouter(tags=["系统"])


@router.get("/health")
async def health_check() -> dict:
    """服务健康检查"""
    return {"status": "healthy", "service": "data-cleaner"}
