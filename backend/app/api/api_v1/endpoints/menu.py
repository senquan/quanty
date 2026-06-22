from typing import List
from fastapi import APIRouter
from app.schemas.menu import MenuInDB
from app.schemas.response import Response

router = APIRouter()


@router.get("/all", response_model=Response[List[MenuInDB]])
async def get_all_menus():
    """获取所有菜单（返回空数据集）"""
    return Response.success(data=[])
