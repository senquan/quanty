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
from app.ws import events as ws_events

setup_logging("DEBUG" if settings.DEBUG else "INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期：迁移建表 → 启动调度器 → **启动 WS 长连接客户端**

    WS 客户端是可选能力：`WS_ENABLED=false`（默认）时完全不启动，
    行为与改造前一致（纯 HTTP），便于一键回退。
    """
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

    # 启动 dc → backend 的 WebSocket 长连接（失败不得影响主服务）
    ws_client = None
    try:
        from app.ws.client import WSClient

        ws_client = WSClient.from_settings()
        if ws_client is not None:
            ws_events.bind(ws_client)
            await ws_client.start()
    except Exception as e:  # noqa: BLE001 - 长连接不可用绝不影响服务启动
        logger.error(f"WS 客户端启动失败（已降级为纯 HTTP）: {e}")

    yield

    # 优雅下线：先发 presence(offline)，给一点时间冲刷 outbox 后再停
    if ws_client is not None:
        try:
            import asyncio

            # 仅在**已连接**时发下线通知：否则该事件会滞留 outbox，
            # 下次启动被重放，反而把服务短暂标记成 offline。
            if ws_client.is_connected():
                ws_events.emit_presence(False, reason="shutdown")
                await asyncio.sleep(0.5)
            await ws_client.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"WS 客户端停止异常: {e}")

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
