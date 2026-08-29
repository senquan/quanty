"""API Key 认证（阶段 A）

主后端（registry）为每个接入的清洗服务分配一个 key，在请求
受保护接口（factors / pipeline）时通过 `X-API-Key` 头携带。

设计要点：
- 若 `API_KEYS` 未配置（开发期），则认证自动放行，便于本地联调。
- 配置后，缺失或错误的 key 一律返回 401。
- 公开接口（health / metrics / qos）不挂此依赖。
"""
from fastapi import Header, HTTPException, status

from app.core.config import settings


def verify_api_key(x_api_key: str | None = Header(default=None)) -> str | None:
    """依赖项：校验 X-API-Key。

    返回被接受的服务名（用于审计），无认证配置时返回 None。
    """
    configured = settings.api_keys
    if not configured:
        # 开发期：未配置 key 则放行
        return None

    if not x_api_key or x_api_key not in configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或非法的 X-API-Key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    return settings.SERVICE_NAME
