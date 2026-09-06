"""本地 outbox：dc → backend 事件推送的**发送侧缓冲**

设计依据（docs/plans/2026-09-04.ws-dc-backend.md §9.2）：

- **outbox 在发送侧（dc）**：dc 是 WS 客户端并负责重连，故缓冲与补发职责归 dc；
  backend 只需按 `seq` 回 `ack`、按事件 `id` 去重。这样 backend **无需持久化水位**，
  也让 Redis 成为完全可选项（§9.6）。
- 事件**先落盘再发送**，进程崩溃最多丢最后一行（JSONL 追加写）。
- 收到 `ack(seq)` 后清理；断线重连后自 `last_acked_seq + 1` 补发。
- TTL 与容量双上限。超限优先丢弃**非关键**事件（流水线进度），
  保留 `factor.updated` / `execution.report` / `signal.generated`。

线程安全：EOD 流水线跑在 `run_in_executor` 的**同步线程**中，因此本类所有公开方法
均可在任意线程调用（内部用 RLock 保护）。
"""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class OutboxRecord:
    """一条待推送事件。"""

    seq: int
    id: str
    type: str
    ts: str
    payload: dict[str, Any] = field(default_factory=dict)
    critical: bool = False

    def to_line(self) -> str:
        return json.dumps(
            asdict(self), ensure_ascii=False, separators=(",", ":"), default=str
        )

    @classmethod
    def from_obj(cls, obj: Any) -> OutboxRecord | None:
        """从反序列化对象构造；脏数据返回 None 由调用方跳过。"""
        if not isinstance(obj, dict):
            return None
        try:
            return cls(
                seq=int(obj["seq"]),
                id=str(obj["id"]),
                type=str(obj["type"]),
                ts=str(obj["ts"]),
                payload=obj.get("payload") or {},
                critical=bool(obj.get("critical", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None


class Outbox:
    """JSONL 追加写 + 内存索引的事件缓冲。

    Args:
        directory: outbox 文件存放目录。
        ttl_hours: 记录保留时长（小时），超期视为作废。
        max_records: 内存/磁盘记录数上限，超出按"非关键优先"丢弃。
        filename: 文件前缀，默认 `ws_outbox`。
    """

    def __init__(
        self,
        directory: Path,
        *,
        ttl_hours: int = 24,
        max_records: int = 10_000,
        filename: str = "ws_outbox",
    ) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._data_path = self._dir / f"{filename}.jsonl"
        self._state_path = self._dir / f"{filename}.state.json"
        self._ttl = timedelta(hours=ttl_hours)
        self._max_records = max(1, int(max_records))

        self._lock = threading.RLock()
        self._records: deque[OutboxRecord] = deque()
        self._last_acked_seq = 0
        self._next_seq = 1
        self._writes_since_compact = 0
        self._dropped_total = 0

        self._load()

    # ---------------- 持久化 ----------------

    def _load(self) -> None:
        """启动时重放未确认且未过期的记录，并恢复 seq 水位。"""
        with self._lock:
            self._load_state()

            if not self._data_path.exists():
                return

            loaded = 0
            expired = 0
            acked = 0
            try:
                with self._data_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = OutboxRecord.from_obj(json.loads(line))
                        except json.JSONDecodeError:
                            continue  # 崩溃导致的半行，跳过
                        if rec is None:
                            continue
                        self._next_seq = max(self._next_seq, rec.seq + 1)
                        if rec.seq <= self._last_acked_seq:
                            acked += 1
                            continue
                        if self._is_expired(rec):
                            expired += 1
                            continue
                        self._records.append(rec)
                        loaded += 1
            except OSError as e:
                logger.warning(f"outbox 读取失败（按空队列继续）: {e}")
                return

            if loaded or expired or acked:
                logger.info(
                    "outbox 载入完成",
                    extra={
                        "outbox_loaded": loaded,
                        "outbox_expired": expired,
                        "outbox_acked_skipped": acked,
                        "last_acked_seq": self._last_acked_seq,
                    },
                )
            if acked or expired:
                self._compact_locked()

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._last_acked_seq = int(state.get("last_acked_seq", 0))
            self._next_seq = max(1, int(state.get("next_seq", 1)))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"outbox 状态读取失败，重置水位: {e}")

    def _save_state(self) -> None:
        payload = {"last_acked_seq": self._last_acked_seq, "next_seq": self._next_seq}
        tmp = self._state_path.with_suffix(".state.json.tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self._state_path)  # 原子替换，避免崩溃留下半截状态
        except OSError as e:
            logger.warning(f"outbox 状态写入失败: {e}")

    def _compact_locked(self) -> None:
        """重写数据文件，丢弃已确认/过期记录（调用方需持锁）。"""
        try:
            tmp = self._data_path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for rec in self._records:
                    f.write(rec.to_line() + "\n")
            tmp.replace(self._data_path)
            self._writes_since_compact = 0
        except OSError as e:
            logger.warning(f"outbox 压缩失败（不影响正确性）: {e}")

    # ---------------- 公开 API ----------------

    def append(
        self,
        type: str,  # noqa: A002 - 与信封字段名保持一致
        payload: dict[str, Any],
        *,
        critical: bool = False,
        msg_id: str | None = None,
    ) -> OutboxRecord:
        """入队一条事件并返回记录（含分配的 seq）。

        容量超限时优先丢弃最旧的非关键事件；若全部为关键事件仍超限，
        才丢弃最旧记录以保证磁盘有界（并记 warning）。
        """
        import uuid

        with self._lock:
            rec = OutboxRecord(
                seq=self._next_seq,
                id=msg_id or uuid.uuid4().hex,
                type=type,
                ts=datetime.now(timezone.utc).isoformat(),
                payload=payload,
                critical=critical,
            )
            self._next_seq += 1

            if len(self._records) >= self._max_records:
                self._evict_locked()

            self._records.append(rec)
            try:
                with self._data_path.open("a", encoding="utf-8") as f:
                    f.write(rec.to_line() + "\n")
            except OSError as e:
                # 落盘失败不阻断：记录仍在内存队列，尽最大努力投递
                logger.error(f"outbox 落盘失败（仅在内存中保留）: {e}")

            self._writes_since_compact += 1
            if self._writes_since_compact >= 500:
                self._compact_locked()
            return rec

    def _evict_locked(self) -> None:
        """丢弃一条记录：优先非关键，其次最旧。"""
        for idx, rec in enumerate(self._records):
            if not rec.critical:
                del self._records[idx]
                self._dropped_total += 1
                logger.warning(
                    f"outbox 超限，丢弃非关键事件 type={rec.type} seq={rec.seq}"
                )
                return
        if self._records:
            rec = self._records.popleft()
            self._dropped_total += 1
            logger.warning(
                f"outbox 超限且全为关键事件，丢弃最旧 type={rec.type} seq={rec.seq}"
            )

    def pending(self, since_seq: int | None = None) -> list[OutboxRecord]:
        """返回 seq 大于指定水位的待发记录（默认取 last_acked_seq）。"""
        base = self._last_acked_seq if since_seq is None else since_seq
        with self._lock:
            return [r for r in self._records if r.seq > base]

    def ack(self, seq: int) -> None:
        """确认已收到 seq（含）之前的所有记录并清理。"""
        with self._lock:
            if seq <= self._last_acked_seq:
                return
            self._last_acked_seq = seq
            while self._records and self._records[0].seq <= seq:
                self._records.popleft()
            self._save_state()
            self._compact_locked()

    def purge_expired(self) -> int:
        """清理过期记录，返回清理条数。"""
        with self._lock:
            before = len(self._records)
            self._records = deque(r for r in self._records if not self._is_expired(r))
            removed = before - len(self._records)
            if removed:
                logger.info(f"outbox 清理过期记录 {removed} 条")
                self._compact_locked()
            return removed

    # ---------------- 状态查询 ----------------

    def _is_expired(self, rec: OutboxRecord) -> bool:
        try:
            ts = datetime.fromisoformat(rec.ts)
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts > self._ttl

    @property
    def last_acked_seq(self) -> int:
        with self._lock:
            return self._last_acked_seq

    @property
    def next_seq(self) -> int:
        with self._lock:
            return self._next_seq

    def depth(self) -> int:
        """当前积压深度（未确认记录数）。"""
        with self._lock:
            return len(self._records)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._records)
            return {
                "depth": total,
                "critical_depth": sum(1 for r in self._records if r.critical),
                "last_acked_seq": self._last_acked_seq,
                "next_seq": self._next_seq,
                "max_records": self._max_records,
                "dropped_total": self._dropped_total,
            }
