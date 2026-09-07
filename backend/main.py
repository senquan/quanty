from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.core.config import settings
from app.api.api_v1.api import api_router
from app.schemas.response import Response
from app.services.factor_strategy_proxy import FactorStrategyProxyError


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启停交易调度器；幂等建表、api_key 明文重加密迁移、WS 令牌判空。

    建表放在这里而非依赖接口懒加载：WS handler 在 HTTP 请求上下文之外运行，
    若表未提前建好，首批 signal / execution.report 落库会失败。
    """
    from app.services.cleaner_gateway import ensure_cleaner_tables, migrate_api_keys
    from app.tasks.scheduler import shutdown_scheduler, start_scheduler

    try:
        await ensure_cleaner_tables()
    except Exception as e:  # noqa: BLE001 - 建表失败不应阻断服务启动
        logging.getLogger(__name__).error("幂等建表失败: %s", e)

    # api_key 明文治理：把库内存量明文 api_key 自动重加密（设计文档 §8 / §11）
    try:
        await migrate_api_keys()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("api_key 重加密迁移失败: %s", e)

    # WS 鉴权令牌判空：启用 WS 却未配置令牌 = 握手不校验，属不安全降级
    if getattr(settings, "WS_ENABLED", False) and not getattr(
        settings, "STRAT_INTEGRATION_TOKEN", ""
    ):
        logging.getLogger(__name__).warning(
            "WS_ENABLED=true 但 STRAT_INTEGRATION_TOKEN 为空，WS 握手将不校验令牌！请配置。"
        )

    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="Quant Backend API",
    description="量化交易系统后端API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# Global Exception Handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一包成 ``Response.fail``。

    ⚠️ ``detail`` 不一定是字符串。闸口拒绝给的是 **dict**
    (``{"reason": ..., "remedy": ...}``,见 ``endpoints/quant.py`` 的 422),
    而 ``Response.msg`` 是 ``str`` —— 直接塞进去会在这里二次抛
    ``ValidationError``,把「这个回测不成立」变成 500。
    用户看到的是「服务挂了」,于是去翻根本没有错误的日志。

    ⇒ dict / list 一律放进 ``data``,``msg`` 只留一句人话。
    """
    detail = exc.detail
    if isinstance(detail, (dict, list)):
        msg = "请求被拒绝" if exc.status_code == 422 else "请求失败"
        body = Response.fail(code=exc.status_code, msg=msg, data=detail)
    else:
        body = Response.fail(code=exc.status_code, msg=str(detail))
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=Response.fail(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
            msg="Request Validation Error", 
            data=exc.errors()
        ).model_dump()
    )

@app.exception_handler(FactorStrategyProxyError)
async def factor_proxy_exception_handler(request: Request, exc: FactorStrategyProxyError):
    # 因子策略代理到 data-cleaner 失败（连接不通 / 清洗服务 4xx5xx / 找不到可用实例）。
    # 直接把真实原因透传给前端，避免「保存失败」黑盒。
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=Response.fail(code=502, msg=f"因子服务错误：{str(exc)}").model_dump(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=Response.fail(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            msg=f"Internal Server Error: {str(exc)}"
        ).model_dump()
    )

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include API routes
app.include_router(api_router, prefix="/api/v1")

# WebSocket：dc 长连接（backend 作服务端，1 对多，见 docs/plans/2026-09-04.ws-dc-backend.md）
# 仅在 WS_ENABLED=true 时挂载；关闭时端点不存在，行为与改造前完全一致（纯 HTTP 轮询）。
if getattr(settings, "WS_ENABLED", False):
    from app.ws.server import router as ws_router

    app.include_router(ws_router)

@app.get("/")
async def root():
    return {"message": "Quant Backend API 服务正常运行"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )