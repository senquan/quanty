"""dc → backend 的 WebSocket 长连接客户端

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §3 / §9）：

- dc 作为**客户端**主动连入 backend（backend 单实例作服务端，1 对多）。
- 自动重连由本类自行管理（`_run` 的 while 循环 + `_delays` 退避序列），
  退避为 **指数退避 + ±20% 抖动**，首次延迟按 `instance_id` 哈希**错峰**，防多实例惊群。
- 事件经 `Outbox` 缓冲：`ack(seq)` 驱动清理，重连后自 `last_acked_seq + 1` 补发，
  因此 **backend 无需持久化水位**，Redis 成为完全可选项（§9.6）。
- **硬约束（§9.4）**：`send()` 永不抛异常、永不阻塞主流程；
  失败仅入 outbox 并打点，绝不阻塞 EOD 流水线。

线程模型：EOD 流水线运行在 `run_in_executor` 的同步线程中，
故 `send()` 可被任意线程调用，唤醒通过 `loop.call_soon_threadsafe` 完成。
"""
from __future__ import annotations

import asyncio
import random
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

from app.core import metrics
from app.core.logging import get_logger
from app.ws import protocol
from app.ws.outbox import Outbox

logger = get_logger(__name__)


def _derive_ws_url(base: str) -> str:
    """由 HTTP 基址推导 WS 地址：http→ws、https→wss，并补上 WS_PATH。"""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + protocol.WS_PATH


def _default_instance_id() -> str:
    """默认实例身份：主机名（稳定，重启后不变）。

    若同一主机部署多个 dc 实例，必须通过 `WS_INSTANCE_ID` 显式区分。
    """
    return socket.gethostname() or "dc-unknown"


