"""dc 侧命令分发（backend → dc 的 `command.request`）

设计依据（docs/plans/2026-09-10.ws-coverage-repair.md §4）：

- 命令在 WS 接收循环中处理，**绝不能阻塞**：慢操作一律 `run_in_executor`。
- 长任务（`coverage.repair`）**先回 accepted**，后台跑完再以**同一 corr_id**
  回 `done`；期间经 `emit_pipeline` 回推进度。
- 单飞：修复与 8:30 定时任务共用 `backfill` 的单飞锁，重复指令回 `busy`。
- 命令响应经 `WSClient.send_now` 直发（携带 corr_id）；进度事件仍走 outbox。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.tasks import backfill as backfill_task
from app.ws import protocol

logger = get_logger(__name__)

#: 进行中的修复状态（供 `coverage.repair.status`）
_repair_state: dict[str, Any] = {}


def _now_iso() -> str:
    return datetime.now().isoformat()


async def handle(client: Any, env: dict[str, Any]) -> None:
    """处理一条 `command.request`；任何异常都转成 `command.response` 的 error。"""
    corr_id = env.get("corr_id")
    payload = env.get("payload") or {}
    cmd = payload.get("cmd") or ""
    params = payload.get("params") or {}
    logger.info(f"收到命令 cmd={cmd} corr_id={corr_id}")

    try:
        if cmd == "coverage.check":
            await _coverage_check(client, corr_id, params)
        elif cmd == "coverage.repair":
            await _coverage_repair(client, corr_id, params)
        elif cmd == "coverage.repair.status":
            _reply(client, corr_id, cmd, ok=True, data=_repair_status())
        else:
            _reply(
                client,
                corr_id,
                cmd,
                ok=False,
                error={"code": "unknown_cmd", "message": f"未知命令: {cmd}"},
            )
    except Exception as e:  # noqa: BLE001 - 单条命令失败不得断开连接
        logger.error(f"命令处理失败 cmd={cmd}: {e}")
        _reply(
            client,
            corr_id,
            cmd,
            ok=False,
            error={"code": "internal_error", "message": str(e)[:200]},
        )


def _reply(
    client: Any,
    corr_id: str | None,
    cmd: str,
    *,
    ok: bool,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    accepted: bool = False,
    done: bool = True,
    task_id: str | None = None,
    progress: float | None = None,
) -> None:
    """回一条 `command.response`（直发，携带 corr_id）。"""
    client.send_now(
        protocol.MsgType.COMMAND_RESPONSE,
        protocol.command_result_payload(
            cmd=cmd,
            ok=ok,
            data=data,
            error=error,
            accepted=accepted,
            done=done,
            task_id=task_id,
            progress=progress,
        ),
        corr_id=corr_id,
    )


async def _coverage_check(client: Any, corr_id: str | None, params: dict[str, Any]) -> None:
    """覆盖度快照：一次 DB 查询，可在接收循环内直接等。"""
    min_ratio = float(params.get("min_ratio", 0.95))
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: backfill_task.check_coverage(min_ratio)
    )
    _reply(client, corr_id, "coverage.check", ok=True, data=result)


async def _coverage_repair(
    client: Any, corr_id: str | None, params: dict[str, Any]
) -> None:
    """指令修复：占用单飞名额 → 秒回 accepted → 后台跑。"""
    source = params.get("source") or "pandadata"
    min_ratio = float(params.get("min_ratio", 0.95))
    # backend 主动指令默认强制回填（即使覆盖度校验通过也执行一轮补齐）
    force = bool(params.get("force", True))

    if not backfill_task.try_begin_repair():
        _reply(
            client,
            corr_id,
            "coverage.repair",
            ok=False,
            error={"code": "busy", "message": "已有修复在进行中"},
        )
        return

    task_id = protocol.new_id()
    _repair_state.clear()
    _repair_state.update(
        {
            "task_id": task_id,
            "source": source,
            "status": "running",
            "started_at": _now_iso(),
            "progress": None,
        }
    )
    _reply(
        client,
        corr_id,
        "coverage.repair",
        ok=True,
        accepted=True,
        done=False,
        task_id=task_id,
    )
    # 后台执行，绝不阻塞接收循环；持引用防被 GC
    task = asyncio.create_task(
        _run_repair(client, corr_id, task_id, source, min_ratio, force),
        name=f"coverage-repair-{task_id[:8]}",
    )
    _repair_state["_task"] = task


async def _run_repair(
    client: Any,
    corr_id: str | None,
    task_id: str,
    source: str,
    min_ratio: float,
    force: bool,
) -> None:
    """后台跑 `verify_and_repair`；进度回推，结束/失败以同一 corr_id 回终态。"""
    from app.ws import events as ws_events

    def _on_progress(p: dict) -> None:
        done = p.get("done", 0)
        total = p.get("total", 0) or 1
        ratio = round(done / total, 4)
        _repair_state.update(
            {"progress": ratio, "done": done, "total": total}
        )
        # 线程安全：_emit → client.send 走 outbox（见 client 线程模型说明）
        ws_events.emit_pipeline(
            "progress",
            task="coverage_repair",
            progress=ratio,
            detail={"task_id": task_id, **p},
        )

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: backfill_task.verify_and_repair(
                source=source,
                min_ratio=min_ratio,
                force=force,
                on_progress=_on_progress,
            ),
        )
        _repair_state.update({"status": "done", "finished_at": _now_iso(), "progress": 1.0})
        _reply(
            client,
            corr_id,
            "coverage.repair",
            ok=True,
            done=True,
            task_id=task_id,
            data=result,
            progress=1.0,
        )
        ws_events.emit_pipeline(
            "finished", task="coverage_repair", progress=1.0, detail={"task_id": task_id}
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"覆盖度修复失败 task_id={task_id}: {e}")
        _repair_state.update(
            {"status": "error", "finished_at": _now_iso(), "error": str(e)[:200]}
        )
        _reply(
            client,
            corr_id,
            "coverage.repair",
            ok=False,
            done=True,
            task_id=task_id,
            error={"code": "repair_failed", "message": str(e)[:200]},
        )
        ws_events.emit_pipeline(
            "failed",
            task="coverage_repair",
            detail={"task_id": task_id, "error": str(e)[:200]},
        )
    finally:
        backfill_task.end_repair()


def _repair_status() -> dict[str, Any]:
    """当前修复状态（含进程内单飞锁的真实运行态）。"""
    state = {k: v for k, v in _repair_state.items() if not k.startswith("_")}
    return {"running": backfill_task.is_repair_running(), **state}
