"""正文规范化：HTML → 纯文本 + canonical_url 规范化

- normalize_text: 剥标签、**丢 markdown 链接 URL**、实体反转、压缩空白，
  供 simhash / 预筛 / LLM 使用
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

# markdown 图片/链接：![](url) / [text](url) —— 保留可见文本，**丢弃 URL**。
# 微信导出的 .md 会把文章配图保留为 markdown 语法，而同一篇的 .html 副本里这些图
# 在 <img> 标签内被整体剥掉。若不丢 URL，两副本会因 6 个 mmbiz 长 URL（每个切成
# 几十个 shingle）产生 9 位汉明距离，超出默认阈值 3 → 转载识别漏判。实测
# doc 8686(.html) vs 8687(.md)：丢 URL 前距离 9（漏），丢后应回到阈值内。
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# 兜底：非 markdown 语法残留的裸 URL / 裸域名路径（如 "(https://mmbiz.qpic.cn/...)"）
_BARE_URL_RE = re.compile(r"<?https?://[^\s)\]]+>?", re.I)
# 裸域名路径，如 mmbiz.qpic.cn/a/b/640?wx_fmt=png
_BARE_DOMAIN_RE = re.compile(
    r"\b[\w.-]+\.(?:cn|com|net|org|io|co|me|tv|xyz)(?:/[\w./%?=&:@+-]*)?",
    re.I,
)
# 微信 HTML 属性残渣：单跑出来会把噪声切成 shingle（__biz= / wx_fmt= 等）
_KV_ATTR_RE = re.compile(r"\b[a-z_]{2,}=[^\s&\])]{4,}", re.I)

# markdown 装饰字符：`#` 标题、`_斜体_`、`**粗体**`、`` `code` ``、`>` 引用、`~` 删除线。
# 微信导出的 .md 里 `_2026年09月05日_ __ _ _ _ _ _` 这类标记会插进字与字之间，
# 把中文 2-gram 切碎（"我|们" 变 "们|_" 之类）。换成空格再压缩。
_MD_MARKUP_RE = re.compile(r"[#*_`>~]+")

# 微信页面样板尾巴：从「预览时标签不可点」起的全是客户端 UI 文案
# （微信扫一扫/取消/允许/分析/视频/小程序/赞/在看/分享/留言/收藏/精选留言…），
# 同一篇文章的 .html 与 .md 副本这段文案措辞不一致，是残余汉明距离的主因。
_WECHAT_BOILERPLATE_RE = re.compile(r"预览时标签不可点.*$", re.S)
# 尾部孤立 UI 词（样板截断后可能残留）
_WECHAT_UI_TAIL_RE = re.compile(
    r"(?:微信扫一扫|知道了?|取消|允许|分享|留言|收藏|听过|精选留言|"
    r"轻点两下取消[赞在看]*|作者头像|图片)[\s，。、：~]+$"
)

_WS_RE = re.compile(r"\s+")

# 常见跟踪参数：同文不同参数视为同文（补充 utm_* 前缀规则）
# t 是新闻站常见缓存破坏时间戳参数（东方财富等）
_TRACKING_PARAMS = {
    "spm", "from", "ref", "source", "timestamp", "_t", "t", "share_token", "scene", "via",
}


def normalize_text(content_html: str) -> str:
    """HTML / markdown / 混合输入 → 规范化纯文本

    顺序要紧：**先丢 markdown 链接的 URL**，再剥 HTML 标签。反过来的话 markdown
    语法里的 `(` `)` 无所谓，但一旦先剥标签，`![alt](url)` 会留下 `![alt](url)`
    原样——URL 仍在文本里，等于没清。
    """
    if not content_html:
        return ""
    text = content_html
    # 1) markdown 图片/链接 → 只留可见文本（丢 URL）
    text = _MD_IMAGE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    # 2) HTML 标签
    text = _BLOCK_TAG_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # 3) markdown 装饰字符（# _ * ` > ~）
    text = _MD_MARKUP_RE.sub(" ", text)
    # 4) 非 markdown 的裸 URL / 裸域名 / 属性残渣（必须在 unescape 之后，
    #    因为 &amp; 反转成 & 才能被 [^\s)\]]+ 正确截断）
    text = _BARE_URL_RE.sub(" ", text)
    text = _BARE_DOMAIN_RE.sub(" ", text)
    text = _KV_ATTR_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    # 5) 微信样板尾巴（同文的 html/md 副本这段 UI 文案措辞不同，是残余距离主因）
    text = _WECHAT_BOILERPLATE_RE.sub("", text).strip()
    prev = None
    while prev != text:  # UI 词可能串联（"... 分享了 留言 收藏"），循环剥到稳定
        prev = text
        text = _WECHAT_UI_TAIL_RE.sub("", text).strip()
    return text


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
