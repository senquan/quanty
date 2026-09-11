"""P4-3 RSSHub 源管理接口测试（/api/v1/intel/rsshub/*）

为什么单开一个文件测接口层：RSSHub 之前只有 CLI（`_p4_rsshub_ingest.py`），
网页管理页一上，**接口错误就直接变成用户可见的报错**，而 CLI 时代很多分支
（未配置 base URL、删除有文档的源）根本没人走过。这里覆盖的是接口层的判定：
  - rsshub:// 但没配 base URL → 必须早失败 400，不能建一个永远拉不通的源；
  - 新增默认停用（第三方中继不自动开），且 upsert 的 ON CONFLICT 不改 enabled
    的坑要被显式对齐覆盖；
  - 有文档的源不允许删（409），否则 doc_mentions 溯源断裂；
  - test 只探活、不入库（不写 feed_health）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.intel import store
from app.intel.api import router as intel_router
from app.intel.ingest.base import FeedItem, FetchResult
from app.intel.store import get_engine

app = FastAPI()
app.include_router(intel_router, prefix="/api/v1/intel")


@pytest.fixture(scope="session")
def client():
    """**会话级** + ``with`` 上下文，两条都不能少

    - 必须 ``with``：TestClient 在 context 外会为每次请求新建 event loop；
    - 必须会话级：每个测试函数各建一个 client 同样是新 loop。

    只要换了 loop，asyncpg 连接池里上次建的连接就还挂在旧 loop 上，下一个请求
    立刻报 "another operation is in progress"（真跑 uvicorn 时是同一个 loop，
    不会触发）。这是测试环境专属坑，但足够隐蔽——表现为"只有第一个用例过"。
    """
    with TestClient(app) as c:
        yield c

SRC = "pytest_rsshub"
FEED = "http://pytest-rsshub.invalid/feed.xml"


def _cleanup(*names: str) -> None:
    """删除测试源及其附属数据（content_hash 去重是跨源全局的，必须清干净）"""
    with get_engine().begin() as c:
        for n in names:
            c.execute(store.text(
                "DELETE FROM intel.doc_mentions WHERE doc_id IN "
                "(SELECT id FROM intel.documents WHERE source_id IN "
                " (SELECT id FROM intel.sources WHERE name = :n))"), {"n": n})
            c.execute(store.text(
                "DELETE FROM intel.documents WHERE source_id IN "
                "(SELECT id FROM intel.sources WHERE name = :n)"), {"n": n})
            c.execute(store.text(
                "DELETE FROM intel.feed_health WHERE source_id IN "
                "(SELECT id FROM intel.sources WHERE name = :n)"), {"n": n})
            c.execute(store.text("DELETE FROM intel.sources WHERE name = :n"), {"n": n})


@pytest.fixture(autouse=True)
def _clean():
    _cleanup(SRC)
    yield
    _cleanup(SRC)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def test_status_shape(client):
    r = client.get("/api/v1/intel/rsshub/status")
    assert r.status_code == 200, r.text
    b = r.json()
    assert {"baseUrl", "configured", "total", "enabledCount"} <= b.keys()


# --------------------------------------------------------------------------
# 登记
# --------------------------------------------------------------------------
def test_add_source_default_disabled(client):
    r = client.post("/api/v1/intel/rsshub/sources",
                    json={"name": SRC, "url": FEED})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["enabled"] is False  # 第三方中继默认不开

    rows = {s["id"]: s for s in client.get("/api/v1/intel/rsshub/sources").json()["sources"]}
    assert sid in rows
    assert rows[sid]["enabled"] is False
    assert rows[sid]["docCount"] == 0


def test_add_source_enabled_true_is_persisted(client):
    """upsert 的 ON CONFLICT(url) 不改 enabled，接口必须显式对齐，否则页面勾选无效"""
    r = client.post("/api/v1/intel/rsshub/sources",
                    json={"name": SRC, "url": FEED, "enabled": True})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    rows = {s["id"]: s for s in client.get("/api/v1/intel/rsshub/sources").json()["sources"]}
    assert rows[sid]["enabled"] is True


def test_add_source_rsshub_route_without_base_url_rejected(client, monkeypatch):
    monkeypatch.setattr("app.intel.ingest.rsshub.settings.INTEL_RSSHUB_BASE_URL", "")
    r = client.post("/api/v1/intel/rsshub/sources",
                    json={"name": SRC, "url": "rsshub://wechat/ershicimi/xxx"})
    assert r.status_code == 400
    assert "INTEL_RSSHUB_BASE_URL" in r.json()["detail"]


def test_add_source_rejects_empty_name(client):
    r = client.post("/api/v1/intel/rsshub/sources", json={"name": "  ", "url": FEED})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# 修改 / 删除
# --------------------------------------------------------------------------
def test_patch_toggle_enabled(client):
    sid = client.post("/api/v1/intel/rsshub/sources",
                      json={"name": SRC, "url": FEED}).json()["id"]
    r = client.patch(f"/api/v1/intel/rsshub/sources/{sid}", json={"enabled": True})
    assert r.status_code == 200, r.text

    rows = {s["id"]: s for s in client.get("/api/v1/intel/rsshub/sources").json()["sources"]}
    assert rows[sid]["enabled"] is True


def test_delete_source_without_docs_ok(client):
    sid = client.post("/api/v1/intel/rsshub/sources",
                      json={"name": SRC, "url": FEED}).json()["id"]
    r = client.delete(f"/api/v1/intel/rsshub/sources/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True


def test_delete_source_with_docs_rejected(client, monkeypatch):
    """有文档的源不许删：删了 doc_mentions 的溯源就断了，只能停用"""
    sid = client.post("/api/v1/intel/rsshub/sources",
                      json={"name": SRC, "url": FEED}).json()["id"]
    monkeypatch.setattr(store, "count_documents_by_source", lambda _sid: 3)
    r = client.delete(f"/api/v1/intel/rsshub/sources/{sid}")
    assert r.status_code == 409
    assert "3" in r.json()["detail"]


def test_patch_missing_source_404(client):
    r = client.patch("/api/v1/intel/rsshub/sources/999999", json={"enabled": True})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# test / run
# --------------------------------------------------------------------------
def test_probe_url_ok(client, monkeypatch):
    def _fake_fetch(self, url, *, limit=50, timeout=15.0):
        return FetchResult(
            source_name="probe", ok=True,
            items=[FeedItem(external_id="1", title="标题A", url="http://x/1",
                            content_html="<p>x</p>", published_at=None,
                            author=None, summary=None)],
            latency_ms=12,
        )

    monkeypatch.setattr("app.intel.ingest.rsshub.RSSHubSource.fetch", _fake_fetch)
    r = client.post("/api/v1/intel/rsshub/test", json={"url": FEED, "limit": 5})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] is True
    assert b["count"] == 1
    assert b["titles"] == ["标题A"]
    # 探活不入库：源清单里不应出现任何新源
    assert client.get("/api/v1/intel/rsshub/sources").json()["count"] == 0


def test_probe_url_failure_surfaces_error(client, monkeypatch):
    def _fake_fetch(self, url, *, limit=50, timeout=15.0):
        return FetchResult(source_name="probe", ok=False, error="ConnectTimeout: 超时",
                           latency_ms=15000)

    monkeypatch.setattr("app.intel.ingest.rsshub.RSSHubSource.fetch", _fake_fetch)
    r = client.post("/api/v1/intel/rsshub/test", json={"url": FEED})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] is False
    assert "超时" in b["error"]


def test_run_proxies_summary(client, monkeypatch):
    calls = {}

    def _fake_run():
        calls["ran"] = True
        return {"sources": 1, "fetched": 5, "new": 2, "dup": 3, "reposts": 0,
                "failed_sources": 0, "details": []}

    monkeypatch.setattr("app.intel.service.run_rsshub_ingest", _fake_run)
    r = client.post("/api/v1/intel/rsshub/run")
    assert r.status_code == 200, r.text
    assert calls.get("ran") is True
    assert r.json()["new"] == 2
