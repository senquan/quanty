"""dc 侧事件源门面：对外统一的事件推送入口

设计约束（docs/plans/2026-09-04.ws-dc-backend.md §9.4 硬约束）：
- 所有 `emit_*` 均为**尽力而为**：未启用 / 未连接 / 异常时静默降级，
  **绝不抛出异常、绝不阻塞调用方**。
  因此 EOD 流水线（运行在 executor 同步线程）与 HTTP 请求处理器都可安全调用。
- 关键事件（`factor.updated` / `execution.report` / `signal.generated`）标记
  `critical=True`，outbox 容量超限时会**优先保留**。

使用方式：
    # 服务启动时（lifespan）
    client = WSClient.from_settings()
    if client:
        events.bind(client)
        await client.start()

    # 业务侧 anywhere
    events.emit_factor_updated(codes=["MOM_20"], reason="factor_build")
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core.logging import get_logger
from app.ws import protocol
from app.ws.client import WSClient

logger = get_logger(__name__)

_client: WSClient | None = None


def bind(client: WSClient | None) -> None:
    """绑定客户端实例（服务启动时调用）。"""
    global _client
    _client = client


def get_client() -> WSClient | None:
    """获取当前绑定的客户端（未启用时返回 None）。"""
    return _client


def is_enabled() -> bool:
    """长连接是否已启用。"""
    return _client is not None


def is_connected() -> bool:
    """当前是否处于已连接状态。"""
    return _client is not None and _client.is_connected()


def stats() -> dict[str, Any]:
    """WS 与 outbox 运行状态（供 /qos 或排查使用）。"""
    if _client is None:
        return {"enabled": False}
    return {"enabled": True, **_client.stats()}


def _emit(
    type: str,  # noqa: A002 - 与信封字段名保持一致
    payload: dict[str, Any],
    *,
    critical: bool = False,
) -> str | None:
    """入队一条事件；任何异常都被吞掉（不阻断调用方）。"""
    client = _client
    if client is None:
        return None
    try:
        return client.send(type, payload, critical=critical)
    except Exception as e:  # noqa: BLE001 - 双保险：send 本身已兜底，这里再兜一层
        logger.error(f"WS 事件发送失败（已忽略）: type={type} err={e}")
        return None


# ---------------- 具体事件 ----------------


async def emit_status() -> None:
    """推送 QoS 快照 —— **替代 backend 的 30s /qos 轮询**（Phase 2）。

    数据与 `GET /api/v1/qos` 完全一致（共用 `app.core.qos.build_qos_snapshot`）。
    """
    from app.core import qos as qos_mod

    try:
        snapshot = await qos_mod.build_qos_snapshot()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"构造 QoS 快照失败，跳过状态推送: {e}")
        return

    _emit(
        protocol.MsgType.EVENT_STATUS,
        protocol.status_payload(
            status=snapshot.get("status", "online"),
            qos=snapshot,
            version=(snapshot.get("system") or {}).get("version"),
        ),
    )


def emit_presence(online: bool, reason: str | None = None) -> None:
    """上/下线通知（连接建立与优雅退出时发送）。"""
    _emit(protocol.MsgType.EVENT_PRESENCE, protocol.presence_payload(online=online, reason=reason))


def emit_pipeline(
    kind: str,
    *,
    task: str,
    step: str | None = None,
    progress: float | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """长任务进度（EOD 流水线 / 因子构建 / 回填）。

    Args:
        kind: `started` / `progress` / `step` / `finished` / `failed`
    """
    mapping = {
        "started": protocol.MsgType.EVENT_PIPELINE_STARTED,
        "progress": protocol.MsgType.EVENT_PIPELINE_PROGRESS,
        "step": protocol.MsgType.EVENT_PIPELINE_STEP,
        "finished": protocol.MsgType.EVENT_PIPELINE_FINISHED,
        "failed": protocol.MsgType.EVENT_PIPELINE_FAILED,
    }
    mtype = mapping.get(kind)
    if mtype is None:
        logger.warning(f"未知流水线事件类型: {kind}")
        return
    _emit(
        mtype,
        protocol.pipeline_payload(
            task=task, step=step, progress=progress, detail=detail
        ),
    )


def emit_factor_updated(
    codes: Iterable[str],
    *,
    version: str | None = None,
    reason: str | None = None,
) -> None:
    """因子变更广播 —— **副本同步通道（根基，Phase 3）**。

    只传因子 code 与版本号，**不传因子矩阵**；backend 收到后标记 stale 并
    经 HTTP 增量同步真实数据（避免大消息造成队头阻塞，设计文档 §4.3 / §5.2）。
    """
    from app.core.config import settings

    service_code = getattr(settings, "WS_SERVICE_CODE", "") or getattr(
        settings, "SERVICE_NAME", "cleaner-dev"
    )
    code_list = list(codes)
    if not code_list:
        return
    _emit(
        protocol.MsgType.EVENT_FACTOR_UPDATED,
        protocol.factor_updated_payload(
            service_code=service_code, codes=code_list, version=version, reason=reason
        ),
        critical=True,
    )
