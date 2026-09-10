"""backend → dc 命令通道（请求-响应）

设计依据（docs/plans/2026-09-10.ws-coverage-repair.md §4）：

- 复用 dc 主动拨入的长连接（**不新建通道**）：按 `service_code` 定位连接，
  经 `Connection.send` 下发 `command.request`。
- 用信封 `corr_id` 关联请求与响应；响应由 `handlers._on_command_response`
  经 `resolve(corr_id, payload)` 投递回此处的 Future。
- **长任务**（如 `coverage.repair`，实测数十分钟）：首个响应仅回
  `accepted=True, done=False`；终态以**同一 corr_id** 再回一条 `done=True`，
  此时 Future 多已结束，由 `Connection.last_repair` 快照兜底（见 handlers）。
- 命令响应**不走 outbox**（outbox 构信封时不带 corr_id，见 dc `client._flush`），
  故丢失即超时；正确性由调用方重试 / `repair.status` 轮询兜底。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.ws import protocol
from app.ws.registry import Connection, registry

logger = logging.getLogger(__name__)


class DcOfflineError(RuntimeError):
    """目标 service_code 没有已建立的 WS 连接。"""


class CommandTimeoutError(RuntimeError):
    """命令在超时时间内未收到响应。"""


#: 等待中的命令：corr_id → Future（进程内；backend 单实例，见 registry 设计依据）
_pending: dict[str, asyncio.Future] = {}


def _find_connection(service_code: str) -> Connection | None:
    """按 service_code 定位 dc 连接（多副本时取第一个；当前 dc 只 1 实例）。"""
    for conn in registry.all():
        if conn.service_code == service_code:
            return conn
    return None


async def send_command(
    service_code: str,
    cmd: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """下发命令并等待**首个**响应。

    Returns:
        `command.response` 的 payload（含 `ok` / `data` / `error` /
        `accepted` / `done` / `task_id`）。

    Raises:
        DcOfflineError: 该 service_code 无 WS 连接。
        CommandTimeoutError: 超时未收到响应。
    """
    conn = _find_connection(service_code)
    if conn is None:
        raise DcOfflineError(
            f"dc 未建立 WS 连接：service_code={service_code}"
            "（请确认 dc 已启动且两侧 WS_ENABLED=true）"
        )

    corr_id = protocol.new_id()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _pending[corr_id] = fut
    try:
        await conn.send(
            protocol.envelope(
                protocol.MsgType.COMMAND_REQUEST,
                protocol.command_payload(cmd=cmd, params=params),
                corr_id=corr_id,
            )
        )
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError as e:
        raise CommandTimeoutError(f"命令 {cmd} 超时（{timeout}s）") from e
    finally:
        _pending.pop(corr_id, None)


def resolve(corr_id: str, payload: dict[str, Any]) -> bool:
    """把命令响应投递给等待中的 Future；返回是否命中（无等待者时 False）。"""
    fut = _pending.get(corr_id or "")
    if fut is not None and not fut.done():
        fut.set_result(payload)
        return True
    return False


def pending_count() -> int:
    """当前等待中的命令数（供运维/排查）。"""
    return len(_pending)
