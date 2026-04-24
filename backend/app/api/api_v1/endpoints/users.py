from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserWithRole
from app.core.security import get_password_hash, verify_password

router = APIRouter()

@router.get("/", response_model=List[UserWithRole])
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """获取用户列表"""
    query = select(User)
    
    if search:
        query = query.where(
            (User.username.contains(search)) |
            (User.email.contains(search)) |
            (User.realname.contains(search))  # H1-4: full_name → realname
        )
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    
    # 添加角色信息
    user_list = []
    for user in users:
        user_dict = user.__dict__.copy()
        # 移除 SQLAlchemy internal _sa_instance_state
        user_dict.pop('_sa_instance_state', None)
        user_dict['role'] = user.role.__dict__ if hasattr(user, 'role') and user.role else None
        user_list.append(user_dict)
    
    return user_list

@router.get("/{user_id}", response_model=UserWithRole)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """获取单个用户信息"""
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    user_dict = user.__dict__.copy()
    user_dict.pop('_sa_instance_state', None)
    user_dict['role'] = user.role.__dict__ if hasattr(user, 'role') and user.role else None
    return user_dict

@router.post("/", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """创建用户"""
    # 检查用户名和邮箱是否已存在
    result = await db.execute(select(User).where(
        (User.username == user_data.username) | (User.email == user_data.email)
    ))
    existing_user = result.scalars().first()
    
    if existing_user:
        if existing_user.username == user_data.username:
            raise HTTPException(status_code=400, detail="用户名已存在")
        if existing_user.email == user_data.email:
            raise HTTPException(status_code=400, detail="邮箱已存在")
    
    # 创建新用户 - H1-2: is_active=1 (not is_active=bool from schema)
    # H1-3: role_id from user_data
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password,
        realname=user_data.full_name,  # schema: full_name → model: realname
        phone=user_data.phone,
        is_active=1 if user_data.is_active else 0,  # H1-2: bool → int (0/1)
        role_id=user_data.role_id  # H1-3: 传递 role_id
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return new_user

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """更新用户信息"""
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 检查邮箱是否已被其他用户使用
    if user_data.email:
        existing_user = await db.execute(select(User).where(
            User.email == user_data.email, User.id != user_id
        ))
        if existing_user.scalars().first():
            raise HTTPException(status_code=400, detail="邮箱已存在")
    
    # 检查用户名是否已被其他用户使用
    if user_data.username:
        existing_user = await db.execute(select(User).where(
            User.username == user_data.username, User.id != user_id
        ))
        if existing_user.scalars().first():
            raise HTTPException(status_code=400, detail="用户名已存在")
    
    # 更新用户数据 - H1-5: dict() → model_dump()
    update_dict = user_data.model_dump(exclude_unset=True)  # H1-5
    # Handle is_active type conversion (bool → int)
    if 'is_active' in update_dict:
        update_dict['is_active'] = 1 if update_dict['is_active'] else 0
    # Handle full_name → realname mapping
    if 'full_name' in update_dict:
        update_dict['realname'] = update_dict.pop('full_name')
    
    for field, value in update_dict.items():
        setattr(user, field, value)
    
    await db.commit()
    await db.refresh(user)
    
    return user

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
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
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
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
