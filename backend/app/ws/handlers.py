"""WS 事件分发与处理入口

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §4.2 / §5 / §6 / §7）：

- 分发前按事件 `id` 做**幂等去重**（内存有界 LRU；无 Redis 时的默认路径，
  最终正确性由业务幂等兜底 —— 见 §9.6）。
- 事件推送类（PUSH_TYPES）处理完成后回 `ack(seq)`，驱动 dc 清理 outbox。
- `factor.updated` 是**副本同步通道（根基）**：标记 stale + 触发增量同步（§5.2）。
- `signal.generated` / `execution.report` 属 Phase 5，此处仅**占位记录**，
  不实现落库（§6.3 明确不做）。

DB 写入口径必须与 `cleaner_gateway.poll_qos` **保持一致**
（status / qos / last_heartbeat），否则两条链路会产生不同的注册表状态。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.cleaner import CleanerService
from app.ws import protocol
from app.ws.registry import Connection, registry

logger = logging.getLogger(__name__)

HandlerFn = Callable[[Connection, dict[str, Any]], Awaitable[None]]


# ---------------- 分发 ----------------


async def dispatch(conn: Connection, env: dict[str, Any]) -> None:
    """分发一条已解码的信封。"""
    mtype = env.get("type")
    msg_id = env.get("id")
    seq = env.get("seq")

    # 幂等：重复投递仍回 ack（让 dc 清理 outbox），但不重复处理
    if msg_id and registry.seen_before(msg_id):
        logger.debug(f"重复消息已跳过 type={mtype} id={msg_id}")
        await _ack(conn, seq)
        return

    # 记录最近消息 id，供落库时追溯（signal / execution_report 会用到）
    conn.last_message_id = msg_id

    handler = _HANDLERS.get(mtype or "")
    if handler is None:
        if mtype in protocol.MsgType.RESERVED_TYPES:
            logger.info(f"收到预留消息（未实现）: {mtype}")
        else:
            logger.debug(f"忽略未处理消息类型: {mtype}")
        await _ack(conn, seq)
        return

    try:
        await handler(conn, env.get("payload") or {})
    except Exception as e:  # noqa: BLE001 - 单条消息失败不得断开连接
        logger.error(f"处理消息失败 type={mtype}: {e}")

    if mtype in protocol.MsgType.PUSH_TYPES and isinstance(seq, int):
        registry.mark_seq(conn.instance_id, seq)
    await _ack(conn, seq)


async def _ack(conn: Connection, seq: Any) -> None:
    """回执 ack，驱动 dc 侧 outbox 清理。"""
    if not isinstance(seq, int):
        return
    try:
        await conn.ws.send_text(
            protocol.encode(protocol.envelope(protocol.MsgType.ACK, {"seq": seq}))
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"发送 ack 失败（连接可能已断）: {e}")


# ---------------- 具体处理器 ----------------


async def _on_hello(conn: Connection, payload: dict[str, Any]) -> None:
    """dc 上报身份与能力；首次连接时补全 service_code / 水位。"""
    service_code = payload.get("service_code") or conn.service_code
    if service_code and not conn.service_code:
        conn.service_code = service_code
    logger.info(
        "dc 握手完成",
        extra={
            "instance_id": conn.instance_id,
            "service_code": conn.service_code,
            "last_acked_seq": payload.get("last_acked_seq"),
            "capabilities": payload.get("capabilities"),
        },
    )


async def _on_ping(conn: Connection, payload: dict[str, Any]) -> None:
    try:
        await conn.ws.send_text(protocol.encode(protocol.envelope(protocol.MsgType.PONG, {})))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"发送 pong 失败: {e}")


async def _on_bye(conn: Connection, payload: dict[str, Any]) -> None:
    logger.info(
        f"dc 优雅下线 instance_id={conn.instance_id} reason={payload.get('reason')}"
    )
    # 标记为离线（连接随后会关闭）
    await _update_service(
        conn.service_code, status="offline", reason=payload.get("reason")
    )


async def _on_status(conn: Connection, payload: dict[str, Any]) -> None:
    """QoS 快照 —— **替代 30s /qos 轮询**（Phase 2，§5 / §4.2）。

    载荷中的 `qos` 与 dc 的 `GET /api/v1/qos` 返回结构完全一致
    （共用 `app.core.qos.build_qos_snapshot`）。
    """
    qos = payload.get("qos") or {}
    status = payload.get("status") or "online"
    await _update_service(conn.service_code, status=status, qos=qos)


async def _on_presence(conn: Connection, payload: dict[str, Any]) -> None:
    online = bool(payload.get("online", True))
    await _update_service(
        conn.service_code,
        status="online" if online else "offline",
        reason=payload.get("reason"),
    )


async def _on_pipeline(conn: Connection, payload: dict[str, Any]) -> None:
    """长任务进度（EOD 流水线 / 因子构建 / 回填）。

    Phase 4 会将其暴露到前端；当前先记录在连接上并打日志，
    避免 `/pipeline/status` 依赖 dc 进程内全局变量（§2.2 既有缺陷）。
    """
    conn.last_pipeline = {
        **payload,
        "instance_id": conn.instance_id,
        "received_at": datetime.now().isoformat(),
    }
    logger.info(
        "流水线进度",
        extra={
            "instance_id": conn.instance_id,
            "task": payload.get("task"),
            "step": payload.get("step"),
            "progress": payload.get("progress"),
        },
    )


async def _on_factor_updated(conn: Connection, payload: dict[str, Any]) -> None:
    """因子变更广播 —— **副本同步通道（根基，Phase 3，§5.2）**。

    只带 code + 版本，**不含因子矩阵**；真实数据由后台经 HTTP 增量同步。
    """
    import asyncio

    from app.services import factor_replica_sync as frs

    service_code = payload.get("service_code") or conn.service_code
    codes = payload.get("codes") or []
    if not service_code:
        logger.warning("factor.updated 缺少 service_code，无法定位副本归属")
        return

    await frs.mark_stale(service_code, codes)
    # 异步触发增量同步：绝不阻塞 WS 接收循环
    asyncio.create_task(frs.sync_service(service_code))  # noqa: RUF006


async def _on_signal_generated(conn: Connection, payload: dict[str, Any]) -> None:
    """信号产出落库（模式 A）—— **只落库，不实现生成逻辑**（§6.3）。

    语义：dc 算出目标持仓后推给 backend，由 backend 走风控与最终决策，
    故这里统一置为 `pending`，交由风控流程消费。

    幂等：依赖 `signals` 表的 `UNIQUE (strategy_id, as_of)`，
    重复推送（WS 至少一次投递）会被唯一约束挡下并记为忽略。
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.signal import Signal

    strategy_id = str(payload.get("strategy_id") or "")
    as_of = _parse_date(payload.get("as_of"))
    if not strategy_id or as_of is None:
        logger.warning(
            f"信号缺少 strategy_id / as_of，无法落库: strategy_id={strategy_id!r} as_of={payload.get('as_of')!r}"
        )
        return

    holdings = payload.get("holdings") or []
    async with AsyncSessionLocal() as db:
        db.add(
            Signal(
                instance_id=conn.instance_id,
                service_code=conn.service_code,
                strategy_id=strategy_id,
                as_of=as_of,
                trade_date=_parse_date(payload.get("trade_date")),
                holdings=holdings,
                diagnostics=payload.get("diagnostics") or {},
                holdings_count=len(holdings),
                status="pending",
                message_id=conn.last_message_id,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.info(
                "重复信号已忽略（幂等命中 strategy_id+as_of）",
                extra={"strategy_id": strategy_id, "as_of": str(as_of)},
            )
            return

    logger.info(
        "信号已落库（待风控与最终决策）",
        extra={
            "instance_id": conn.instance_id,
            "strategy_id": strategy_id,
            "as_of": str(as_of),
            "holdings_count": len(holdings),
        },
    )


async def _on_execution_report(conn: Connection, payload: dict[str, Any]) -> None:
    """成交回报落库（模式 B）—— dc 直接撮合成交后回推，backend 据此感知持仓（§7）。

    幂等：依赖 `execution_reports.report_id` 唯一约束，重复推送不会重复记账。
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.signal import ExecutionReport

    report_id = str(payload.get("report_id") or "")
    if not report_id:
        logger.warning("成交回报缺少 report_id，无法幂等落库")
        return

    fills = payload.get("fills") or []
    gross = sum(_to_float(f.get("amount")) for f in fills)
    fee = sum(_to_float(f.get("fee")) for f in fills)

    async with AsyncSessionLocal() as db:
        db.add(
            ExecutionReport(
                report_id=report_id,
                instance_id=conn.instance_id,
                service_code=conn.service_code,
                strategy_id=(payload.get("strategy_id") or None),
                executed_at=_parse_datetime(payload.get("executed_at")),
                fills=fills,
                fill_count=len(fills),
                gross_amount=gross,
                fee_total=fee,
                status="received",
                message_id=conn.last_message_id,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.info(f"重复成交回报已忽略（幂等命中 report_id={report_id}）")
            return

    logger.info(
        "成交回报已落库（待回写持仓）",
        extra={
            "instance_id": conn.instance_id,
            "report_id": report_id,
            "strategy_id": payload.get("strategy_id"),
            "fill_count": len(fills),
        },
    )


# ---------------- 小工具 ----------------


def _parse_date(value: Any):
    """把 'YYYY-MM-DD' / date / datetime 转为 date；失败返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    """解析 ISO8601 时间串，并归一为**朴素本地时间**。

    为什么必须去掉时区：本库既有表（`cleaner_services` / `factor_registry`）
    统一使用 `timestamp without time zone` + `datetime.now()`，
    asyncpg 会**拒绝**把带时区的 datetime 写入无时区列
    （DataError: invalid input for query argument）。
    故此处先按本地时区换算，再剥离 tzinfo，与库内其它时间戳保持同一基准。

    失败时返回 None（成交时间缺失不阻断落库）。
    """
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------- 注册表更新（与 poll_qos 口径一致） ----------------


async def _update_service(
    service_code: str | None,
    *,
    status: str | None = None,
    qos: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """更新 cleaner_services 的状态 / qos / last_heartbeat。

    字段口径与 `cleaner_gateway.poll_qos` 完全一致，保证 HTTP 轮询与 WS 推送
    两条链路写入相同状态，不会出现"同一服务两种状态"。
    """
    if not service_code:
        return
    try:
        async with AsyncSessionLocal() as db:
            svc = (
                await db.execute(
                    select(CleanerService).where(
                        CleanerService.service_code == service_code
                    )
                )
            ).scalar_one_or_none()
            if svc is None:
                logger.warning(
                    f"WS 消息指向未注册的 service_code={service_code}"
                    "（请先在 backend 注册清洗服务）"
                )
                return
            if status:
                svc.status = status
            if qos is not None:
                svc.qos = qos
            elif status == "offline" and reason:
                svc.qos = {"offline_reason": reason}
            svc.last_heartbeat = datetime.now()
            await db.commit()
    except Exception as e:  # noqa: BLE001 - 状态更新失败不得断开连接
        logger.error(f"更新 cleaner_services 失败 service_code={service_code}: {e}")


_HANDLERS: dict[str, HandlerFn] = {
    protocol.MsgType.HELLO: _on_hello,
    protocol.MsgType.PING: _on_ping,
    protocol.MsgType.BYE: _on_bye,
    protocol.MsgType.EVENT_STATUS: _on_status,
    protocol.MsgType.EVENT_PRESENCE: _on_presence,
    protocol.MsgType.EVENT_PIPELINE_STARTED: _on_pipeline,
    protocol.MsgType.EVENT_PIPELINE_PROGRESS: _on_pipeline,
    protocol.MsgType.EVENT_PIPELINE_STEP: _on_pipeline,
    protocol.MsgType.EVENT_PIPELINE_FINISHED: _on_pipeline,
    protocol.MsgType.EVENT_PIPELINE_FAILED: _on_pipeline,
    protocol.MsgType.EVENT_FACTOR_UPDATED: _on_factor_updated,
    protocol.MsgType.EVENT_SIGNAL_GENERATED: _on_signal_generated,
    protocol.MsgType.EVENT_EXECUTION_REPORT: _on_execution_report,
}