class WSClient:
    """dc → backend 的 WebSocket 客户端。"""

    def __init__(
        self,
        outbox: Outbox,
        *,
        uri: str,
        token: str,
        instance_id: str,
        service_code: str,
        ping_interval: float = 15.0,
        ping_timeout: float = 20.0,
        open_timeout: float = 10.0,
        max_frame_bytes: int = 256 * 1024,
        reconnect_min: float = 0.5,
        reconnect_max: float = 30.0,
        idle_flush_sec: float = 5.0,
        max_inflight: int = 1000,
    ) -> None:
        self._outbox = outbox
        self._uri = uri
        self._token = token
        self._instance_id = instance_id
        self._service_code = service_code
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._open_timeout = open_timeout
        self._max_frame_bytes = max_frame_bytes
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._idle_flush_sec = idle_flush_sec
        self._max_inflight = max_inflight

        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None
        self._wakeup: asyncio.Event | None = None
        self._connected = False
        self._stopping = False
        self._resync_from: int | None = None
        self._last_seen = 0.0
        # 直发（send_now）产生的短命任务，持引用防被 GC
        self._ephemeral: set[asyncio.Task] = set()

    # ---------------- 构造助手 ----------------

    @classmethod
    def from_settings(cls) -> "WSClient | None":
        """按配置构造客户端；未启用或地址缺失时返回 None（调用方按"未启用"处理）。"""
        from app.core.config import settings

        if not getattr(settings, "WS_ENABLED", False):
            return None

        uri = (getattr(settings, "BACKEND_WS_URL", "") or "").strip() or _derive_ws_url(
            getattr(settings, "BACKEND_BASE_URL", "")
        )
        if not uri:
            logger.warning(
                "WS_ENABLED=true 但未解析出 backend WS 地址"
                "（请配置 BACKEND_WS_URL 或 BACKEND_BASE_URL），长连接未启用"
            )
            return None

        token = getattr(settings, "STRATEGY_INTERNAL_TOKEN", "") or ""
        if not token:
            logger.warning("WS_ENABLED=true 但 STRATEGY_INTERNAL_TOKEN 未配置，握手将被拒绝")

        instance_id = getattr(settings, "WS_INSTANCE_ID", "") or _default_instance_id()
        service_code = getattr(settings, "WS_SERVICE_CODE", "") or getattr(
            settings, "SERVICE_NAME", "cleaner-dev"
        )

        outbox = Outbox(
            Path(getattr(settings, "DATA_DIR", "data")) / "ws",
            ttl_hours=int(getattr(settings, "WS_OUTBOX_TTL_HOURS", 24)),
            max_records=int(getattr(settings, "WS_OUTBOX_MAX_RECORDS", 10_000)),
        )
        return cls(
            outbox,
            uri=uri,
            token=token,
            instance_id=instance_id,
            service_code=service_code,
            ping_interval=float(getattr(settings, "WS_PING_INTERVAL_SEC", 15.0)),
            ping_timeout=float(getattr(settings, "WS_PING_TIMEOUT_SEC", 20.0)),
            open_timeout=float(getattr(settings, "WS_OPEN_TIMEOUT_SEC", 10.0)),
            max_frame_bytes=int(getattr(settings, "WS_MAX_FRAME_BYTES", 256 * 1024)),
            reconnect_min=float(getattr(settings, "WS_RECONNECT_MIN_SEC", 0.5)),
            reconnect_max=float(getattr(settings, "WS_RECONNECT_MAX_SEC", 30.0)),
            max_inflight=int(getattr(settings, "WS_MAX_INFLIGHT", 1000)),
        )

    # ---------------- 生命周期 ----------------

    async def start(self) -> None:
        """启动后台连接任务（幂等）。必须在事件循环中调用。"""
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="dc-ws-client")
        logger.info(
            "WS 客户端已启动",
            extra={"ws_uri": self._uri, "instance_id": self._instance_id},
        )

    async def stop(self) -> None:
        """停止后台任务并尝试优雅下线。"""
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._connected = False
        metrics.set_gauge("ws_connected", 0.0)

    # ---------------- 发送入口（线程安全，永不抛出） ----------------

    def send(
        self,
        type: str,  # noqa: A002 - 与信封字段名保持一致
        payload: dict[str, Any],
        *,
        critical: bool = False,
    ) -> str | None:
        """入队一条事件并唤醒发送协程。

        可在**任意线程**调用；失败仅记日志与打点，**不抛异常**。

        Returns:
            事件 id；未入队时返回 None。
        """
        try:
            rec = self._outbox.append(type, payload, critical=critical)
        except Exception as e:  # noqa: BLE001 - 绝不因推送失败影响主流程
            logger.error(f"WS 事件入队失败（已丢弃）: {e}")
            metrics.inc("ws_enqueue_failed_total")
            return None

        metrics.inc("ws_enqueued_total")
        metrics.set_gauge("ws_outbox_depth", float(self._outbox.depth()))
        self._wake()
        return rec.id

    def send_now(
        self,
        type: str,  # noqa: A002 - 与信封字段名保持一致
        payload: dict[str, Any],
        *,
        corr_id: str | None = None,
    ) -> None:
        """**直接**在当前连接上发送一条消息（绕过 outbox）。

        用于命令响应这类**必须携带 `corr_id`** 的消息：outbox 构信封时不带
        corr_id（见 `_flush`），故不能复用 `send()`。

        线程安全、永不抛异常；未连接时静默丢弃（正确性由调用方重试 /
        `coverage.repair.status` 轮询兜底）。
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        env = protocol.envelope(type, payload, corr_id=corr_id)

        def _schedule() -> None:
            task = asyncio.ensure_future(self._safe_send(self._ws, env))
            self._ephemeral.add(task)
            task.add_done_callback(self._ephemeral.discard)

        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError as e:  # 循环已关闭
            logger.debug(f"WS 直发失败（循环已关闭）: {e}")

    def _wake(self) -> None:
        """跨线程唤醒发送协程（在事件循环线程中执行 Event.set）。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(self._notify)
        except RuntimeError as e:  # 循环已关闭
            logger.debug(f"WS 唤醒失败（循环已关闭）: {e}")

    def _notify(self) -> None:
        if self._wakeup is not None:
            self._wakeup.set()

    def is_connected(self) -> bool:
        return self._connected

    def stats(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "uri": self._uri,
            "instance_id": self._instance_id,
            "service_code": self._service_code,
            **self._outbox.stats(),
        }

    # ---------------- 连接主循环 ----------------

    def _delays(self) -> Iterator[float]:
        """重连退避序列：指数退避 + ±20% 抖动，首次按 instance_id 错峰。

        由 `_run` 的退避循环在每次重连前消费（next(delays)）。
        """
        # 错峰：同一时刻多实例重启时，避免同时冲击 backend
        stagger = (hash(self._instance_id) % 1000) / 1000.0
        yield self._reconnect_min * (1.0 + stagger)

        n = 0
        while True:
            delay = min(self._reconnect_max, self._reconnect_min * (2**n))
            yield max(0.1, delay * (1.0 + random.uniform(-0.2, 0.2)))
            n += 1

    async def _run(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            from websockets.asyncio.client import connect
        except ImportError as e:  # pragma: no cover
            logger.error(f"未安装 websockets，长连接不可用（回退 HTTP）: {e}")
            return

        # 重连退避（错峰 + 指数 + 抖动）由本类自行管理，
        # 不再依赖 websockets 内置 reconnect_delays（该参数在 14/15 已被移除，
        # 残留会作为 **kwargs 透传给 asyncio.create_connection 而崩溃）。
        delays = self._delays()
        while not self._stopping:
            try:
                async with connect(
                    self._connect_uri(),
                    additional_headers=self._build_headers(),
                    ping_interval=self._ping_interval,
                    ping_timeout=self._ping_timeout,
                    open_timeout=self._open_timeout,
                    max_size=self._max_frame_bytes,
                ) as ws:
                    if self._stopping:
                        return
                    try:
                        await self._session(ws)
                    except ConnectionClosed:
                        metrics.inc("ws_connection_closed_total")
                        logger.warning("WS 连接断开，将自动重连")
                    except Exception as e:  # noqa: BLE001
                        metrics.inc("ws_session_error_total")
                        logger.error(f"WS 会话异常，将重连: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 连接无法建立（backend 未启动等）
                metrics.inc("ws_connect_failed_total")
                logger.warning(f"WS 连接失败，等待重连: {e}")
            # 统一退避后重连：连接失败与会话结束都走这里（首次延迟已含错峰）
            await asyncio.sleep(next(delays))

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "data-cleaner-ws"}
        if self._token:
            headers[protocol.TOKEN_HEADER] = self._token
        return headers

    def _connect_uri(self) -> str:
        """连接地址：附加上身份与版本 query 参数。

        **必须带这些参数**：backend 在 accept 之前校验 `instance_id` 与协议版本，
        缺失时会在握手阶段拒绝。注意 Starlette 在握手未完成时调用 `close()`
        表现为 **HTTP 403**（而非正常的 WS 关闭码），排查时容易误判为鉴权/CORS 问题。
        """
        params = urlencode(
            {
                "instance_id": self._instance_id,
                "service_code": self._service_code,
                "v": protocol.PROTOCOL_VERSION,
            }
        )
        sep = "&" if "?" in self._uri else "?"
        return f"{self._uri}{sep}{params}"

    async def _session(self, ws: Any) -> None:
        """一次连接的会话：发送 hello → 启动发送协程 → 接收循环。"""
        self._ws = ws
        self._connected = True
        self._resync_from = None
        self._wakeup = asyncio.Event()
        metrics.inc("ws_connected_total")
        metrics.set_gauge("ws_connected", 1.0)
        logger.info(
            "WS 已连接",
            extra={
                "instance_id": self._instance_id,
                "service_code": self._service_code,
                "last_acked_seq": self._outbox.last_acked_seq,
            },
        )

        await self._send_hello(ws)
        sender = asyncio.create_task(self._sender_loop(ws), name="dc-ws-sender")
        try:
            async for raw in ws:
                self._touch()
                try:
                    env = protocol.decode(raw)
                except protocol.ProtocolError as e:
                    metrics.inc("ws_decode_error_total")
                    logger.warning(f"WS 收到非法信封: {e}")
                    continue
                await self._on_message(ws, env)
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender
            self._ws = None
            self._wakeup = None
            self._connected = False
            metrics.set_gauge("ws_connected", 0.0)

    def _touch(self) -> None:
        import time

        self._last_seen = time.time()

    async def _send_hello(self, ws: Any) -> None:
        env = protocol.envelope(
            protocol.MsgType.HELLO,
            {
                "instance_id": self._instance_id,
                "service_code": self._service_code,
                "last_acked_seq": self._outbox.last_acked_seq,
                "capabilities": {
                    "events": sorted(protocol.MsgType.PUSH_TYPES),
                    "max_frame_bytes": self._max_frame_bytes,
                },
            },
        )
        try:
            await ws.send(protocol.encode(env))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"发送 hello 失败（将随重连重试）: {e}")

    async def _on_message(self, ws: Any, env: dict[str, Any]) -> None:
        """处理 backend 下发消息：ack / ping / resync.request / welcome / error /
        command.request。"""
        mtype = env.get("type")
        payload = env.get("payload") or {}

        if mtype == protocol.MsgType.ACK:
            seq = payload.get("seq")
            if isinstance(seq, int):
                self._outbox.ack(seq)
                metrics.set_gauge("ws_outbox_depth", float(self._outbox.depth()))
            return

        if mtype == protocol.MsgType.PING:
            await self._safe_send(ws, protocol.envelope(protocol.MsgType.PONG, {}))
            return

        if mtype == protocol.MsgType.WELCOME:
            logger.info(
                "WS 握手完成",
                extra={"server_last_seq": payload.get("last_seq"), **payload},
            )
            return

        if mtype == protocol.MsgType.RESYNC_REQUEST:
            # backend 要求自某个水位补发；置位后由发送协程按该水位重放
            seq = payload.get("last_seq")
            self._resync_from = int(seq) if isinstance(seq, int) else 0
            self._wake()
            return

        if mtype == protocol.MsgType.ERROR:
            logger.warning(
                f"backend 返回错误: {payload.get('code')} - {payload.get('message')}"
            )
            metrics.inc("ws_error_message_total")
            return

        if mtype == protocol.MsgType.COMMAND_REQUEST:
            # 懒导入：避免 client ↔ commands ↔ backfill 的导入期耦合
            from app.ws import commands

            await commands.handle(self, env)
            return

        logger.debug(f"忽略未处理消息类型: {mtype}")

    async def _safe_send(self, ws: Any, env: dict[str, Any]) -> None:
        try:
            await ws.send(protocol.encode(env))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"WS 发送失败（连接可能已断）: {e}")

    # ---------------- 发送协程 ----------------

    async def _sender_loop(self, ws: Any) -> None:
        """周期性/被唤醒地冲刷 outbox；重连后自动补发未确认事件。"""
        while True:
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._idle_flush_sec)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            if self._wakeup is not None:
                self._wakeup.clear()
            try:
                await self._flush(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                metrics.inc("ws_flush_error_total")
                logger.warning(f"WS 冲刷 outbox 失败: {e}")
                return

    async def _flush(self, ws: Any) -> None:
        base = (
            self._resync_from
            if self._resync_from is not None
            else self._outbox.last_acked_seq
        )
        records = self._outbox.pending(base)
        if not records:
            if self._resync_from is not None:
                self._resync_from = None
            return

        depth = self._outbox.depth()
        if depth > self._max_inflight:
            metrics.inc("ws_backpressure_total")
            logger.warning(
                f"WS 积压过深: {depth} > {self._max_inflight}，触发背压告警"
            )

        sending = self._resync_from is not None
        self._resync_from = None
        if sending:
            logger.info(f"WS 按 backend 请求补发 {len(records)} 条事件")

        for rec in records:
            env = protocol.envelope(
                rec.type, rec.payload, seq=rec.seq, msg_id=rec.id, ts=rec.ts
            )
            try:
                text = protocol.encode(env)
            except protocol.ProtocolError as e:
                # 单帧超限：无法经 WS 投递，确认掉以免永久卡住队列（应由 HTTP 兜底）
                logger.error(f"事件 {rec.type} 无法编码（丢弃）: {e}")
                metrics.inc("ws_encode_error_total")
                self._outbox.ack(rec.seq)
                continue
            try:
                await ws.send(text)
            except Exception as e:  # noqa: BLE001 - 连接已断，留给重连后补发
                logger.debug(f"WS 发送中断（seq={rec.seq}）: {e}")
                return
            metrics.inc("ws_message_sent_total")
