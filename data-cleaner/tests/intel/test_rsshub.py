"""P4-3 RSSHub 可选源测试

单元：rsshub:// 解析 / RSS 解析（mock httpx）/ 未配置 base URL 报错
集成：run_rsshub_ingest 成功入库后 feed_health 标 degraded + 真实落库清理
"""
from __future__ import annotations

from app.intel.ingest import rsshub as RSSHUB
from app.intel.ingest.rsshub import RSSHubSource, resolve_rsshub_url


# --------------------------------------------------------------------------
# 单元：URL 解析
# --------------------------------------------------------------------------
def test_resolve_rsshub_route_with_base(monkeypatch):
    monkeypatch.setattr("app.intel.ingest.rsshub.settings.INTEL_RSSHUB_BASE_URL",
                        "http://localhost:1200/")
    assert resolve_rsshub_url("rsshub://wechat/customer/abc") == \
        "http://localhost:1200/wechat/customer/abc"


def test_resolve_rsshub_no_base_raises(monkeypatch):
    import pytest
    monkeypatch.setattr("app.intel.ingest.rsshub.settings.INTEL_RSSHUB_BASE_URL", "")
    # resolve_rsshub_url 未配置 base URL 时抛 ValueError
    with pytest.raises(ValueError):
        resolve_rsshub_url("rsshub://wechat/abc")
    # fetch 内部捕获该异常 → 返回 ok=False（而非抛出）
    res = RSSHubSource("t").fetch("rsshub://wechat/abc")
    assert res.ok is False
    assert "INTEL_RSSHUB_BASE_URL" in res.error


def test_resolve_plain_url_passthrough():
    assert resolve_rsshub_url("http://example.com/feed") == "http://example.com/feed"


# --------------------------------------------------------------------------
# 单元：fetch 解析 RSS（mock httpx）
# --------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, timeout=None):
        return _FakeResp(self._content)


RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>测试源</title>
    <item>
      <title>贵州茅台三季度营收同比增长</title>
      <link>https://example.com/news/123</link>
      <guid>news-123</guid>
      <pubDate>Mon, 07 Sep 2026 04:00:00 GMT</pubDate>
      <dc:creator>张三</dc:creator>
      <content:encoded><![CDATA[<p>贵州茅台(600519)营收同比增长15%。</p>]]></content:encoded>
    </item>
  </channel>
</rss>
""".encode("utf-8")


def test_fetch_parses_rss(monkeypatch):
    import app.intel.ingest.rsshub as M
    monkeypatch.setattr(M.httpx, "Client", lambda *a, **k: _FakeClient(RSS2))
    res = RSSHubSource("t").fetch("http://localhost:1200/wechat/x")
    assert res.ok is True
    assert len(res.items) == 1
    it = res.items[0]
    assert it.title == "贵州茅台三季度营收同比增长"
    assert it.url == "https://example.com/news/123"
    assert it.author == "张三"
    assert "600519" in it.content_html


def test_fetch_rsshub_url_without_base_returns_error(monkeypatch):
    import app.intel.ingest.rsshub as M
    monkeypatch.setattr(M.settings, "INTEL_RSSHUB_BASE_URL", "")
    res = RSSHubSource("t").fetch("rsshub://wechat/abc")
    assert res.ok is False
    assert "INTEL_RSSHUB_BASE_URL" in res.error


# --------------------------------------------------------------------------
# 集成：run_rsshub_ingest 标 degraded + 真实落库
# --------------------------------------------------------------------------
def test_run_rsshub_ingest_marks_degraded_and_ingests(monkeypatch):
    import app.intel.ingest.rsshub as M
    from app.intel import service as SVC
    from app.intel import store

    # 1) 登记一个 enabled 的 rsshub 源（url 随便，fetch 被 mock）
    src = SVC.ensure_rsshub_source("pytest_rsshub", "http://localhost:1200/wechat/x",
                                   enabled=True)
    try:
        # 2) mock fetch 返回 1 篇（绕开网络）
        def fake_fetch(self, url, *, limit=50, timeout=15.0):
            from app.intel.ingest.base import FetchResult, FeedItem
            return FetchResult(source_name=self.name, ok=True, items=[FeedItem(
                external_id="news-xyz", title="测试文", url="http://x/1",
                content_html="<p>正文</p>", published_at=None, author="", summary="正文",
            )])
        monkeypatch.setattr(M.RSSHubSource, "fetch", fake_fetch)

        summary = SVC.run_rsshub_ingest()
        assert summary["new"] >= 1

        # 3) feed_health 必须标 degraded（不是 ok）
        with store.get_engine().connect() as c:
            rows = c.execute(
                store.text(
                    "SELECT status, error FROM intel.feed_health "
                    "WHERE source_id=:sid ORDER BY checked_at DESC LIMIT 1"
                ), {"sid": src["id"]}
            ).mappings().all()
            assert rows, "feed_health 应有记录"
            assert rows[0]["status"] == "degraded"
        # 4) 落库回查
        with store.get_engine().connect() as c:
            n = c.execute(
                store.text("SELECT count(*) FROM intel.documents WHERE source_id=:sid"),
                {"sid": src["id"]},
            ).scalar()
            assert n >= 1
    finally:
        from app.intel import store
        with store.get_engine().begin() as c:
            c.execute(
                store.text("DELETE FROM intel.documents WHERE source_id=:sid"),
                {"sid": src["id"]},
            )
            c.execute(
                store.text("DELETE FROM intel.feed_health WHERE source_id=:sid"),
                {"sid": src["id"]},
            )
            c.execute(store.text("DELETE FROM intel.sources WHERE id=:sid"),
                      {"sid": src["id"]})


def test_run_rsshub_ingest_no_enabled_sources():
    from app.intel import service as SVC
    summary = SVC.run_rsshub_ingest()
    assert summary["sources"] == 0
