"""券商工厂：按 mode 路由到具体适配器"""
from functools import lru_cache

from app.models.trading import MODE_LIVE, MODE_PAPER
from app.services.broker.base import BrokerAdapter
from app.services.broker.mx import MxBroker
from app.services.broker.simulated import SimulatedBroker


def normalize_mode(mode: str | None) -> str:
    """归一化模式字符串，未识别一律按模拟盘处理（安全兜底）。"""
    m = (mode or MODE_PAPER).strip().lower()
    return MODE_LIVE if m == MODE_LIVE else MODE_PAPER


@lru_cache(maxsize=None)
def get_broker(mode: str = MODE_PAPER) -> BrokerAdapter:
    mode = normalize_mode(mode)
    if mode == MODE_LIVE:
        return MxBroker()
    return SimulatedBroker()


def describe_modes() -> list[dict]:
    """各模式的可用性，供 /trading/mode 与前端模式切换使用"""
    out = []
    for mode in (MODE_PAPER, MODE_LIVE):
        broker = get_broker(mode)
        try:
            ready, msg = broker.is_ready()
        except Exception as e:  # noqa: BLE001
            ready, msg = False, f"适配器异常: {e}"
        out.append(
            {
                "mode": mode,
                "broker": broker.broker_code,
                "ready": ready,
                "message": msg,
            }
        )
    return out
