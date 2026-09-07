"""intel 配置接入：直接复用 dc 的 Settings 单例

intel 不定义独立配置类，所有 INTEL_* 项都在 dc 的 ``app.core.config.Settings`` 中声明，
此处仅做转发，便于业务代码统一从 ``app.intel.core.config`` 取用。
"""
from app.core.config import get_settings, settings

__all__ = ["settings", "get_settings"]
