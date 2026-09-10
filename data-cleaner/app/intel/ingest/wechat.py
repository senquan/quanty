"""微信公众号源（P4-2 半自动实装）

设计文档 §7.1：官方接口 / 搜狗微信均不可行；主路径是**人工投喂**（manual）与
**目录 watch**（本模块）。微信导出文件（HTML/mhtml/纯文本）的解析与 manual 完全一致，
故 WeChatSource 直接复用 ManualSource 的解析能力，仅以 ``source_type='wechat'`` 作溯源区分
（落 intel.sources 后理解层/画像/因子对来源无差别）。

真正的"自动摄取"在 ``service.run_wechat_watch``：监听 ~/intel-inbox/，新文件入库后移入
processed/ 作幂等标记。本类只满足 FeedSource 契约 + 暴露 collect/fetch（目录 watch 也复用 collect）。
"""
from __future__ import annotations

from pathlib import Path

from app.intel.core.logging import get_logger
from app.intel.ingest.base import FetchResult, FeedItem, FeedSource
from app.intel.ingest.manual import ManualSource

logger = get_logger(__name__)


class WeChatSource(FeedSource):
    source_type = "wechat"

    def __init__(self, name: str):
        self.name = name
        # 解析完全复用 manual（微信导出同样是 HTML/mhtml/纯文本）
        self._parser = ManualSource(name)

    def collect(self, target: str | Path) -> list[FeedItem]:
        """解析一个目标（单文件 / 目录）为 FeedItem 列表，委托 ManualSource。"""
        return self._parser.collect(target)

    def fetch(self, url: str, *, limit: int = 50, timeout: float = 15.0) -> FetchResult:
        """满足 FeedSource 契约：target 可为 URL / 文件 / 目录，委托 ManualSource 并标 wechat 名。"""
        return self._parser.fetch(url, limit=limit, timeout=timeout)
