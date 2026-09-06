"""data-cleaner 侧 WebSocket 能力（dc 作为**客户端**连入 backend，1 对多）

设计见 docs/plans/2026-09-04.ws-dc-backend.md

模块职责：
- `protocol`：与 backend **逐字一致**的契约（信封、消息类型、版本协商）
- `outbox`  ：发送侧缓冲（seq 分配、持久化、ack 清理、断线补发）
- `client`  ：连接、指数退避重连、心跳、收发循环
- `events`  ：事件源封装（状态 / 在场 / 流水线进度 / 因子变更）

硬约束：**推送失败仅入 outbox 并告警，绝不阻塞 EOD 主流水线**。

本包刻意不在 __init__ 中导入子模块，避免配置/循环依赖；调用方按需导入。
"""
