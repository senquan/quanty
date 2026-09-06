"""backend 侧 WebSocket 能力（backend 作为**服务端**，接收 N 个 dc 实例连入，1 对多）

设计见 docs/plans/2026-09-04.ws-dc-backend.md

模块职责：
- `protocol`：与 data-cleaner **逐字一致**的契约（信封、消息类型、版本协商）
- `registry`：进程内连接注册表（instance_id → 连接 / last_seq / ack / 在线状态）
- `server`  ：WS 端点 /ws/dc、握手鉴权（X-Internal-Token）、单帧与连接数限制
- `handlers`：事件分发 + 幂等去重 + 各类型处理入口

注意：连接注册表为**进程内**结构，仅适用于 backend 单实例部署（本系统当前形态）。
若未来 backend 多副本，需引入消息中间件做 fan-out（见设计文档 §11）。

本包刻意不在 __init__ 中导入子模块，避免循环依赖；调用方按需导入。
"""
