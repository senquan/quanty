"""P4-1 人工投喂单测：解析（清单/导出网页/mhtml/纯文本/目录）+ 真实库入库集成

覆盖 plan §6 P4-1：URL 清单(.txt/.csv) / 导出文件(HTML/mhtml/纯文本) / 目录 → 解析入库。
解析逻辑走纯单测（不触网）；入库走真实 PG（与 RSS 同链路），测试后清理不污染。
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.intel.ingest import manual as M
from app.intel.ingest.base import FeedItem

MHTML_FIXTURE = """From: saved
Subject: test
MIME-Version: 1.0
Content-Type: multipart/related; boundary="BOUND"

--BOUND
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: 7bit

<html><head><title>微信文章标题</title><meta property="article:author" content="小红"></head><body><article><p>这是mhtml正文内容，贵州茅台三季度增长。</p></article></body></html>

--BOUND
Content-Type: image/png
Content-Location: img1

fakeimagedata
--BOUND--
"""

HTML_FIXTURE = """<!DOCTYPE html>
<html><head>
<meta property="og:title" content="贵州茅台三季报点评">
<meta property="article:author" content="老朱投研">
<meta property="article:published_time" content="2024-10-30T09:30:00+08:00">
<title>旧标题应被og:title覆盖</title>
</head><body>
<nav>顶部导航不应进正文</nav>
<article><h1>贵州茅台三季报点评</h1><p>公司三季度营收同比增长15%，净利润增长18%。</p><p>高端白酒需求稳定。</p></article>
<script>var x=1; doNotLeak();</script>
</body></html>"""


# --------------------------------------------------------------------------
# 1. 清单解析（离线：monkeypatch _fetch_url）
# --------------------------------------------------------------------------
def test_txt_url_manifest(tmp_path, monkeypatch):
    f = tmp_path / "urls.txt"
    f.write_text("# 注释行\nhttps://a.com/p1\nhttps://b.com/p2\n\n", encoding="utf-8")

    calls = []

    def fake(self, url, timeout=15.0):
        calls.append(url)
        return FeedItem(
            external_id=url, title=f"T-{url[-2:]}", url=url,
            content_html="<p>body</p>", published_at=None, author="",
            summary="body",
        )

    monkeypatch.setattr(M.ManualSource, "_fetch_url", fake)
    items = M.ManualSource("t").collect(f)
    assert len(items) == 2, items
    assert calls == ["https://a.com/p1", "https://b.com/p2"]
    assert items[0].title == "T-p1"


def test_csv_url_manifest_with_header(tmp_path, monkeypatch):
    f = tmp_path / "urls.csv"
    f.write_text("title,url\n甲,https://x.com/a\n乙,https://x.com/b\n", encoding="utf-8")

    got = []

    def fake(self, url, timeout=15.0):
        got.append(url)
        return FeedItem(external_id=url, title="x", url=url, content_html="<p>x</p>",
                        published_at=None, author="", summary="x")

    monkeypatch.setattr(M.ManualSource, "_fetch_url", fake)
    items = M.ManualSource("t").collect(f)
    assert len(items) == 2
    assert got == ["https://x.com/a", "https://x.com/b"]


def test_txt_non_url_is_treated_as_plain_article(tmp_path, monkeypatch):
    f = tmp_path / "note.txt"
    f.write_text("今天调研了宁德时代，产能扩张超预期。", encoding="utf-8")
    fetched = []
    monkeypatch.setattr(M.ManualSource, "_fetch_url",
                        lambda u, **k: fetched.append(u) or _raise())
    items = M.ManualSource("t").collect(f)
    assert fetched == []  # 不应触发任何 URL 抓取
    assert len(items) == 1
    assert "宁德时代" in items[0].content_html


def _raise():
    raise AssertionError("should not be called")


# --------------------------------------------------------------------------
# 2. 导出网页解析
# --------------------------------------------------------------------------
def test_parse_exported_html(tmp_path):
    f = tmp_path / "article.html"
    f.write_text(HTML_FIXTURE, encoding="utf-8")
    items = M.ManualSource("t").collect(f)
    assert len(items) == 1
    it = items[0]
    assert it.title == "贵州茅台三季报点评"          # og:title 覆盖 <title>
    assert it.author == "老朱投研"
    assert it.published_at == datetime(2024, 10, 30, 1, 30, tzinfo=timezone.utc)  # +08→UTC
    assert "营收同比增长15%" in it.content_html
    assert "var x=1" not in it.content_html          # script 已剥离
    assert "顶部导航" not in it.content_html          # nav 已剥离


def test_parse_exported_mhtml(tmp_path):
    f = tmp_path / "article.mhtml"
    f.write_bytes(MHTML_FIXTURE.encode("utf-8"))
    items = M.ManualSource("t").collect(f)
    assert len(items) == 1
    it = items[0]
    assert it.title == "微信文章标题"
    assert it.author == "小红"
    assert "mhtml正文内容" in it.content_html


def test_parse_plain_md(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# 我的复盘\n\n锂价见底，天齐锂业值得关注。", encoding="utf-8")
    items = M.ManualSource("t").collect(f)
    assert len(items) == 1
    it = items[0]
    assert it.title == "note"
    assert "天齐锂业" in it.content_html


def test_directory_dispatch(tmp_path):
    (tmp_path / "a.html").write_text(HTML_FIXTURE, encoding="utf-8")
    (tmp_path / "b.md").write_text("正文内容测试", encoding="utf-8")
    (tmp_path / "ignore.log").write_text("skip me", encoding="utf-8")  # 不支持，跳过
    items = M.ManualSource("t").collect(tmp_path)
    titles = {it.title for it in items}
    assert "贵州茅台三季报点评" in titles
    assert len(items) == 2  # .log 被忽略


def test_bad_target_returns_empty():
    items = M.ManualSource("t").collect("/no/such/path/xyz")
    assert items == []


def test_fetch_isolated_on_error(monkeypatch):
    """单条 URL 投喂失败时，fetch 返回 ok=False 的 FetchResult，不抛异常（单源失败隔离）"""

    def boom(self, url, timeout=15.0):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(M.ManualSource, "_fetch_url", boom)
    res = M.ManualSource("t").fetch("https://example.com/article")
    assert res.ok is False
    assert res.error
    assert "simulated network failure" in res.error


# --------------------------------------------------------------------------
# 3. 日期解析鲁棒性
# --------------------------------------------------------------------------
def test_parse_published_variants():
    ms = M.ManualSource._parse_published
    assert ms("2024-10-30T09:30:00+08:00") == datetime(2024, 10, 30, 1, 30, tzinfo=timezone.utc)
    assert ms("2024-10-30 09:30:00").year == 2024
    assert ms("2024年10月30日 09:30").year == 2024
    assert ms("1730259000").year == 2024                       # 秒级时间戳
    assert ms("not-a-date") is None


# --------------------------------------------------------------------------
# 4. 真实库入库集成（与 RSS 同链路；测试后清理）
# --------------------------------------------------------------------------
def test_run_manual_ingest_writes_documents():
    import uuid

    from app.intel import store
    from app.intel.service import run_manual_ingest

    name = f"pytest_manual_{uuid.uuid4().hex[:8]}"
    d = tempfile.mkdtemp(prefix="intel_manual_")
    try:
        (Path(d) / "p1.html").write_text(HTML_FIXTURE, encoding="utf-8")
        stats = run_manual_ingest(d, source_name=name)
        assert stats["new"] == 1, stats
        assert stats["fetched"] == 1

        # 验证落库：documents 行存在、raw_path 文件一致
        sid = stats["source_id"]
        with store.get_engine().connect() as c:
            rows = c.execute(
                store.text(
                    "SELECT id, raw_path, content_hash, title FROM intel.documents "
                    "WHERE source_id = :sid"
                ),
                {"sid": sid},
            ).mappings().all()
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["title"] == "贵州茅台三季报点评"
        raw = Path(row["raw_path"])
        assert raw.exists()
        import hashlib
        sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        assert sha == row["content_hash"], "落盘原文 sha 必须等于 content_hash"

        # 二次投喂同目录应判重（dup），不新增
        stats2 = run_manual_ingest(d, source_name=name)
        assert stats2["new"] == 0 and stats2["dup"] == 1, stats2
    finally:
        # 清理：删文档 + 源
        with store.get_engine().begin() as c:
            c.execute(
                store.text("DELETE FROM intel.documents WHERE source_id = "
                           "(SELECT id FROM intel.sources WHERE url = :u)"),
                {"u": f"manual://{name}"},
            )
            c.execute(
                store.text("DELETE FROM intel.sources WHERE url = :u"),
                {"u": f"manual://{name}"},
            )
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# 回归：文件名带方括号（微信导出 "[2025-07-01-1922]标题.html"）
#
# urlsplit 会把 netloc 里的 '[' 当 IPv6 字面量起头，未闭合即抛
# "Invalid IPv6 URL"。修复前该异常被 _parse_file_multi 的 except 吞掉，
# 表现为"上传 228 篇、前端报成功、库里只进 3 篇"。
# ---------------------------------------------------------------------------
WX_PAGE = """<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>{}</title>
</head><body>
<div id="js_content"><p>贵州茅台与五粮液股息率对比，长期持有收息。</p></div>
<em id="publish_time">2025年07月01日 19:22</em>
</body></html>"""


def test_bracketed_filename_parses_instead_of_silently_dropped():
    """文件名以 [2025-07-01-1922] 开头时必须正常解析（不得静默变 0 条）"""
    d = tempfile.mkdtemp(prefix="bracket_")
    try:
        p = Path(d) / "[2025-07-01-1922]红利投资6月回顾及7月展望.html"
        p.write_text(WX_PAGE.format("红利投资6月回顾及7月展望"), encoding="utf-8")
        items = M.ManualSource("分红养老之路").collect(str(p))
        assert len(items) == 1, f"应解析出 1 条，实际 {len(items)}"
        assert items[0].title == "红利投资6月回顾及7月展望"
        assert "贵州茅台" in items[0].content_html
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_publish_time_read_from_wechat_page():
    """微信导出页时间在 <em id="publish_time">，没有 meta / <time datetime>"""
    d = tempfile.mkdtemp(prefix="pubtime_")
    try:
        p = Path(d) / "wx.html"
        p.write_text(WX_PAGE.format("标题"), encoding="utf-8")
        items = M.ManualSource("s").collect(str(p))
        assert items[0].published_at == datetime(2025, 7, 1, 19, 22, tzinfo=timezone.utc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_published_falls_back_to_filename_date():
    """页面无任何时间信息时，用文件名里的日期（远好于退化成入库时间）"""
    d = tempfile.mkdtemp(prefix="fname_")
    try:
        p = Path(d) / "[2025-07-01-1922]无时间标记.html"
        p.write_text(
            "<html><head><title>无时间</title></head><body><p>正文</p></body></html>",
            encoding="utf-8",
        )
        items = M.ManualSource("s").collect(str(p))
        assert items[0].published_at is not None
        assert items[0].published_at.date().isoformat() == "2025-07-01"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_canonical_url_survives_bracketed_file_uri():
    """canonical_url 对含方括号的 file:// URI 不得抛异常（解析不了就原样返回）"""
    from app.intel.normalize import text as norm_text

    uri = Path(r"C:\tmp\[2025-07-01-1922]标题.html").as_uri()
    assert norm_text.canonical_url(uri) == uri  # as_uri 已转义，可正常规范化

    raw = "file://C:\\tmp\\[2025-07-01-1922]标题.html"  # 未转义的旧式拼法
    assert norm_text.canonical_url(raw) == raw  # 兜底：原样返回，绝不抛


