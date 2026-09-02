"""行情代理：向 data-cleaner（行情中继）取最新价

backend 与 data-cleaner 分属独立 Postgres 实例，backend 无法直读
`factor.raw_bars`，故调仓算股数所需的取价经 data-cleaner 的
`POST /api/v1/raw/latest-prices` 完成（一次请求覆盖全部标的，避免 N 次往返）。
"""
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.factor_strategy_proxy import pick_service

DEFAULT_TIMEOUT = 10.0


class MarketProxyError(Exception):
    """向行情中继取价失败"""


async def latest_prices(
    db: AsyncSession, symbols: list[str], service_code: str | None = None
) -> dict[str, float]:
    """批量取最新前复权收盘价。

    只返回取到价格的标的；调用方需自行处理缺失标的。
    取价失败抛 MarketProxyError，由调用方决定降级策略（通常记为调仓失败，不盲目下单）。
    """
    if not symbols:
        return {}
    svc = await pick_service(db, service_code)
    headers = {"X-API-Key": svc.api_key} if svc.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{svc.base_url}/api/v1/raw/latest-prices",
                headers=headers,
                json={"symbols": list(dict.fromkeys(symbols))},
            )
    except Exception as e:  # noqa: BLE001
        raise MarketProxyError(f"无法连接行情中继：{e}") from e

    if resp.status_code >= 400:
        raise MarketProxyError(f"行情中继返回 {resp.status_code}：{resp.text[:200]}")

    data: Any = resp.json()
    prices = (data or {}).get("prices") or {}
    return {str(k): float(v) for k, v in prices.items() if v is not None}
