"""源类型注册表：rss / web / manual / wechat

P0 只实现 rss；web / manual / wechat 为接口占位（P0-6 冻结接口）。
service 按 intel.sources.source_type 经此分发；新增源类型在此登记。
"""
from __future__ import annotations

from app.intel.ingest.base import FeedSource
from app.intel.ingest.manual import ManualSource
from app.intel.ingest.rss import RSSSource
from app.intel.ingest.rsshub import RSSHubSource
from app.intel.ingest.web import WebSource
from app.intel.ingest.wechat import WeChatSource

# 源类型 → 实现类（keys 即 sources.source_type 枚举约定）
SOURCE_TYPES: dict[str, type[FeedSource]] = {
    "rss": RSSSource,
    "web": WebSource,
    "manual": ManualSource,
    "wechat": WeChatSource,
    "rsshub": RSSHubSource,
}


def create_source(source_type: str, name: str) -> FeedSource:
    cls = SOURCE_TYPES.get(source_type)
    if cls is None:
        raise ValueError(f"未知源类型: {source_type!r}；可选: {sorted(SOURCE_TYPES)}")
    return cls(name)
