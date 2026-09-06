"""dc ↔ backend WebSocket 通讯协议契约（v1）

本文件在 data-cleaner 与 backend 两侧**逐字保持一致**，由契约测试校验一致性。
任一侧修改后必须同步另一侧，否则测试失败（防止协议漂移）。

信封结构：
    {
      "v": 1,                 协议版本，用于协商；不匹配则拒绝并回退 HTTP
      "id": "01J...",         消息唯一 id，接收端据此幂等去重
      "type": "event.status", 消息类型（见 MsgType）
      "ts": "2026-...",       发送方 ISO8601 时间戳
      "seq": 1024,            发送方连接内单调递增，驱动 ack 与断线补发
      "corr_id": "...",       请求-响应关联（响应必填，幂等键）
      "reply_to": null,       预留：跨实例路由
      "payload": { },         业务载荷
      "meta": { }             元数据（分片等）
    }

设计要点：
- bulk 数据（因子矩阵 / correlation / parquet）**不走 WS**，仅传通知与小载荷，
  避免队头阻塞拖死心跳与控制消息（见 docs/plans/2026-09-04.ws-dc-backend.md §4.3）。
- 单帧上限 MAX_FRAME_BYTES，超出必须分片或改走 HTTP。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

#: 当前协议版本（任一侧不兼容变更时必须递增，并保留对旧版本的兼容或明确回退）
PROTOCOL_VERSION = 1

#: 本端支持的版本集合（握手时协商）
SUPPORTED_VERSIONS: tuple[int, ...] = (1,)

#: 单帧上限（字节）。超出必须分片或改走 HTTP。
MAX_FRAME_BYTES = 256 * 1024

#: WS 路径与鉴权头
WS_PATH = "/ws/dc"
TOKEN_HEADER = "x-internal-token"


class ProtocolError(Exception):
    """信封解析 / 校验失败。"""


class MsgType:
    """消息类型常量。

    方向说明：
    - dc → backend：hello/bye、ping/pong、event.*
    - backend → dc：welcome、pong、ack、resync.request、strategy.compute、error
    """

    # ---- 连接与保活 ----
    HELLO = "hello"                    # dc → backend：上报身份与能力
    WELCOME = "welcome"                # backend → dc：握手确认（含 resync 水位）
    BYE = "bye"                        # dc → backend：优雅下线
    PING = "ping"                      # 双向：应用级心跳
    PONG = "pong"                      # 双向：心跳应答

    # ---- dc → backend：事件推送 ----
    EVENT_STATUS = "event.status"                          # 【迁移】替代 30s poll_qos
    EVENT_PRESENCE = "event.presence"                      # 【迁移】上线/下线/降级
    EVENT_PIPELINE_STARTED = "event.pipeline.started"      # 【迁移】
    EVENT_PIPELINE_PROGRESS = "event.pipeline.progress"    # 【迁移】
    EVENT_PIPELINE_STEP = "event.pipeline.step"            # 【迁移】
    EVENT_PIPELINE_FINISHED = "event.pipeline.finished"    # 【迁移】
    EVENT_PIPELINE_FAILED = "event.pipeline.failed"        # 【迁移】
    EVENT_FACTOR_UPDATED = "event.factor.updated"          # 【新增·根基】副本同步触发源
    EVENT_SIGNAL_GENERATED = "event.signal.generated"      # 【预留】不实现生成
    EVENT_EXECUTION_REPORT = "event.execution.report"      # 【新增】撮合成交回报

    # ---- backend → dc：下发 ----
    ACK = "ack"                                            # 按 seq 确认
    RESYNC_REQUEST = "resync.request"                      # 请求补发（自 last_seq 起）
    STRATEGY_COMPUTE = "strategy.compute"                  # 【预留】提交策略请求计算
    ERROR = "error"                                        # 错误 / nack

    #: dc → backend 的**事件推送**类：需要 seq 与 ack 保障（outbox 缓冲 + 断线补发）
    PUSH_TYPES: frozenset[str] = frozenset(
        {
            EVENT_STATUS,
            EVENT_PRESENCE,
            EVENT_PIPELINE_STARTED,
            EVENT_PIPELINE_PROGRESS,
            EVENT_PIPELINE_STEP,
            EVENT_PIPELINE_FINISHED,
            EVENT_PIPELINE_FAILED,
            EVENT_FACTOR_UPDATED,
            EVENT_SIGNAL_GENERATED,
            EVENT_EXECUTION_REPORT,
        }
    )

    #: 预留但未实现的类型：收到仅记录日志，**不视为错误**
    RESERVED_TYPES: frozenset[str] = frozenset(
        {EVENT_SIGNAL_GENERATED, STRATEGY_COMPUTE}
    )

    #: 不可丢弃的高价值事件（outbox 超限时优先保留）
    CRITICAL_TYPES: frozenset[str] = frozenset(
        {EVENT_FACTOR_UPDATED, EVENT_EXECUTION_REPORT, EVENT_SIGNAL_GENERATED}
    )


def new_id() -> str:
    """生成消息唯一 id（零依赖，使用 uuid4 hex）。"""
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    """当前 UTC 时间的 ISO8601 表示（带时区）。"""
    return datetime.now(timezone.utc).isoformat()


def envelope(
    type: str,  # noqa: A002 - 与信封字段名保持一致
    payload: dict[str, Any] | None = None,
    *,
    seq: int | None = None,
    corr_id: str | None = None,
    msg_id: str | None = None,
    ts: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造信封。

    Args:
        type: 消息类型（见 MsgType）。
        payload: 业务载荷，缺省为空字典。
        seq: 发送方连接内单调序号；事件推送类必填（用于 ack / 补发）。
        corr_id: 请求-响应关联 id；响应消息必填，同时作为幂等键。
        msg_id: 消息 id，缺省自动生成（接收端据此去重）。
        ts: 时间戳，缺省取当前 UTC。
        meta: 元数据（如分片信息）。
    """
    return {
        "v": PROTOCOL_VERSION,
        "id": msg_id or new_id(),
        "type": type,
        "ts": ts or utc_now_iso(),
        "seq": seq,
        "corr_id": corr_id,
        "reply_to": None,
        "payload": payload or {},
        "meta": meta or {},
    }


