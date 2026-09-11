"""进程内连接注册表（backend 单实例适用）

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §3 / §9.6 / §1-10）：

- backend **单实例** → 连接注册表退化为**进程内 dict**，
  v1 设想的 Redis 选主 / fan-out 全部消失，Redis 降为可选。
- 按 `instance_id` 区分连接。dc **当前 1 个实例、未来会多**，故首版即按
  1 对多设计（注册、去重、错峰均按 instance_id 维度）。
- **backend 不持久化 last_seq**：断线补发由 dc 侧 outbox 驱动
  （dc 自持 `last_acked_seq`，重连后自 `last_acked_seq + 1` 补发）。
  因此 Redis 缺失时功能完整，仅去重窗口变短，由业务幂等兜底。

事件去重：无 Redis 时使用**内存有界 LRU**（OrderedDict），Redis 可用时
由 `dedup` 层额外做跨重启去重（见 app/services/factor_replica_sync 的幂等设计）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from fastapi import WebSocket

from app.ws import protocol

logger = logging.getLogger(__name__)


@dataclass
class Connection:
    """一条 dc 长连接。"""

    instance_id: str
    service_code: str
    ws: WebSocket
    protocol_version: int
    connected_at: datetime = field(default_factory=datetime.now)
    last_seq: int = 0
    last_seen: float = field(default_factory=time.time)
    remote: str | None = None
    # 最近一次流水线状态（Phase 4 进度推送的落地处）
    last_pipeline: dict[str, Any] | None = None
    # 最近处理的消息 id（供落库时追溯来源，避免改动 handler 签名）
    last_message_id: str | None = None
    # 最近一条消息的 corr_id（命令响应经此与 send_command 的 Future 关联）
    last_corr_id: str | None = None
    # 最近一次覆盖度快照 / 修复状态（coverage.* 命令结果落地处）
    last_coverage: dict[str, Any] | None = None
    last_repair: dict[str, Any] | None = None
    # 简易限流：当前窗口内的消息计数
    window_start: float = field(default_factory=time.time)
    window_msgs: int = 0

    def touch(self) -> None:
        self.last_seen = time.time()

    def is_stale(self, stale_seconds: float) -> bool:
        return (time.time() - self.last_seen) > stale_seconds

    def rate_limit_hit(self, limit_per_min: int) -> bool:
        """简易滑动窗口限流：每分钟超过上限即命中。"""
        now = time.time()
        if now - self.window_start >= 60.0:
            self.window_start = now
            self.window_msgs = 0
        self.window_msgs += 1
        return self.window_msgs > limit_per_min

    async def send(self, env: dict[str, Any]) -> None:
        """向本连接发送一条信封；失败仅记日志（不断开连接）。"""
        try:
            await self.ws.send_text(protocol.encode(env))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"WS 发送失败 instance_id={self.instance_id}: {e}")


class ConnectionRegistry:
    """进程内连接注册表 + 事件去重。"""

    def __init__(
        self,
        *,
        max_connections: int = 50,
        dedup_size: int = 10_000,
        stale_seconds: float = 120.0,
        rate_limit_per_min: int = 600,
    ) -> None:
        self._conns: dict[str, Connection] = {}
        self._lock = asyncio.Lock()
        self._max_connections = max_connections
        self._dedup_size = max(1, dedup_size)
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._stale_seconds = stale_seconds
        self._rate_limit = rate_limit_per_min
        # service_code → 最近一次连上的时间戳（断连告警用；unregister 不清，
        # 以便区分"曾连过又断开"与"本进程内从未连上"）
        self._last_connected: dict[str, float] = {}
        self._started_at = time.time()
        self._counters = {
            "registered_total": 0,
            "unregistered_total": 0,
            "rejected_total": 0,
            "duplicates_total": 0,
            "messages_total": 0,
        }

    # ---------------- 连接管理 ----------------

    async def register(self, conn: Connection) -> bool:
        """登记连接；超过上限或 instance_id 冲突时返回 False。"""
        async with self._lock:
            if len(self._conns) >= self._max_connections:
                self._counters["rejected_total"] += 1
                logger.warning(
                    f"WS 连接数达上限 {self._max_connections}，拒绝 instance_id={conn.instance_id}"
                )
                return False
            self._conns[conn.instance_id] = conn
            if conn.service_code:
                self._last_connected[conn.service_code] = time.time()
            self._counters["registered_total"] += 1
        logger.info(
            "dc 已连接",
            extra={
                "instance_id": conn.instance_id,
                "service_code": conn.service_code,
                "remote": conn.remote,
                "total": len(self._conns),
            },
        )
        return True

    async def unregister(self, instance_id: str) -> None:
        async with self._lock:
            if self._conns.pop(instance_id, None) is not None:
                self._counters["unregistered_total"] += 1
        logger.info(f"dc 已断开 instance_id={instance_id}")

    def get(self, instance_id: str) -> Connection | None:
        return self._conns.get(instance_id)

    def all(self) -> list[Connection]:
        return list(self._conns.values())

    def count(self) -> int:
        return len(self._conns)

    def instance_ids(self) -> list[str]:
        return list(self._conns.keys())

    def connected_service_codes(self) -> set[str]:
        """当前已建立长连接的 service_code 集合（用于跳过 HTTP 轮询）。"""
        return {c.service_code for c in self._conns.values() if c.service_code}

    def is_service_connected(self, service_code: str) -> bool:
        return service_code in self.connected_service_codes()

    def known_service_codes(self) -> set[str]:
        """本进程内出现过的 service_code（当前 + 历史），供 `/ws/status` 告警用。"""
        codes = {c.service_code for c in self._conns.values() if c.service_code}
        codes |= set(self._last_connected.keys())
        return codes

    def last_connected_at(self, service_code: str) -> float | None:
        """该 service_code 最近一次连上的时间戳；从未连过返回 None。"""
        return self._last_connected.get(service_code)

    def mark_seen(self, service_code: str) -> None:
        """标记该 service_code 此刻仍在线（**每收到一条消息都要调用**）。

        只在 `register()` 时记录是不够的：长连接维持数小时后断开，算出的失联时长
        会把**连接持续时间**也算进去。2026-09-11 演练实测：实际仅断连 163s 却报
        1326s，导致一断线就立刻误告警（阈值形同虚设）。故收到消息即刷新。
        """
        if service_code:
            self._last_connected[service_code] = time.time()

    def disconnect_report(
        self, service_codes: Iterable[str], threshold_sec: float
    ) -> list[dict[str, Any]]:
        """返回「当前未连接且失联已超过阈值」的告警清单。

        基准取"上次连上时刻"；若本进程内从未连上，则以进程启动时刻为基准——
        否则 backend 重启后 dc 再也没连上的情况会永远不告警。
        """
        now = time.time()
        out: list[dict[str, Any]] = []
        for code in service_codes:
            if not code or self.is_service_connected(code):
                continue
            base = self._last_connected.get(code)
            since = base if base is not None else self._started_at
            down = now - since
            if down >= threshold_sec:
                out.append(
                    {
                        "service_code": code,
                        "down_seconds": round(down, 1),
                        "ever_connected": base is not None,
                    }
                )
        return out

    # ---------------- 水位 / 幂等 ----------------

    def mark_seq(self, instance_id: str, seq: int) -> None:
        conn = self._conns.get(instance_id)
        if conn is not None and isinstance(seq, int) and seq > conn.last_seq:
            conn.last_seq = seq

    def last_seq(self, instance_id: str) -> int:
        conn = self._conns.get(instance_id)
        return conn.last_seq if conn else 0

    def seen_before(self, msg_id: str) -> bool:
        """幂等去重：返回 True 表示该消息**已处理过**（调用方应跳过）。

        无 Redis 时使用内存有界 LRU；这是"至少一次投递"下的第一道防线，
        最终正确性由业务幂等兜底（如 signal_id / report_id 唯一键）。
        """
        if not msg_id:
            return False
        now = time.time()
        if msg_id in self._seen:
            self._seen.move_to_end(msg_id)
            self._counters["duplicates_total"] += 1
            return True
        self._seen[msg_id] = now
        if len(self._seen) > self._dedup_size:
            self._seen.popitem(last=False)
        return False

    def count_message(self) -> None:
        self._counters["messages_total"] += 1

    # ---------------- 运维 ----------------

    async def purge_stale(self) -> list[str]:
        """清理长时间无消息的连接（心跳失效时的兜底），返回被清理的 instance_id。"""
        stale = [cid for cid, c in self._conns.items() if c.is_stale(self._stale_seconds)]
        for cid in stale:
            conn = self._conns.get(cid)
            if conn is not None:
                try:
                    await conn.ws.close(code=1001, reason="stale connection")
                except Exception:  # noqa: BLE001
                    pass
            await self.unregister(cid)
        if stale:
            logger.warning(f"清理失联 WS 连接: {stale}")
        return stale

    @property
    def rate_limit(self) -> int:
        return self._rate_limit

    def stats(self) -> dict[str, Any]:
        return {
            "connections": len(self._conns),
            "max_connections": self._max_connections,
            "instances": [
                {
                    "instance_id": c.instance_id,
                    "service_code": c.service_code,
                    "protocol_version": c.protocol_version,
                    "last_seq": c.last_seq,
                    "idle_seconds": round(time.time() - c.last_seen, 1),
                }
                for c in self._conns.values()
            ],
            "dedup_size": len(self._seen),
            **self._counters,
        }


#: 模块级单例（backend 单实例，进程内注册表即可）
registry = ConnectionRegistry()
