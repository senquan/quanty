"""RSS 源实现：httpx 拉取 + 单源失败隔离

httpx 是 dc 既有依赖（alphafeed 行情源 REST 客户端），零新依赖。
失败返回 FetchResult(ok=False, error=...)，不抛异常——feed_health 据此记 failed。
"""
from __future__ import annotations

import time

import httpx

from app.intel.core.logging import get_logger
from app.intel.ingest.base import FeedItem, FetchResult, FeedSource
from app.intel.ingest.feed_parser import parse_feed

logger = get_logger(__name__)

UA = "Mozilla/5.0 (compatible; lab.Quant-intel/0.1; RSS reader)"
_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"


class RSSSource(FeedSource):
    source_type = "rss"

    def __init__(self, name: str):
        self.name = name

    def fetch(self, url: str, *, limit: int = 50, timeout: float = 15.0) -> FetchResult:
        t0 = time.time()
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                resp = client.get(url, headers={"User-Agent": UA, "Accept": _ACCEPT})
            resp.raise_for_status()
            items = [
                FeedItem(
                    external_id=it["guid"],
                    title=it["title"],
                    url=it["link"],
                    content_html=it["content_html"],
                    published_at=it["published"],
                    author=it["author"],
                    summary=it["summary"],
                )
                for it in parse_feed(resp.content, limit)
            ]
            return FetchResult(
                source_name=self.name, ok=True, items=items,
                latency_ms=round((time.time() - t0) * 1000),
            )
        except Exception as e:  # noqa: BLE001 — 单源失败不拖垮整体，记 error 由 feed_health 呈现
            return FetchResult(
                source_name=self.name, ok=False,
                error=f"{type(e).__name__}: {str(e)[:150]}",
                latency_ms=round((time.time() - t0) * 1000),
            )
