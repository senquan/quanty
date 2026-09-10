"""RSSHub 可选源（P4-3 实装）：第三方聚合，默认 disabled，health 标 degraded

设计文档 §7.1：RSSHub 自建/第三方实例能把微信公众号等聚合为 RSS，但**稳定性与合规性不保证**，
故定位为**可选、默认关闭**的备选路径。与 RSSSource 解析完全一致（复用 feed_parser），
差异只在两点：
  1. url 形如 ``rsshub://<route>`` 时按 settings.INTEL_RSSHUB_BASE_URL 解析为真实 feed URL；
     未配置 base URL 时 fetch 直接返回 ok=False（不假装可用）。
  2. 入库成功时 feed_health 记 **degraded**（而非 ok）——第三方中继，不假装稳。

仅当用户显式 enable 一个 rsshub 源时才会被 run_rsshub_ingest 拉取。
"""
from __future__ import annotations

import time

import httpx

from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.ingest.base import FeedItem, FetchResult, FeedSource
from app.intel.ingest.feed_parser import parse_feed

logger = get_logger(__name__)

UA = "Mozilla/5.0 (compatible; lab.Quant-intel/0.1; RSSHub reader)"
_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"


def resolve_rsshub_url(url: str) -> str:
    """``rsshub://<route>`` → ``{INTEL_RSSHUB_BASE_URL}/<route>``；非 rsshub:// 原样返回。

    未配置 base URL 时抛出 ValueError（由 fetch 捕获为 ok=False）。
    """
    if url.startswith("rsshub://"):
        base = settings.INTEL_RSSHUB_BASE_URL.rstrip("/")
        if not base:
            raise ValueError("未配置 INTEL_RSSHUB_BASE_URL，无法解析 rsshub:// 源")
        route = url[len("rsshub://"):].lstrip("/")
        return f"{base}/{route}"
    return url


class RSSHubSource(FeedSource):
    source_type = "rsshub"

    def __init__(self, name: str):
        self.name = name

    def fetch(self, url: str, *, limit: int = 50, timeout: float = 15.0) -> FetchResult:
        t0 = time.time()
        try:
            real_url = resolve_rsshub_url(url)
        except ValueError as e:
            return FetchResult(
                source_name=self.name, ok=False, error=str(e),
                latency_ms=round((time.time() - t0) * 1000),
            )
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                resp = client.get(real_url, headers={"User-Agent": UA, "Accept": _ACCEPT})
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
