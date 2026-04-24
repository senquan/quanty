"\"\"\"
Menu management endpoints.

This module provides CRUD operations for menu items, including listing,
creating, updating, deleting, and managing menu hierarchies.
\"\"\"
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.menu import Menu
from app.schemas.menu import MenuCreate, MenuUpdate, MenuInDB
from app.schemas.response import Response

router = APIRouter()

@router.get(\"/\", response_model=Response[List[MenuInDB]])
async def get_menus(
    search: Optional[str] = Query(None),
    parent_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"获取菜单列表\"\"\"
    query = select(Menu)
    if parent_id is not None:
        query = query.where(Menu.parent_id == parent_id)
    if search:
        query = query.where(Menu.name.ilike(f\"%{search}%\"))
    query = query.order_by(Menu.id.desc())
    result = await db.execute(query)
    menus = result.scalars().all()

    return Response.success(data=menus)

@router.get(\"/{menu_id}\", response_model=MenuInDB)
async def get_menu(
    menu_id: int,
    db: AsyncSession = Depends(get_db)
):
    \"\"\"获取单个菜单信息\"\"\"
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = result.scalars().first()
    if not menu:
        raise HTTPException(status_code=404, detail=\"菜单不存在\")

    return menu

@router.post(\"/\", response_model=MenuInDB)
async def create_menu(
    menu_data: MenuCreate,
    db: AsyncSession = Depends(get_db)
):
    \"\"\"创建菜单\"\"\"
    # 检查父菜单是否存在
    if menu_data.parent_id != 0:
        parent_result = await db.execute(select(Menu).where(Menu.id == menu_data.parent_id))
        parent_menu = parent_result.scalars().first()
        if not parent_menu:
            raise HTTPException(status_code=400, detail=\"父菜单不存在\")

    # 创建新菜单
    new_menu = Menu(**menu_data.model_dump())
    new_menu.created_at = datetime.now()

    db.add(new_menu)
    await db.commit()
    await db.refresh(new_menu)

    return Response.success(data=new_menu)

@router.put(\"/{menu_id}\", response_model=MenuInDB)
async def update_menu(
    menu_id: int,
    menu_data: MenuUpdate,
    db: AsyncSession = Depends(get_db)
):
    \"\"\"更新菜单信息\"\"\"
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = result.scalars().first()
    if not menu:
        raise HTTPException(status_code=404, detail=\"菜单不存在\")

    # 检查父菜单是否存在
    if menu_data.parent_id is not None and menu_data.parent_id != 0:
        parent_result = await db.execute(select(Menu).where(Menu.id == menu_data.parent_id))
        parent_menu = parent_result.scalars().first()
        if not parent_menu:
            raise HTTPException(status_code=400, detail=\"父菜单不存在\")

    # 更新菜单数据 - H1-5: dict() → model_dump()
    update_dict = menu_data.model_dump(exclude_unset=True)  # H1-5
    for field, value in update_dict.items():
        setattr(menu, field, value)

    await db.commit()
    await db.refresh(menu)

    return menu

@router.delete(\"/{menu_id}\")
async def delete_menu(
    menu_id: int,
    db: AsyncSession = Depends(get_db)
):
    \"\"\"删除菜单\"\"\"
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = result.scalars().first()
    if not menu:
        raise HTTPException(status_code=404, detail=\"菜单不存在\")

    # 检查是否有子菜单
    count_result = await db.execute(select(Menu.id).where(Menu.parent_id == menu_id))
    child_count = count_result.scalars().first()
    if child_count:
        raise HTTPException(status_code=400, detail=\"该菜单下存在子菜单，无法删除\")

    await db.delete(menu)
    await db.commit()

    return {\"message\": \"菜单删除成功\"}

@router.put(\"/{menu_id}/status\")
async def update_menu_status(
    menu_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db)
):
    \"\"\"更新菜单状态\"\"\"
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    menu = result.scalars().first()
    if not menu:
        raise HTTPException(status_code=404, detail=\"菜单不存在\")
  
    menu.is_enabled = is_active
    await db.commit()
    await db.refresh(menu)
    
    return {\"message\": \"菜单状态更新成功\"}

@router.get(\"/{menu_id}/children\", response_model=List[MenuInDB])
async def get_menu_children(
    menu_id: int,
    db: AsyncSession = Depends(get_db)
):
    \"\"\"获取菜单的子菜单\"\"\"
    result = await db.execute(
        select(Menu).where(Menu.parent_id == menu_id).order_by(Menu.id)
    )
    children = result.scalars().all()
    return children
