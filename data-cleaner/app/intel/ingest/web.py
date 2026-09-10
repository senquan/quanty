"""单页/列表抓取源（后续实现）：无 RSS 的站点（如部分财经快讯页）

P0-6 冻结接口：抛 NotImplementedError；源类型已登记。
"""
from app.intel.ingest.base import FetchResult, FeedSource


class WebSource(FeedSource):
    source_type = "web"

    def __init__(self, name: str):
        self.name = name

    def fetch(self, url: str, *, limit: int = 50, timeout: float = 15.0) -> FetchResult:
        raise NotImplementedError("web 源为后续实现：单页/列表抓取（无 RSS 站点）")
