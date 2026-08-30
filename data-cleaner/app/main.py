"""data-cleaner 服务入口

数据清洗与因子计算服务：
多源数据接入 → 清洗流水线 → 因子工厂 → 存储 → REST API
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging

setup_logging("DEBUG" if settings.DEBUG else "INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期管理"""
    logger.info("data-cleaner 服务启动", extra={"status": "startup"})
    # 确保因子数据目录存在
    settings.factor_data_path.mkdir(parents=True, exist_ok=True)
    # 幂等建表（因子库 / 行业 / 策略 / 执行记录等），避免策略等表缺失导致保存失败
    from app.storage.db import apply_migrations

    applied = await apply_migrations()
    logger.info(
        "因子库迁移完成",
        extra={"status": "migrated", "count": len(applied)},
    )
    # 启动定时调度（APScheduler）
    from app.tasks.scheduler import start_scheduler

    start_scheduler()
    yield
    from app.tasks.scheduler import shutdown_scheduler

    shutdown_scheduler()
    from app.storage import cache

    await cache.close_redis()
    logger.info("data-cleaner 服务关闭", extra={"status": "shutdown"})


app = FastAPI(
    title="Data Cleaner API",
    description="数据清洗与因子计算服务",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 内部服务，按需收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
