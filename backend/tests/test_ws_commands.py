"""WS 命令通道（backend → dc）单测：离线 / 超时 / 响应投递 / 协议载荷。

对应 docs/plans/2026-09-10.ws-coverage-repair.md §6 步骤 2 的 verify。
不依赖网络与 DB：用假的 Connection 与手工注入的 Future 覆盖关键分支。
"""
import asyncio

import pytest

from app.ws import commands, protocol


def test_offline_raises_dc_offline():
    """无任何 dc 连接时，send_command 应快速失败并抛 DcOfflineError。"""

    async def run() -> None:
        with pytest.raises(commands.DcOfflineError):
            await commands.send_command("no-such-service", "coverage.check", timeout=1.0)

    asyncio.run(run())


def test_timeout_when_no_response(monkeypatch):
    """有连接但 dc 不回响应时应抛 CommandTimeoutError，且 _pending 不残留。"""

    class _FakeConn:
        instance_id = "fake"
        service_code = "fake-svc"

        async def send(self, env: dict) -> None:  # noqa: ANN001
            return None

    monkeypatch.setattr(commands, "_find_connection", lambda sc: _FakeConn())
    before = commands.pending_count()

    async def run() -> None:
        with pytest.raises(commands.CommandTimeoutError):
            await commands.send_command("fake-svc", "coverage.check", timeout=0.05)

    asyncio.run(run())
    assert commands.pending_count() == before  # finally 清理，无泄漏


def test_resolve_fulfills_pending():
    """resolve 命中等待中的 corr_id 时投递结果；未知 corr_id 返回 False。"""

    async def run() -> None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        commands._pending["cid-1"] = fut
        try:
            assert commands.resolve("cid-1", {"ok": True, "data": {"x": 1}}) is True
            assert await asyncio.wait_for(fut, 1.0) == {"ok": True, "data": {"x": 1}}
            assert commands.resolve("unknown", {}) is False
        finally:
            commands._pending.pop("cid-1", None)

    asyncio.run(run())


def test_command_payload_shape():
    """命令载荷字段口径（两侧 protocol.py 逐字一致，故只测一侧即可）。"""
    req = protocol.command_payload(cmd="coverage.check", params={"min_ratio": 0.9})
    assert req == {"cmd": "coverage.check", "params": {"min_ratio": 0.9}}

    accepted = protocol.command_result_payload(
        cmd="coverage.repair", ok=True, accepted=True, done=False, task_id="t1"
    )
    assert accepted["accepted"] is True
    assert accepted["done"] is False
    assert accepted["task_id"] == "t1"

    done = protocol.command_result_payload(cmd="coverage.check", ok=True, data={"ratio": 1.0})
    assert done["done"] is True
    assert done["data"] == {"ratio": 1.0}
    assert done["error"] == {}
