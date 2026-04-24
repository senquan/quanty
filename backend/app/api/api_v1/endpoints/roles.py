from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.role import Role
from app.schemas.role import RoleCreate, RoleUpdate, RoleInDB, RoleWithPermissions
from app.models.role_permission import RolePermission
from app.models.menu import Menu

router = APIRouter()

@router.get("/", response_model=List[RoleInDB])
async def get_roles(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """获取角色列表"""
    query = select(Role)
    if search:
        query = query.where(Role.name.contains(search))
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    roles = result.scalars().all()
    return roles

@router.get("/{role_id}", response_model=RoleWithPermissions)
async def get_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """获取单个角色信息"""
    result = await db.execute(select(Role).filter(Role.id == role_id))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # H1-4: 获取权限信息
    perm_result = await db.execute(select(RolePermission).filter(RolePermission.role_id == role_id))
    permissions = perm_result.scalars().all()
    
    role_dict = role.__dict__.copy()
    role_dict.pop('_sa_instance_state', None)
    role_dict['permissions'] = [perm.__dict__ for perm in permissions]
    
    return role_dict

@router.post("/", response_model=RoleInDB)
async def create_role(
    role_data: RoleCreate,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """创建角色"""
    existing_role = await db.execute(select(Role).where(Role.name == role_data.name))
    if existing_role.scalars().first():
        raise HTTPException(status_code=400, detail="角色名已存在")
    
    new_role = Role(
        name=role_data.name,
        description=role_data.description,
        is_active=role_data.is_active
    )
    
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)
    
    return new_role

@router.put("/{role_id}", response_model=RoleInDB)
async def update_role(
    role_id: int,
    role_data: RoleUpdate,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """更新角色信息"""
    result = await db.execute(select(Role).filter(Role.id == role_id))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    if role_data.name and role_data.name != role.name:
        existing_role = await db.execute(select(Role).where(Role.name == role_data.name))
        if existing_role.scalars().first():
            raise HTTPException(status_code=400, detail="角色名已存在")
    
    # H1-5: dict() → model_dump()
    update_dict = role_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(role, field, value)
    
    await db.commit()
    await db.refresh(role)
    
    return role

@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """删除角色"""
    result = await db.execute(select(User).filter(User.role_id == role_id))
    user_count = len(result.scalars().all())
    if user_count > 0:
        raise HTTPException(status_code=400, detail="该角色正在被用户使用，无法删除")
    
    # 删除角色关联的权限
    await db.execute(RolePermission.delete().where(RolePermission.role_id == role_id))
    
    result2 = await db.execute(select(Role).where(Role.id == role_id))
    role = result2.scalars().first()
    if role:
        await db.delete(role)
    
    await db.commit()
    
    return {"message": "角色删除成功"}

@router.put("/{role_id}/status")
async def update_role_status(
    role_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """更新角色状态"""
    result = await db.execute(select(Role).filter(Role.id == role_id))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    role.is_active = is_active
    await db.commit()
    await db.refresh(role)
    
    return {"message": "角色状态更新成功"}

@router.post("/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    menu_ids: List[int],
    db: AsyncSession = Depends(get_db),  # H1-1: sync → async
    current_user: User = Depends(get_current_user)
):
    """设置角色权限"""
    result = await db.execute(select(Role).filter(Role.id == role_id))
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 删除现有权限
    await db.execute(RolePermission.delete().where(RolePermission.role_id == role_id))
    
    # 添加新权限
    for menu_id in menu_ids:
        menu_result = await db.execute(select(Menu).where(Menu.id == menu_id))
        menu = menu_result.scalars().first()
        if menu:
            permission = RolePermission(
                role_id=role_id,
                menu_id=menu_id,
                can_view=True,
                can_add=True,
                can_edit=True,
                can_delete=True
            )
            db.add(permission)
    
    await db.commit()
    
    return {"message": "权限设置成功"}
