"""自选股管理（按用户隔离的 CRUD）。"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.database import get_db
from app.models.quant import Watchlist
from app.models.user import User
from app.schemas.quant import (
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistItemUpdate,
    WatchlistBulkCreate,
)
from app.schemas.response import Response
from app.api.api_v1.endpoints.auth import get_current_user
from app.services.watchlist_service import ensure_watchlist_tables

router = APIRouter()


def _normalize_code(code: str) -> str:
    """规整股票代码：去空格、转大写。"""
    return (code or "").strip().upper()


@router.get("", response_model=Response[List[WatchlistItemResponse]])
async def list_watchlist(
    search: Optional[str] = Query(None, description="按代码或名称模糊搜索"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的自选股列表"""
    await ensure_watchlist_tables()
    query = select(Watchlist).filter(Watchlist.user_id == current_user.id)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(Watchlist.code.ilike(like), Watchlist.name.ilike(like))
        )
    query = query.order_by(Watchlist.created_at.desc())
    result = await db.execute(query)
    return Response.success(data=result.scalars().all())


@router.post("", response_model=Response[WatchlistItemResponse])
async def create_watchlist(
    payload: WatchlistItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增一只自选股"""
    await ensure_watchlist_tables()
    code = _normalize_code(payload.code)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码不能为空")

    existing = await db.execute(
        select(Watchlist).filter(
            Watchlist.user_id == current_user.id, Watchlist.code == code
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail=f"自选股 {code} 已存在")

    item = Watchlist(
        user_id=current_user.id,
        code=code,
        name=(payload.name or "").strip() or None,
        note=payload.note,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return Response.success(data=item, msg="已添加")


@router.post("/bulk", response_model=Response[List[WatchlistItemResponse]])
async def bulk_create_watchlist(
    payload: WatchlistBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量导入自选股（重复代码自动跳过）"""
    await ensure_watchlist_tables()
    created: list[Watchlist] = []
    seen = set()
    for raw in payload.items:
        code = _normalize_code(raw.code)
        if not code or code in seen:
            continue
        seen.add(code)
        existing = await db.execute(
            select(Watchlist).filter(
                Watchlist.user_id == current_user.id, Watchlist.code == code
            )
        )
        if existing.scalars().first():
            continue
        created.append(
            Watchlist(
                user_id=current_user.id,
                code=code,
                name=(raw.name or "").strip() or None,
                note=raw.note,
            )
        )
    if created:
        db.add_all(created)
        await db.commit()
        for item in created:
            await db.refresh(item)
    return Response.success(data=created, msg=f"成功导入 {len(created)} 只")


@router.put("/{item_id}", response_model=Response[WatchlistItemResponse])
async def update_watchlist(
    item_id: int,
    payload: WatchlistItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改自选股名称/备注"""
    result = await db.execute(
        select(Watchlist).filter(
            Watchlist.id == item_id, Watchlist.user_id == current_user.id
        )
    )
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="自选股不存在")

    if payload.name is not None:
        item.name = payload.name.strip() or None
    if payload.note is not None:
        item.note = payload.note
    item.updated_at = func.now()
    await db.commit()
    await db.refresh(item)
    return Response.success(data=item)


@router.delete("/{item_id}", response_model=Response)
async def delete_watchlist(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除一只自选股"""
    result = await db.execute(
        select(Watchlist).filter(
            Watchlist.id == item_id, Watchlist.user_id == current_user.id
        )
    )
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="自选股不存在")

    await db.delete(item)
    await db.commit()
    return Response.success(msg="已删除")
