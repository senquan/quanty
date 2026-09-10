"""正文规范化：HTML → 纯文本 + canonical_url 规范化

- normalize_text: 剥标签、实体反转、压缩空白，供 simhash / 预筛 / LLM 使用
- canonical_url:  去跟踪参数（utm_* 等）、去 fragment、末尾斜杠归一——同文变体 URL 归并
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 块级标签：边界处换成空格，防段落粘连（</p><p> 直接删会把两段并成一段）
_BLOCK_TAG_RE = re.compile(
    r"</?(?:div|p|br|li|ul|ol|tr|td|th|table|h[1-6]|section|article|blockquote"
    r"|figure|figcaption|header|footer|aside|nav|dl|dt|dd|pre|hr)[^>]*>",
    re.I,
)
# 行内标签：直接删除，防英文单词碎裂（<b>stop</b> 剥成 "stop" 而非 "s t o p"）
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 常见跟踪参数：同文不同参数视为同文（补充 utm_* 前缀规则）
# t 是新闻站常见缓存破坏时间戳参数（东方财富等）
_TRACKING_PARAMS = {
    "spm", "from", "ref", "source", "timestamp", "_t", "t", "share_token", "scene", "via",
}


def normalize_text(content_html: str) -> str:
    """HTML → 规范化纯文本：块级标签换空格 → 行内标签删除 → 实体反转 → 空白压缩"""
    if not content_html:
        return ""
    text = _BLOCK_TAG_RE.sub(" ", content_html)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def canonical_url(url: str) -> str:
    """URL 规范化：小写 scheme/host、剥 utm_* 与跟踪参数、去 fragment、去末尾斜杠

    规范化是确定性的：同一篇文章的分享变体（带不同 utm）映射到同一 canonical。
    空 url 返回空串。
    """
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        # urlsplit 会把 netloc 里的 '[' 当成 IPv6 字面量起头，未闭合就抛
        # "Invalid IPv6 URL"。本地文件路径极易踩中：微信导出的文件名常带
        # 时间戳方括号，如 "[2025-07-01-1922]红利投资6月回顾及7月展望.html"，
        # 拼成 file://C:\...\[2025-...]... 后即炸（实测 226 篇全解析为 0 条，
        # 异常还被上层 except 吞掉，表现为"上传成功但一篇没进库"）。
        # 规范化是尽力而为，解析不了就原样返回，绝不让整篇文档消失。
        return url
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    qs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_PARAMS)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(qs), ""))
