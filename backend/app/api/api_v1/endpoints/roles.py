from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取角色列表"""
    query = db.query(Role)
    
    if search:
        query = query.filter(Role.name.contains(search))
    
    total = query.count()
    roles = query.offset(skip).limit(limit).all()
    
    return roles

@router.get("/{role_id}", response_model=RoleWithPermissions)
async def get_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取单个角色信息"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 获取权限信息
    permissions = db.query(RolePermission).filter(
        RolePermission.role_id == role_id
    ).all()
    
    role_dict = role.__dict__.copy()
    role_dict['permissions'] = [perm.__dict__ for perm in permissions]
    
    return role_dict

@router.post("/", response_model=RoleInDB)
async def create_role(
    role_data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建角色"""
    # 检查角色名是否已存在
    existing_role = db.query(Role).filter(Role.name == role_data.name).first()
    if existing_role:
        raise HTTPException(status_code=400, detail="角色名已存在")
    
    # 创建新角色
    new_role = Role(
        name=role_data.name,
        description=role_data.description,
        is_active=role_data.is_active
    )
    
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    
    return new_role

@router.put("/{role_id}", response_model=RoleInDB)
async def update_role(
    role_id: int,
    role_data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新角色信息"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 如果更新角色名，检查是否已存在
    if role_data.name and role_data.name != role.name:
        existing_role = db.query(Role).filter(Role.name == role_data.name).first()
        if existing_role:
            raise HTTPException(status_code=400, detail="角色名已存在")
    
    # 更新角色数据
    for field, value in role_data.dict(exclude_unset=True).items():
        setattr(role, field, value)
    
    db.commit()
    db.refresh(role)
    
    return role

@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除角色"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 检查是否有用户在使用这个角色
    user_count = db.query(User).filter(User.role_id == role_id).count()
    if user_count > 0:
        raise HTTPException(status_code=400, detail="该角色正在被用户使用，无法删除")
    
    db.delete(role)
    db.commit()
    
    return {"message": "角色删除成功"}

@router.put("/{role_id}/status")
async def update_role_status(
    role_id: int,
    is_active: bool,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新角色状态"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    role.is_active = is_active
    db.commit()
    db.refresh(role)
    
    return {"message": "角色状态更新成功"}

@router.post("/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    menu_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """设置角色权限"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 删除现有权限
    db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
    
    # 添加新权限
    for menu_id in menu_ids:
        # 检查菜单是否存在
        menu = db.query(Menu).filter(Menu.id == menu_id).first()
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
    
    db.commit()
    
    return {"message": "权限设置成功"}