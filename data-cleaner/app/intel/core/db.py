"""intel 数据库接入：复用 dc 的异步引擎与 session 工厂

intel 数据落在独立的 ``intel`` schema（迁移见 migrations/011_intel_schema.sql），
与 ``factor`` schema 物理隔离，但共用同一连接池与 run_async 线程循环隔离机制
（见 app/storage/db.py 顶部说明：后台任务走 engine_bg，避免 asyncpg 绑错 loop）。
"""
from app.storage.db import (  # noqa: F401
    async_session,
    async_session_bg,
    current_session,
    engine,
    engine_bg,
    run_async,
)

__all__ = [
    "engine",
    "engine_bg",
    "async_session",
    "async_session_bg",
    "current_session",
    "run_async",
]
