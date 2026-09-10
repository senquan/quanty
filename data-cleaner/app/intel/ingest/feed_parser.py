"""RSS 2.0 / Atom 解析器（stdlib xml.etree，不依赖 feedparser）

移植自 Vibe-Research sources/rss.py 的 parse_feed 思路并增强：
- content:encoded 全文提取（不少源全文在此字段，description 只有摘要）
- guid / author 提取
- published 保留 datetime（不落格式化字符串，供 available_at 计算）

与 Vibe 的差异（设计文档结论）：不做 recent_days 截断——我们要建作者历史画像，
全量入库，过滤只用于"是否送 LLM"（P1 的事），不用于"是否入库"。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}


def _text(el, *paths) -> str:
    """按候选路径取第一个非空文本；Atom <link href=.../> 无文本时取 href"""
    for p in paths:
        x = el.find(p, _NS)
        if x is not None:
            t = (x.text or "").strip()
            if not t and x.get("href"):
                return x.get("href")
            if t:
                return t
    return ""


def parse_date(s: str) -> datetime | None:
    """RFC822 / ISO8601 日期解析，无 tz 时按 UTC"""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def strip_html(s: str) -> str:
    """剥 HTML 标签（轻量，供摘要与 redline 匹配；正文规范化见 normalize/text.py）"""
    return re.sub(r"<[^>]+>", "", s or "")


def parse_feed(content: bytes, limit: int = 50) -> list[dict]:
    """RSS 2.0 / Atom → [{guid, title, link, published, content_html, summary, author}]

    整份 XML 解析失败返回 []（坏 feed 视为空源，不中断）；
    无 title 且无 link 的条目丢弃；published 为 datetime 或 None。
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        if len(items) >= limit:
            break
        title = _text(it, "title", "atom:title")
        link = _text(it, "link", "atom:link[@rel='alternate']", "atom:link", "guid")
        guid = _text(it, "guid", "atom:id") or link
        pub = parse_date(_text(it, "pubDate", "atom:published", "atom:updated", "dc:date"))
        # content:encoded 优先（全文常在此），其次 description / atom:content
        body = _text(it, "content:encoded") or _text(it, "description", "atom:content", "atom:summary")
        author = _text(it, "author", "atom:author/atom:name", "dc:creator")
        if not (title or link):
            continue
        items.append({
            "guid": guid,
            "title": title.strip(),
            "link": link,
            "published": pub,
            "content_html": body,
            "summary": strip_html(body)[:300],
            "author": author,
        })
    return items
