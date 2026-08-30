"""自选股表幂等建表（仅首次调用时执行一次）。"""
import asyncio

from app.core.database import Base, engine
from app.models.quant import Watchlist

_INIT_LOCK = None


async def ensure_watchlist_tables() -> None:
    """幂等建表：只在首次调用时执行一次（基于 async engine）"""
    global _INIT_LOCK
    if _INIT_LOCK is None:
        _INIT_LOCK = asyncio.Lock()
    async with _INIT_LOCK:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Watchlist.__table__],
            )


__all__ = ["ensure_watchlist_tables", "Watchlist"]
