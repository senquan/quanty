"""intel 日志接入：复用 dc 的结构化日志（JsonFormatter / setup_logging）"""
from app.core.logging import get_logger, setup_logging

__all__ = ["get_logger", "setup_logging"]