def test_looks_like_url_never_raises():
    """URL 判定是谓词函数，对畸形 netloc 必须返回 False 而不是抛 ValueError"""
    assert M._looks_like_url("https://example.com/a") is True
    assert M._looks_like_url(r"C:\tmp\[2025-07-01]标题.html") is False
    assert M._looks_like_url("") is False


def test_title_unquoted_from_bracketed_file_uri():
    """文件名被 as_uri() 百分号编码后，title 必须还原为人类可读

    这是 id=10445 的真实事故：``_file_uri`` 用 ``as_uri()`` 转义方括号与中文
    （为绕开 urlsplit 的 Invalid IPv6 URL），但标题那条路直接拿编码后的
    ``Path(url).stem`` 当 title 落库 → 前端显示 ``%5B2026-09-09-1030%5D%E6%84%9F...``。
    修了 URL 主体却漏了标题回溯，属于「同一个坑的两面」。
    """
    d = tempfile.mkdtemp(prefix="unquote_")
    try:
        p = Path(d) / "[2026-09-09-1030]感恩长鑫打新收益换两家优质红利.html"
        # 页面无 <title>/og:title → 强制走文件名回溯分支
        p.write_text("<html><body><article><p>正文</p></article></body></html>",
                     encoding="utf-8")
        items = M.ManualSource("分红养老之路").collect(str(p))
        assert len(items) == 1
        assert items[0].title == "[2026-09-09-1030]感恩长鑫打新收益换两家优质红利"
        assert "%" not in items[0].title, "标题里不该再有百分号编码残留"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_readable_stem_handles_all_forms():
    """_readable_stem 对 file:// URI / 编码路径 / Path / 纯文件名都要正确"""
    # 编码过的 file:// URI（as_uri 的真实输出形态）
    uri = Path(r"C:/tmp/[2025-07-01-1922]红利回顾.html").as_uri()
    assert M._readable_stem(uri) == "[2025-07-01-1922]红利回顾"
    # 未编码的 Path 与纯文件名（不该被 unquote 破坏）
    assert M._readable_stem(Path("普通中文标题.md")) == "普通中文标题"
    assert M._readable_stem("plain.txt") == "plain"
    # 含 % 但非编码的标题不应被误伤（unquote 对非法序列原样返回）
    assert M._readable_stem(Path("涨幅50%的股票.txt")) == "涨幅50%的股票"


def test_plain_text_title_unquoted(tmp_path):
    """纯文本投喂（.md/.txt）的标题同样要还原（走了另一条调用点）"""
    p = tmp_path / "[2026-01-02-0800]早报精选.md"
    p.write_text("今日要点：贵州茅台。", encoding="utf-8")
    items = M.ManualSource("s").collect(str(p))
    assert items[0].title == "[2026-01-02-0800]早报精选"
