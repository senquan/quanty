"""脚本策略回测代理 —— backend **不持有计算**，只做网关转发。

按 ``docs/memo/2026-09-04.architecture.md`` §0 的归口决策：A 股行情在 dc（``factor.raw_bars``），
backend 拿不到（两库隔离），所以脚本策略回测一律转发到 dc::

    POST {dc}/api/v1/backtest/script   跑回测
    GET  {dc}/api/v1/backtest/styles   口径表（前端下拉）

转发层只多做一个判断：**422 必须原样透传**。
dc 的 422 是「这个回测不成立」（闸口拦下 / 没数据），detail 是
``{"reason": ..., "remedy": ...}`` —— 前端要靠它展示「为什么跑不了 + 怎么改」。
如果在这里被统一包成「转发失败」，用户只会看到一句没用的 500，
然后以为是服务挂了，去翻日志 —— 而日志里什么都没有。

超时给 60s：dc 侧要读库（万行级）+ 逐 bar 撮合，比普通 CRUD 慢一档。
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import factor_strategy_proxy as fs

DEFAULT_TIMEOUT = 60.0

SCRIPT_PATH = "/api/v1/backtest/script"
STYLES_PATH = "/api/v1/backtest/styles"


class ScriptBacktestProxyError(Exception):
    """转发失败（连不上 / 超时 / dc 5xx）—— 服务端问题。"""


class ScriptBacktestRefused(ScriptBacktestProxyError):
    """dc 闸口拒绝了这个回测（422）—— 这是「请求不成立」，不是故障。"""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "remedy": self.remedy}


async def run_script_backtest(
    db: AsyncSession,
    payload: dict,
    service_code: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """转发一次脚本策略回测，返回 dc 的完整结果。"""
    svc = await fs.pick_service(db, service_code)
    try:
        result = await fs._request(
            svc, "POST", SCRIPT_PATH, payload=payload, timeout=timeout
        )
    except fs.FactorStrategyProxyError as e:
        if e.status_code == 422:
            # 只要 dc 说是 422，就是「请求不成立」—— 不管 detail 长什么样。
            # dc 的实现固定给 {"reason", "remedy"}；拿不到结构化内容时退回文本，
            # 也好过把它说成「服务不可用」（用户会去翻根本没有错误的日志）。
            d = e.detail if isinstance(e.detail, dict) else {}
            raise ScriptBacktestRefused(
                str(d.get("reason") or e.detail or "回测被 dc 闸口拒绝"),
                str(d.get("remedy") or ""),
            ) from e
        raise ScriptBacktestProxyError(str(e)) from e
    return result if isinstance(result, dict) else {}


async def backtest_styles(
    db: AsyncSession,
    service_code: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """取 dc 的口径表（style / price_field），供前端下拉。"""
    svc = await fs.pick_service(db, service_code)
    try:
        result = await fs._request(svc, "GET", STYLES_PATH, timeout=timeout)
    except fs.FactorStrategyProxyError as e:
        raise ScriptBacktestProxyError(str(e)) from e
    return result if isinstance(result, dict) else {}