def encode(env: dict[str, Any]) -> str:
    """序列化信封为文本；超限时抛错（调用方应改走 HTTP 或分片）。"""
    try:
        text = json.dumps(env, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as e:  # pragma: no cover - 防御性
        raise ProtocolError(f"信封序列化失败: {e}") from e

    size = len(text.encode("utf-8"))
    if size > MAX_FRAME_BYTES:
        raise ProtocolError(
            f"单帧超限: {size}B > {MAX_FRAME_BYTES}B（type={env.get('type')}）"
            " —— bulk 数据应改走 HTTP 或分片"
        )
    return text


def decode(text: str) -> dict[str, Any]:
    """反序列化并校验信封；格式/版本不合法时抛 ProtocolError。"""
    try:
        env = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"非法 JSON: {e}") from e

    if not isinstance(env, dict):
        raise ProtocolError(f"信封必须是对象，收到: {type(env).__name__}")

    version = env.get("v")
    if not is_supported_version(version):
        raise ProtocolError(f"不支持的协议版本: {version}（本端支持 {SUPPORTED_VERSIONS}）")

    if not env.get("type"):
        raise ProtocolError("信封缺少 type 字段")

    env.setdefault("payload", {})
    env.setdefault("meta", {})
    return env


def is_supported_version(version: Any) -> bool:
    """版本协商：不匹配时调用方应拒绝连接并回退 HTTP。"""
    return isinstance(version, int) and version in SUPPORTED_VERSIONS


def error_envelope(
    code: str,
    message: str,
    *,
    corr_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """构造错误 / nack 信封。"""
    return envelope(
        MsgType.ERROR,
        {"code": code, "message": message, "retryable": retryable},
        corr_id=corr_id,
    )


# ---------- 载荷构造助手（保证两侧字段口径一致） ----------


def status_payload(
    *,
    status: str,
    qos: dict[str, Any] | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """event.status 载荷：替代 backend 的 30s poll_qos。"""
    return {
        "status": status,
        "qos": qos or {},
        "version": version,
    }


def presence_payload(*, online: bool, reason: str | None = None) -> dict[str, Any]:
    """event.presence 载荷：上线 / 下线 / 降级。"""
    return {"online": online, "reason": reason}


def factor_updated_payload(
    *,
    service_code: str,
    codes: Iterable[str],
    version: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """event.factor.updated 载荷（根基：副本同步触发源）。

    注意：**只传因子 code 与版本号，不传因子矩阵**——数据由 backend
    后台经 HTTP 增量拉取，避免大消息造成队头阻塞。
    """
    return {
        "service_code": service_code,
        "codes": list(codes),
        "version": version,
        "reason": reason,
    }


def pipeline_payload(
    *,
    task: str,
    step: str | None = None,
    progress: float | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """event.pipeline.* 载荷：长任务实时进度。"""
    return {
        "task": task,
        "step": step,
        "progress": progress,
        "detail": detail or {},
    }
