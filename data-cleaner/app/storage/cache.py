"""Redis 缓存客户端（热因子数据加速）

Key 规范（见设计文档 §6.3）:
- factor:latest:{code} -> 最新一期因子值（JSON）
- factor:status        -> 流水线最近运行状态（**仅**由 publish_status 写）
- factor:heartbeat     -> 30s 存活心跳（**仅**由 publish_heartbeat 写）

为什么要拆两个 key：历史上心跳与流水线报告共用 `factor:status`，
导致 30s 一次的心跳把流水线运行结果冲掉（设计文档 §2.2 既有缺陷，Phase 4 修复）。

无 Redis 时优雅降级（所有操作为 no-op），不影响主流程。
"""
import json
from typing import Any

#: 流水线运行状态（由 publish_status 写）
STATUS_KEY = "factor:status"

#: 存活心跳（由 publish_heartbeat 写，与流水线状态分离）
HEARTBEAT_KEY = "factor:heartbeat"

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client = None  # lazy: redis.asyncio.Redis | None


def _redis_module():
    """懒加载 redis 模块；未安装时返回 None（降级为 no-op）"""
    try:
        import redis.asyncio as aioredis
        return aioredis
    except ImportError:
        logger.warning("未安装 redis，缓存降级为 no-op")
        return None


def get_redis():
    """懒加载 Redis 客户端；未安装/连接失败返回 None 并降级"""
    global _client
    if _client is not None:
        return _client
    mod = _redis_module()
    if mod is None:
        _client = None
        return None
    try:
        _client = mod.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.warning("Redis 不可用，缓存降级为 no-op", extra={"error": str(e)})
        _client = None
    return _client


async def set_json(key: str, value: Any, ttl: int = 86400) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as e:
        logger.warning("Redis 写入失败", extra={"key": key, "error": str(e)})


async def get_json(key: str) -> Any | None:
    r = get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def publish_status(status: dict) -> None:
    """刷新**流水线**运行状态（factor:status，TTL 24h）。

    注意：勿用于心跳。历史上 30s 心跳也写本 key，会把流水线运行结果冲掉
    （设计文档 §2.2）。心跳请改用 `publish_heartbeat()`。
    """
    await set_json(STATUS_KEY, status, ttl=86400)


async def publish_heartbeat(status: dict) -> None:
    """刷新存活心跳（factor:heartbeat，TTL 24h）—— 与流水线状态分离。"""
    await set_json(HEARTBEAT_KEY, status, ttl=86400)


async def get_status() -> Any | None:
    """读取流水线运行状态"""
    return await get_json(STATUS_KEY)


async def get_heartbeat() -> Any | None:
    """读取存活心跳"""
    return await get_json(HEARTBEAT_KEY)


async def cache_factor_latest(code: str, values: list) -> None:
    """缓存最新一期因子值（factor:latest:{code}，TTL 24h）"""
    await set_json(f"factor:latest:{code}", values, ttl=86400)


async def get_factor_latest(code: str) -> list | None:
    return await get_json(f"factor:latest:{code}")


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
