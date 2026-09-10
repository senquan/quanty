"""人工投喂源（P4-1 实装）：URL 清单 / 导出文件 / 目录 → 解析入库

主路径入口：公众号历史文章、用户剪藏的网页、手动整理的导出文件。
设计文档 §7.1：微信公众号官方接口/搜狗均不可行，主路径是人工投喂。

输入形态（``collect`` 自动判别）：
  - ``.txt`` / ``.csv``（每行/每列一个 URL）          → URL 清单，逐条 httpx 抓取解析
  - ``.html`` / ``.htm`` / ``.mhtml``（浏览器导出/剪藏） → bs4 解析 标题/作者/时间/正文
  - ``.txt`` / ``.md``（纯文本文章）                   → 以文件名作标题，整篇作正文
  - 目录                                               → 递归按扩展名分发上述规则

解析结果统一为 ``FeedItem``，之后复用 ``service._ingest_one_source`` 走与 RSS
完全相同的规范化→去重→转载识别→落盘→入库链路；理解层/画像/因子对来源无差别。
"""
from __future__ import annotations

import csv
import email
import html
import io
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.intel.core.logging import get_logger
from app.intel.ingest.base import FeedItem, FetchResult, FeedSource
from app.intel.ingest.feed_parser import parse_date
from app.intel.normalize import text as norm_text

logger = get_logger(__name__)

UA = "Mozilla/5.0 (compatible; lab.Quant-intel/0.1; manual ingest)"
_ACCEPT = "text/html, application/xhtml+xml, application/xml;q=0.9, */*;q=0.8"

# 扩展名 → 处置方式
_URL_MANIFEST_EXT = {".txt", ".csv"}
_EXPORTED_HTML_EXT = {".html", ".htm", ".mhtml"}
_PLAIN_EXT = {".md", ".markdown"}
_SUPPORTED_EXT = _URL_MANIFEST_EXT | _EXPORTED_HTML_EXT | _PLAIN_EXT


# --------------------------------------------------------------------------
# 模块级辅助：URL 判定 / 清单解析 / mhtml 抽取
# --------------------------------------------------------------------------
# 文件名里的日期："[2025-07-01-1922]标题.html" / "2025-07-01 标题.md" / "标题_20250701.html"
_FNAME_DATE_RE = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")


def _date_from_filename(name: str) -> datetime | None:
    """从文件名提取发布日期（解析不出返回 None）"""
    m = _FNAME_DATE_RE.search(name or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:  # 2025-13-45 之类
        return None


def _file_uri(p: Path) -> str:
    """本地文件的 file:// URI（百分号编码特殊字符）

    必须走 ``Path.as_uri()`` 而不是 f"file://{path}"：后者对方括号、空格、中文
    不做转义，``urlsplit`` 会把 netloc 里的 '[' 当 IPv6 字面量解析并抛
    ``ValueError: Invalid IPv6 URL``。微信导出文件名常带 ``[2025-07-01-1922]``
    这类前缀，实测 226 篇 .html 因此全部解析为 0 条（异常被上层吞掉，前端却报
    "导入成功"）。as_uri() 输出 ``file:///C:/.../%5B2025-...%5D...``，可被正常解析。
    """
    try:
        return p.resolve().as_uri()
    except ValueError:  # 相对路径等极端情况：退化为不带 scheme 的标记串
        return f"file:///{p.resolve().as_posix()}"


def _looks_like_url(s: str) -> bool:
    s = str(s).strip()
    if not s:
        return False
    try:
        pr = urlparse(s)
    except ValueError:  # 同上：畸形 netloc（含未闭合 '['）不该让判定函数抛异常
        return False
    return pr.scheme in ("http", "https") and bool(pr.netloc)


def _txt_urls(text: str) -> list[str]:
    """每行一个 URL 的 .txt：返回 URL 列表；若含非 URL 行则视为普通文章（返回 []）"""
    lines: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    if not lines:
        return []
    if all(_looks_like_url(ln) for ln in lines):
        return lines
    return []


def _csv_urls(text: str) -> list[str]:
    """CSV 链接清单：自动识别 url/link/链接 列，无表头时取第一列"""
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]
    url_col = 0
    for i, h in enumerate(header):
        if "url" in h or "link" in h or "链接" in h:
            url_col = i
            break
    has_header = any("url" in h or "link" in h or "链接" in h for h in header)
    start = 1 if has_header else 0
    out: list[str] = []
    for r in rows[start:]:
        if url_col < len(r):
            u = r[url_col].strip()
            if u:
                out.append(u)
    return out


def _extract_mhtml_html(raw: bytes) -> str:
    """从 MHTML（multipart/related）抽出正文 HTML 部，失败回退整段解码"""
    try:
        msg = email.message_from_bytes(raw)
    except Exception:  # noqa: BLE001
        return raw.decode("utf-8", "replace")
    if not msg.is_multipart():
        return raw.decode("utf-8", "replace")
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    for part in msg.walk():
        if part.get_content_type().startswith("text/"):
            payload = part.get_payload(decode=True)
            if payload is not None:
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return raw.decode("utf-8", "replace")


