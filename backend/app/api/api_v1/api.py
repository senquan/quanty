from fastapi import APIRouter
from app.api.api_v1.endpoints import auth, users, quant, roles, menus, trading

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(users.router, prefix="/users", tags=["用户管理"])
api_router.include_router(roles.router, prefix="/roles", tags=["角色管理"])
api_router.include_router(menus.router, prefix="/system/menus", tags=["菜单管理"])
api_router.include_router(quant.router, prefix="/quant", tags=["量化"])
api_router.include_router(trading.router, prefix="/trading", tags=["模拟交易"])