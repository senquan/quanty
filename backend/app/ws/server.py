"""backend WebSocket 服务端：接收 N 个 dc 实例连入（1 对多）

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §3 / §8）：

- backend **单实例**作服务端，dc 作客户端主动拨出。
- 握手鉴权：dc 在 HTTP 升级请求头携带 `X-Internal-Token`
  （server-to-server 可自由设头，无需像浏览器那样塞 query 参数）。
- 版本协商失败 → 直接拒绝连接，让 dc **回退 HTTP**（§4.4）。
- 防护：连接数上限、单帧上限、消息速率限流（§8）。

运维注意：**LB / 反向代理的空闲超时必须大于心跳间隔**（WS ping 15s），
否则连接会被静默踢掉 —— 这是 WS 上线最常见的事故点（§11）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.ws import handlers, protocol
from app.ws.registry import Connection, registry

logger = logging.getLogger(__name__)

router = APIRouter()

# WS 关闭码（RFC 6455）
_CLOSE_POLICY_VIOLATION = 1008
_CLOSE_TRY_AGAIN = 1013


@router.websocket(protocol.WS_PATH)
async def websocket_dc(
    websocket: WebSocket,
    instance_id: str = Query(default="", description="dc 实例身份，多实例部署时须唯一"),
    service_code: str = Query(default="", description="对应 cleaner_services.service_code"),
    v: int = Query(default=protocol.PROTOCOL_VERSION, description="协议版本"),
) -> None:
    """dc 长连接端点。

    鉴权与校验顺序（全部在 accept 之前完成，失败即拒绝）：
    1. 总开关 WS_ENABLED
    2. X-Internal-Token
    3. instance_id 必填
    4. 协议版本协商
    """
    # 1) 总开关：关闭时端点虽挂载但直接拒绝，便于一键回退
    if not getattr(settings, "WS_ENABLED", False):
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="ws disabled")
        return

    # 2) 内部令牌（server-to-server 握手）
    expected = getattr(settings, "STRATEGY_INTERNAL_TOKEN", "") or ""
    token = (
        websocket.headers.get(protocol.TOKEN_HEADER)
        or websocket.headers.get("X-Internal-Token")
        or ""
    )
    if expected and token != expected:
        logger.warning(
            "WS 握手鉴权失败",
            extra={"instance_id": instance_id, "remote": _remote(websocket)},
        )
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="unauthorized")
        return

    # 3) 实例身份必填（连接注册表的键；dc 当前 1 个、未来会多）
    if not instance_id:
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="missing instance_id")
        return

    # 4) 版本协商：不匹配则拒绝，调用方自动回退 HTTP
    if not protocol.is_supported_version(v):
        await websocket.close(
            code=_CLOSE_POLICY_VIOLATION, reason="unsupported protocol version"
        )
        return

    await websocket.accept()

    conn = Connection(
        instance_id=instance_id,
        service_code=service_code,
        ws=websocket,
        protocol_version=int(v),
        remote=_remote(websocket),
    )
    if not await registry.register(conn):
        await websocket.close(code=_CLOSE_TRY_AGAIN, reason="too many connections")
        return

    # 下发 welcome（携带本端记录的水位，供 dc 决定补发起点）
    try:
        await websocket.send_text(
            protocol.encode(
                protocol.envelope(
                    protocol.MsgType.WELCOME,
                    {
                        "instance_id": instance_id,
                        "last_seq": registry.last_seq(instance_id),
                        "server_time": datetime.now().isoformat(),
                        "max_frame_bytes": getattr(
                            settings, "WS_MAX_FRAME_BYTES", protocol.MAX_FRAME_BYTES
                        ),
                    },
                )
            )
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"发送 welcome 失败: {e}")

    max_frame = int(getattr(settings, "WS_MAX_FRAME_BYTES", protocol.MAX_FRAME_BYTES))
    try:
        while True:
            raw = await websocket.receive_text()
            conn.touch()
            # 刷新"该服务最近在线时刻"，供断连告警计算**真实**失联时长
            #（只靠 register 记录会把连接存续时长算进失联，见 registry.mark_seen 注释）
            registry.mark_seen(conn.service_code)
            registry.count_message()

            if len(raw.encode("utf-8")) > max_frame:
                logger.warning(
                    f"单帧超限，断开 instance_id={instance_id}"
                    f"（{len(raw.encode('utf-8'))}B > {max_frame}B）"
                )
                await websocket.close(code=1009, reason="message too big")
                break

            if conn.rate_limit_hit(registry.rate_limit):
                logger.warning(f"消息速率超限，断开 instance_id={instance_id}")
                await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="rate limit")
                break

            try:
                env = protocol.decode(raw)
            except protocol.ProtocolError as e:
                logger.warning(f"收到非法信封 instance_id={instance_id}: {e}")
                continue

            await handlers.dispatch(conn, env)
    except WebSocketDisconnect:
        logger.info(f"dc 正常断开 instance_id={instance_id}")
    except Exception as e:  # noqa: BLE001 - 单连接异常不得影响服务端
        logger.error(f"WS 会话异常 instance_id={instance_id}: {e}")
    finally:
        await registry.unregister(instance_id)


def _remote(websocket: WebSocket) -> str | None:
    try:
        client = websocket.client
        return f"{client.host}:{client.port}" if client else None
    except Exception:  # noqa: BLE001
        return None


@router.get("/ws/status", summary="WS 长连接状态、各 dc 最近流水线进度与副本待同步")
async def ws_status() -> dict:
    """内部运维端点：用于排查"连上了吗 / 在推什么 / 副本有没有落后"。

    与 dc 的 `/api/v1/qos` 同属内部只读探测，不挂鉴权（内网使用）。
    注意：仅当 `WS_ENABLED=true` 时挂载，关闭时该端点不存在。
    """
    from app.services import factor_replica_sync as frs

    # 断连告警（只读、不查库）：对本进程内出现过的 service_code 计算失联时长
    alert_sec = int(getattr(settings, "WS_DISCONNECT_ALERT_SEC", 300))
    try:
        alerts = registry.disconnect_report(registry.known_service_codes(), alert_sec)
    except Exception as e:  # noqa: BLE001 - 探测失败不影响主状态输出
        logger.warning(f"构造断连告警失败: {e}")
        alerts = []

    return {
        "enabled": True,
        "connections": registry.stats(),
        "replica_stale": frs.stale_snapshot(),
        "disconnect_alert_sec": alert_sec,
        "disconnect_alerts": alerts,
    }
