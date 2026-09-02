from contextlib import asynccontextmanager

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
    """启停交易调度器（仅当 ENABLE_TRADING_SCHEDULER=true 时启动）。"""
    from app.tasks.scheduler import shutdown_scheduler, start_scheduler

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
    return JSONResponse(
        status_code=exc.status_code,
        content=Response.fail(code=exc.status_code, msg=exc.detail).model_dump()
    )

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