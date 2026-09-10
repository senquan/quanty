"""数据覆盖度代理：backend → dc（经 WS 命令通道）

设计依据（docs/plans/2026-09-10.ws-coverage-repair.md §4 / §5）：

- backend **不直连** dc 的 factor 库（本地共库能跑、docker 分库必挂），
  一律经 WS 命令问 dc（见 `app/ws/commands.py`）。
- `check` / `repair.status` 为秒级同步命令；`repair` 为长任务：
  秒回 `accepted`，进度经 `event.pipeline.progress` 异步回推，终态以同一
  corr_id 再回一条 `done`（落到 `Connection.last_repair` 快照）。
"""
from __future__ import annotations

from typing import Any

from app.ws import commands
from app.ws.registry import registry


class CoverageUnavailableError(RuntimeError):
    """dc 未建立 WS 连接 / 命令被拒，无法查询或指令（前端应提示"服务离线/不可用"）。"""


def is_online(service_code: str) -> bool:
    """该 service_code 当前是否有已建立的 WS 连接。"""
    return registry.is_service_connected(service_code)


def _first_error(resp: dict[str, Any], fallback: str) -> str:
    return (resp.get("error") or {}).get("message") or fallback


async def check(
    service_code: str, *, min_ratio: float = 0.95, timeout: float = 20.0
) -> dict[str, Any]:
    """查询 dc 最新交易日覆盖度快照（`check_coverage` 的返回）。"""
    try:
        resp = await commands.send_command(
            service_code, "coverage.check", {"min_ratio": min_ratio}, timeout=timeout
        )
    except commands.DcOfflineError as e:
        raise CoverageUnavailableError(str(e)) from e
    if not resp.get("ok"):
        raise CoverageUnavailableError(_first_error(resp, "覆盖度查询失败"))
    return resp.get("data") or {}


async def repair(
    service_code: str,
    *,
    source: str | None = None,
    min_ratio: float = 0.95,
    force: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """指令 dc 触发一轮覆盖度修复（异步）。

    Returns:
        `{"accepted": bool, "task_id": str | None, "running": bool}`
        其中 `running` 表示"已受理、结果稍后回推"。
    """
    params: dict[str, Any] = {"min_ratio": min_ratio, "force": force}
    if source:
        params["source"] = source
    try:
        resp = await commands.send_command(
            service_code, "coverage.repair", params, timeout=timeout
        )
    except commands.DcOfflineError as e:
        raise CoverageUnavailableError(str(e)) from e
    if not resp.get("ok"):
        raise CoverageUnavailableError(_first_error(resp, "修复指令被拒绝"))
    return {
        "accepted": bool(resp.get("accepted")),
        "task_id": resp.get("task_id"),
        "running": resp.get("done") is False,
    }


async def repair_status(service_code: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """查询 dc 侧修复状态（运行中 / 进度 / 最近结果）。"""
    try:
        resp = await commands.send_command(
            service_code, "coverage.repair.status", None, timeout=timeout
        )
    except commands.DcOfflineError as e:
        raise CoverageUnavailableError(str(e)) from e
    if not resp.get("ok"):
        raise CoverageUnavailableError(_first_error(resp, "修复状态查询失败"))
    return resp.get("data") or {}