class ManualSource(FeedSource):
    source_type = "manual"

    def __init__(self, name: str):
        self.name = name

    # ---- 满足 FeedSource 契约（manual 主要经 service.run_manual_ingest 调用） ----
    def fetch(self, url: str, *, limit: int = 50, timeout: float = 15.0) -> FetchResult:
        t0 = time.time()
        if _looks_like_url(url):
            # 单条 URL 投喂：失败即源失败（ok=False），由调用方决定降级
            try:
                items = [self._fetch_url(url, timeout=timeout)]
            except Exception as e:  # noqa: BLE001
                return FetchResult(
                    source_name=self.name, ok=False,
                    error=f"{type(e).__name__}: {str(e)[:150]}",
                    latency_ms=round((time.time() - t0) * 1000),
                )
            if limit:
                items = items[:limit]
            return FetchResult(
                source_name=self.name, ok=True, items=items,
                latency_ms=round((time.time() - t0) * 1000),
            )
        # 文件/目录目标：整体不可解析（极少见）才报 ok=False；逐文件错误在 collect 内吞掉
        try:
            items = self.collect(url)
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                source_name=self.name, ok=False,
                error=f"{type(e).__name__}: {str(e)[:150]}",
                latency_ms=round((time.time() - t0) * 1000),
            )
        if limit:
            items = items[:limit]
        return FetchResult(
            source_name=self.name, ok=True, items=items,
            latency_ms=round((time.time() - t0) * 1000),
        )

    # ---- 核心分发 ----
    def collect(self, target: str | Path) -> list[FeedItem]:
        """target 可以是：URL / URL 清单文件(.txt/.csv) / 导出文件 / 目录。

        返回解析出的 FeedItem 列表（可能为空）。任何单篇/单文件错误被记 warning 并跳过，
        不中断整体（与 RSS 单源失败隔离一致）。
        """
        target = str(target)
        if _looks_like_url(target):
            # 单条 URL：直接抓取（异常上抛，由 fetch 捕获为 ok=False；文件/目录分支才吞逐条错误）
            return [self._fetch_url(target)]
        p = Path(target)
        if p.is_dir():
            out: list[FeedItem] = []
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in _SUPPORTED_EXT:
                    out.extend(self._parse_file_multi(f))
            return out
        if not p.exists():
            logger.warning(f"manual 目标不存在: {target}")
            return []
        return self._parse_file_multi(p)

    def _parse_file_multi(self, p: Path) -> list[FeedItem]:
        ext = p.suffix.lower()
        try:
            if ext in _EXPORTED_HTML_EXT:
                it = self._parse_exported(p)
                return [it] if it else []
            if ext in _PLAIN_EXT:
                it = self._parse_plain(p)
                return [it] if it else []
            if ext in _URL_MANIFEST_EXT:
                return self._parse_list_or_plain(p)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"manual 解析失败 {p}: {type(e).__name__}: {e}")
            return []
        # .txt 兜底（理论上已在 _URL_MANIFEST_EXT 覆盖）
        if ext == ".txt":
            return self._parse_list_or_plain(p)
        logger.warning(f"manual 跳过不支持的文件类型: {p}")
        return []

    # ---- 清单 / 纯文本 ----
    def _parse_list_or_plain(self, p: Path) -> list[FeedItem]:
        text = p.read_text(encoding="utf-8", errors="replace")
        ext = p.suffix.lower()
        urls = _csv_urls(text) if ext == ".csv" else _txt_urls(text)
        if urls:
            items: list[FeedItem] = []
            for u in urls:
                try:
                    items.append(self._fetch_url(u))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"manual 抓取 URL 失败 {u}: {e}")
                    continue
            return items
        # 非清单 → 整篇作为一篇纯文本文章
        it = self._parse_plain_from_text(
            p.stem, text, external_id=_file_uri(p)
        )
        return [it] if it else []

    def _parse_plain(self, p: Path) -> FeedItem | None:
        text = p.read_text(encoding="utf-8", errors="replace")
        return self._parse_plain_from_text(
            p.stem, text, external_id=_file_uri(p)
        )

    def _parse_plain_from_text(
        self, title: str, text: str, external_id: str
    ) -> FeedItem | None:
        text = text.strip()
        if not text:
            return None
        # 纯文本：HTML 转义后作为 content_html（normalize_text 剥标签还原为原文）
        content_html = html.escape(text, quote=False)
        return FeedItem(
            external_id=external_id,
            title=title or "未命名",
            url=external_id,
            content_html=content_html,
            published_at=None,
            author="",
            summary=text[:300],
        )

    # ---- 导出网页（HTML / MHTML） ----
    def _parse_exported(self, p: Path) -> FeedItem | None:
        raw = p.read_bytes()
        if p.suffix.lower() == ".mhtml":
            page = _extract_mhtml_html(raw)
        else:
            page = raw.decode("utf-8", "replace")
        item = self._parse_html_bytes(page, _file_uri(p))
        if item is not None and item.published_at is None:
            # 再兜一层：导出工具常把日期写进文件名，如
            # "[2025-07-01-1922]红利投资6月回顾及7月展望.html"。
            # 拿不到真实发布日期时，用文件名日期远好于用入库时间（后者会让
            # 整批历史文章挤在"今天"，既失真又让防前视检查失去意义）。
            dt = _date_from_filename(p.name)
            if dt:
                item.published_at = dt
        return item

    def _fetch_url(self, url: str, timeout: float = 15.0) -> FeedItem:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers={"User-Agent": UA, "Accept": _ACCEPT})
        resp.raise_for_status()
        return self._parse_html_bytes(resp.text, url)

    def _parse_html_bytes(self, page: str, url: str) -> FeedItem:
        soup = BeautifulSoup(page, "html.parser")
        title = self._extract_title(soup) or (
            Path(url).stem if url.startswith("file://") else (url or "未命名")
        )
        author = self._extract_author(soup)
        pub = self._extract_published(soup)
        body = self._extract_body(soup) or ""
        summary = re.sub(r"<[^>]+>", "", body)[:300].strip()
        return FeedItem(
            external_id=norm_text.canonical_url(url) or url,
            title=title,
            url=url,
            content_html=body,
            published_at=pub,
            author=author,
            summary=summary,
        )

    # ---- HTML 元数据抽取 ----
    def _extract_title(self, soup: BeautifulSoup) -> str:
        for prop in ("og:title", "twitter:title"):
            m = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            if m and m.get("content"):
                return m["content"].strip()
        if soup.title:
            t = soup.title.get_text(strip=True)
            if t:
                return t
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return ""

    def _extract_author(self, soup: BeautifulSoup) -> str:
        for prop in ("article:author", "og:article:author"):
            m = soup.find("meta", attrs={"property": prop})
            if m and m.get("content"):
                return m["content"].strip()
        m = soup.find("meta", attrs={"name": "author"})
        if m and m.get("content"):
            return m["content"].strip()
        el = soup.find(attrs={"itemprop": "author"})
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return txt
        return ""

    def _extract_published(self, soup: BeautifulSoup):
        for prop in (
            "article:published_time",
            "article:published",
            "article:publication_time",
            "og:published_time",
            "og:updated_time",
        ):
            m = soup.find("meta", attrs={"property": prop})
            if m and m.get("content"):
                dt = self._parse_published(m["content"].strip())
                if dt:
                    return dt
        m = soup.find("meta", attrs={"name": "publish_date"})
        if m and m.get("content"):
            dt = self._parse_published(m["content"].strip())
            if dt:
                return dt
        el = soup.find(attrs={"itemprop": "datePublished"})
        if el and el.get("content"):
            dt = self._parse_published(el["content"].strip())
            if dt:
                return dt
        t = soup.find("time")
        if t and t.get("datetime"):
            dt = self._parse_published(t["datetime"].strip())
            if dt:
                return dt
        # 微信导出页：发布时间在正文 <em id="publish_time">2025年07月01日 19:22</em>
        # 里，没有任何 meta / <time datetime>，不认它就只能退化成入库时间
        # （226 篇横跨 14 个月的文章会被压成"同一天"，因子时间维度失效）。
        for attr in ("publish_time", "publish-time", "publishTime", "publish_time_box"):
            el = soup.find(id=attr)
            if el:
                dt = self._parse_published(el.get_text(" ", strip=True))
                if dt:
                    return dt
        return None

    def _extract_body(self, soup: BeautifulSoup) -> str:
        for tag in ("article", "main"):
            el = soup.find(tag)
            if el:
                for sel in ("script", "style", "noscript", "nav", "header", "footer", "aside"):
                    for x in el.find_all(sel):
                        x.decompose()
                return el.decode_contents()
        body = soup.body or soup
        for sel in ("script", "style", "noscript", "nav", "header", "footer", "aside"):
            for x in body.find_all(sel):
                x.decompose()
        return body.decode_contents()

    @staticmethod
    def _parse_published(s: str):
        if not s:
            return None
        s = s.strip()
        # Unix 时间戳（秒 / 毫秒）
        if s.isdigit() and len(s) in (10, 13):
            ts = int(s[:10]) if len(s) == 13 else int(s)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        # 2024年10月30日 09:30
        m = re.match(
            r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[ ]*(\d{0,2}):?(\d{0,2})?\s*$", s
        )
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            h = int(m.group(4)) if m.group(4) else 0
            mi = int(m.group(5)) if m.group(5) else 0
            try:
                return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
            except ValueError:
                pass
        # 2024-10-30 09:30[:ss]（无时区 → 按 UTC；带偏移的 ISO 必须落到下方 parse_date 处理）
        m2 = re.match(
            r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?\s*$", s
        )
        if m2:
            y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            h, mi = int(m2.group(4)), int(m2.group(5))
            sec = int(m2.group(6)) if m2.group(6) else 0
            try:
                return datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc)
            except ValueError:
                pass
        # ISO8601 / RFC822 兜底（feed_parser 处理）；统一转 UTC 与 TIMESTAMPTZ 存储对齐
        dt = parse_date(s)
        return dt.astimezone(timezone.utc) if dt else None
