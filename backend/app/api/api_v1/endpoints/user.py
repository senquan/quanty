from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.role import Role
from app.models.user import User
from app.schemas.user_info import UserInfoResponse
from app.schemas.response import Response

router = APIRouter()

DEFAULT_HOME_PATH = "/analytics"


@router.get("/info", response_model=Response[UserInfoResponse])
async def get_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户信息（供前端路由守卫与个人中心使用）"""
    roles: list[str] = []
    if current_user.role_id:
        result = await db.execute(select(Role).where(Role.id == current_user.role_id))
        role = result.scalars().first()
        if role:
            roles.append(role.name)

    if not roles:
        roles = ["user"]

    user_info = UserInfoResponse(
        avatar="",
        realName=current_user.realname or current_user.username,
        roles=roles,
        userId=str(current_user.id),
        username=current_user.username,
        desc=current_user.email or "",
        homePath=DEFAULT_HOME_PATH,
        token="",
    )
    return Response.success(data=user_info)
