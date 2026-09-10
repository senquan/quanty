"""L0 摄取源契约

与 dc 行情源（app.ingestion.base.BaseSource → RawBar DataFrame）不同，
intel 摄取的是**文档**：一个源一次拉取产生 0..N 篇 FeedItem。

契约要点（对齐 Vibe-Research sources/rss.py 的工程纪律）：
- 单源失败**不抛异常**：返回 FetchResult(ok=False, error=...)，由 feed_health 呈现，不拖垮整轮
- content_html 保留原始 HTML（原文不可变），剥标签发生在 normalize 层
- published_at 保留 datetime（不格式化），供 available_at = max(published, ingested) 防前视
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FeedItem:
    """单篇文档条目（规范化前的原始结构）"""

    external_id: str                # feed guid（同源内幂等键）
    title: str
    url: str                        # 文章链接
    content_html: str               # 原始内容（content:encoded / description，含 HTML）
    published_at: datetime | None
    author: str = ""
    summary: str = ""               # 剥标签摘要（redline 快速匹配 / 列表展示用）


@dataclass
class FetchResult:
    """一次源拉取的结果（失败也返回：ok=False + error，单源失败不拖垮整体）"""

    source_name: str
    ok: bool
    items: list[FeedItem] = field(default_factory=list)
    error: str = ""
    latency_ms: int = 0


class FeedSource(ABC):
    """文档摄取源契约：rss / web / manual / wechat 均实现此接口"""

    source_type: str                # registry 登记的源类型
    name: str

    @abstractmethod
    def fetch(self, url: str, *, limit: int = 50, timeout: float = 15.0) -> FetchResult:
        """拉取一个源。失败时返回 ok=False 且 error 非空，**不抛异常**。"""
        ...
