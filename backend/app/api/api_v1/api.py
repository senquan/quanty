from fastapi import APIRouter
from app.api.api_v1.endpoints import auth, user, users, quant, roles, menu, menus, trading
from app.api.api_v1.endpoints import cleaner, factor_library

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(user.router, prefix="/user", tags=["用户"])
api_router.include_router(users.router, prefix="/users", tags=["用户管理"])
api_router.include_router(roles.router, prefix="/roles", tags=["角色管理"])
api_router.include_router(menu.router, prefix="/menu", tags=["菜单"])
api_router.include_router(menus.router, prefix="/system/menus", tags=["菜单管理"])
api_router.include_router(quant.router, prefix="/quant", tags=["量化"])
api_router.include_router(trading.router, prefix="/trading", tags=["模拟交易"])
api_router.include_router(cleaner.router, tags=["清洗服务"])
api_router.include_router(factor_library.router, tags=["因子库"])
