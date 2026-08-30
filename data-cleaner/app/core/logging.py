"""结构化日志配置

统一字段: ts / level / service / task / symbol_count / duration_ms
输出 JSON 格式，便于日志采集与检索。
"""
import json
import logging
import sys
from datetime import UTC, datetime

SERVICE_NAME = "data-cleaner"


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        log = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 透传业务上下文字段（通过 logger.info(..., extra={...}) 注入）
        for field in ("task", "symbol_count", "duration_ms", "status"):
            value = getattr(record, field, None)
            if value is not None:
                log[field] = value
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        return json.dumps(log, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化全局日志配置（服务启动时调用一次）"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # 压低第三方库噪音
    # - httpx/httpcore：DEBUG 会打印每个请求的连接、TLS、收发细节
    # - redis：新版本客户端对旧版服务端报 MAINT_NOTIFICATIONS 未知子命令（无害）
    for noisy in (
        "uvicorn.access",
        "httpx",
        "httpcore",
        "httpcore.http11",
        "httpcore.connection",
        "urllib3",
        "redis",
        "redis.asyncio",
        "redis.asyncio.connection",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
