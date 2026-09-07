"""市场情报模块（intel）

dc 内的可选子模块，经 ``INTEL_ENABLED`` 开关门控。关闭时本包不会被 import，
重型依赖（LLM SDK 等）不加载，dc 行为与改造前一致（与 dc 既有 ``WS_ENABLED`` 同构）。

模块划分（设计文档 §0/§4）：
  - core/        复用 dc 基础设施的接入层（settings / logger / db）
  - ingest/      L0 摄取层（RSS / web / manual / wechat），P0 起逐步实现
  - api.py       /intel/* REST 路由（占位）
  - tasks.py     调度注册点（占位，由 app.tasks.scheduler.register_jobs 在启用时调用）
"""
from app.core.config import settings


def is_enabled() -> bool:
    """intel 是否启用（与开关同义，供内部断言/日志使用）"""
    return bool(settings.INTEL_ENABLED)
