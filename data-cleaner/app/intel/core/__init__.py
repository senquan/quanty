"""intel.core —— 复用 dc 基础设施的接入层

intel 模块不直接 import dc 内部实现细节，统一经本包取用：
  - config:   服务配置（含 INTEL_*）
  - logging:  结构化日志
  - db:       异步 SQLAlchemy 引擎 / session（与 dc 共用同一 PG 实例的 intel schema）

这样后续 intel 业务代码只依赖 ``app.intel.core.*``，dc 内部重构不波及 intel。
"""
from app.intel.core import config, db, logging  # noqa: F401
