from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.security import get_password_hash
from app.models.role import Role
from app.models.user import User
from app.schemas.user import (
    PaginatedUsers,
    UserCreate,
    UserResponse,
    UserUpdate,
    UserWithRole,
)

router = APIRouter()


async def serialize_user(
    user: User, db: AsyncSession, include_role: bool = True
) -> UserWithRole:
    role_data = None
    if include_role and user.role_id:
        result = await db.execute(select(Role).where(Role.id == user.role_id))
        role = result.scalars().first()
        if role:
            role_data = {"id": role.id, "name": role.name}

    return UserWithRole(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.realname,
        phone=user.phone,
        is_active=user.is_active == 1,
        role_id=user.role_id,
        created_at=user.created_at,
        updated_at=user.updated_at,
        role=role_data,
    )


def _search_filter(query, search: str):
    pattern = f"%{search}%"
    return query.where(
        or_(
            User.username.ilike(pattern),
            User.email.ilike(pattern),
            User.realname.ilike(pattern),
        )
    )


@router.get("/", response_model=PaginatedUsers)
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取用户列表"""
    query = select(User)
    count_query = select(func.count(User.id))

    if search:
        query = _search_filter(query, search)
        count_query = _search_filter(count_query, search)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(User.id.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    items = [await serialize_user(user, db) for user in users]
    return PaginatedUsers(items=items, total=total)


@router.get("/{user_id}", response_model=UserWithRole)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个用户信息"""
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return await serialize_user(user, db)


@router.post("/", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建用户"""
    result = await db.execute(
        select(User).where(
            (User.username == user_data.username) | (User.email == user_data.email)
        )
    )
    existing_user = result.scalars().first()

    if existing_user:
        if existing_user.username == user_data.username:
            raise HTTPException(status_code=400, detail="用户名已存在")
        if existing_user.email == user_data.email:
            raise HTTPException(status_code=400, detail="邮箱已存在")

    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password,
        realname=user_data.full_name,
        phone=user_data.phone,
        is_active=1 if user_data.is_active else 0,
        role_id=user_data.role_id,
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return await serialize_user(new_user, db, include_role=False)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新用户信息"""
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user_data.email:
        existing_user = await db.execute(
            select(User).where(User.email == user_data.email, User.id != user_id)
        )
        if existing_user.scalars().first():
            raise HTTPException(status_code=400, detail="邮箱已存在")

    if user_data.username:
        existing_user = await db.execute(
            select(User).where(User.username == user_data.username, User.id != user_id)
        )
        if existing_user.scalars().first():
            raise HTTPException(status_code=400, detail="用户名已存在")

    update_dict = user_data.model_dump(exclude_unset=True)
    if "is_active" in update_dict:
        update_dict["is_active"] = 1 if update_dict["is_active"] else 0
    if "full_name" in update_dict:
        update_dict["realname"] = update_dict.pop("full_name")

    for field, value in update_dict.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    return await serialize_user(user, db, include_role=False)


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除用户"""
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.delete(user)
    await db.commit()

    return {"message": "用户删除成功"}


@router.put("/{user_id}/status")
async def update_user_status(
    user_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新用户状态"""
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.is_active = 1 if is_active else 0
    await db.commit()
    await db.refresh(user)

    return {"message": "用户状态更新成功"}
