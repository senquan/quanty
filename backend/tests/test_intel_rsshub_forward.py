"""P4-3 RSSHub 转发：网关只做代转发，错误码必须原样透传

为什么单独测网关：RSSHub 的业务规则（rsshub:// 没配 base URL → 400；源下已有
文档不许删 → 409）都在 dc 侧。如果网关把它们一律吞成 500，用户只会看到
"删除失败"，而真正该看到的是"该源下已有 3 篇文档，请改为停用"。
这里断言的就是**透传**：URL、method、body、以及 dc 的业务错误码。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.api_v1.endpoints.intel import router as intel_router  # noqa: E402
from app.core.config import settings as app_settings  # noqa: E402
from app.core.dependencies import get_current_user  # noqa: E402
from app.models.user import User  # noqa: E402

DC_BASE = "http://dc.test:8100"


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


class _FakeClient:
    """替身 httpx.AsyncClient：记录转发内容，并按 queued 返回预设响应"""

    captured: dict = {}
    next_response: _FakeResponse | None = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None, headers=None):
        _FakeClient.captured = {
            "method": method,
            "url": url,
            "json": json,
            "headers": headers or {},
        }
        return _FakeClient.next_response or _FakeResponse(200, {"ok": True})


@pytest.fixture()
def client(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(app_settings, "INTEL_CLEANER_BASE_URL", DC_BASE)
    monkeypatch.setattr(app_settings, "INTEL_CLEANER_API_KEY", "k-123")
    _FakeClient.captured = {}
    _FakeClient.next_response = None

    app = FastAPI()
    app.include_router(intel_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="tester")
    return TestClient(app)


def test_status_is_forwarded_to_dc(client):
    _FakeClient.next_response = _FakeResponse(
        200, {"baseUrl": "http://rsshub.local", "configured": True, "total": 2}
    )
    r = client.get("/api/v1/intel/rsshub/status")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 2
    assert _FakeClient.captured["url"] == f"{DC_BASE}/api/v1/intel/rsshub/status"
    assert _FakeClient.captured["method"] == "GET"
    assert _FakeClient.captured["headers"].get("X-API-Key") == "k-123"


def test_add_source_forwards_body(client):
    _FakeClient.next_response = _FakeResponse(200, {"id": 7, "enabled": False})
    r = client.post(
        "/api/v1/intel/rsshub/sources",
        json={"name": "公众号X", "url": "rsshub://wechat/x", "enabled": False},
    )
    assert r.status_code == 200, r.text
    assert _FakeClient.captured["method"] == "POST"
    assert _FakeClient.captured["json"]["name"] == "公众号X"
    assert _FakeClient.captured["json"]["url"] == "rsshub://wechat/x"
    # credibility 有默认值，必须带上，否则 dc 侧会退回 low 之外的口径不一致
    assert "credibility" in _FakeClient.captured["json"]


def test_patch_sends_only_changed_fields(client):
    """PATCH 只传改动字段：把 enabled 一起传过去会踩"局部更新"的语义坑"""
    _FakeClient.next_response = _FakeResponse(200, {"id": 3, "updated": True})
    r = client.patch("/api/v1/intel/rsshub/sources/3", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert _FakeClient.captured["method"] == "PATCH"
    assert _FakeClient.captured["url"].endswith("/api/v1/intel/rsshub/sources/3")
    assert _FakeClient.captured["json"] == {"enabled": True}


def test_dc_409_is_surfaced_not_swallowed(client):
    """dc 的 409（源下已有文档不许删）要原样透传，不能变成笼统的 500"""
    _FakeClient.next_response = _FakeResponse(
        409, {"detail": "该源下已有 3 篇文档，删除会破坏溯源；请改为停用"}
    )
    r = client.delete("/api/v1/intel/rsshub/sources/3")
    body = r.json()
    assert body["code"] == 409, body
    assert "3 篇文档" in body["msg"]
    assert _FakeClient.captured["method"] == "DELETE"


def test_dc_400_base_url_missing_is_surfaced(client):
    _FakeClient.next_response = _FakeResponse(
        400, {"detail": "未配置 INTEL_RSSHUB_BASE_URL，无法解析 rsshub:// 源"}
    )
    r = client.post(
        "/api/v1/intel/rsshub/sources", json={"name": "x", "url": "rsshub://a/b"}
    )
    body = r.json()
    assert body["code"] == 400, body
    assert "INTEL_RSSHUB_BASE_URL" in body["msg"]


def test_run_and_test_endpoints(client):
    _FakeClient.next_response = _FakeResponse(200, {"sources": 1, "new": 5})
    r = client.post("/api/v1/intel/rsshub/run")
    assert r.status_code == 200, r.text
    assert _FakeClient.captured["url"].endswith("/rsshub/run")

    _FakeClient.next_response = _FakeResponse(200, {"ok": True, "count": 2})
    r = client.post("/api/v1/intel/rsshub/test", json={"url": "http://x/feed", "limit": 5})
    assert r.status_code == 200, r.text
    assert _FakeClient.captured["json"] == {"url": "http://x/feed", "limit": 5}


def test_dc_unreachable_returns_502(client, monkeypatch):
    import httpx

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    r = client.get("/api/v1/intel/rsshub/status")
    body = r.json()
    assert body["code"] == 502, body
    assert "data-cleaner" in body["msg"]
