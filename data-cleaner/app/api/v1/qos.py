"""QoS / 状态自检接口（阶段 A）

供主后端 registry 周期性轮询，判断清洗服务是否在线：
- 在线 (online)   : 接口正常返回且依赖可用
- 离线 (offline)  : 网络不通 / 连接拒绝（由调用方判定）
- 降级 (degraded) : 接口返回但 DB 不可达（本服务依赖 PG）

注意：本接口为「开放受监控」，不挂 X-API-Key 依赖，便于监控组件轮询。

改造说明：快照内容已抽到 `app.core.qos.build_qos_snapshot()`，
与 WebSocket `event.status` 推送**共用同一份数据**，避免两条链路口径分叉。
启用 WS 后本接口退化为**兜底探针**（backend 仅轮询未建立长连接的实例）。
"""
from fastapi import APIRouter

from app.core.qos import build_qos_snapshot

router = APIRouter(prefix="/qos", tags=["qos"])


@router.get("")
async def qos():
    return await build_qos_snapshot()
