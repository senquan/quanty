"""P4-1 上传代转发：后端网关层的扩展名白名单与失败合并

为什么单开一个文件测后端：zip 解包在 data-cleaner（dc）侧实现，但**前端不直连 dc**，
必须经主后端转发。网关的白名单只要漏一个扩展名，功能就形同没有 —— 而这类漏配
在 dc 侧的单测里**永远测不出来**（dc 只看到"网关放行后才过来的请求"）。

因此这里断言两件事：
① `.zip` 必须被网关放行并转发给 dc；
② 网关自己拒收的文件也要回传给前端，不能"丢了文件却没提示"。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.api_v1.endpoints.intel import (  # noqa: E402
    UPLOAD_ALLOWED_EXT,
    router as intel_router,
)
from app.core.dependencies import get_current_user  # noqa: E402
from app.models.user import User  # noqa: E402

HTML = b"<html><head><title>t</title></head><body><p>content</p></body></html>"


class _FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """替身 httpx.AsyncClient：只记下被转发的内容，不真发请求"""

    captured: dict = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, files=None, data=None, headers=None):
        _FakeClient.captured = {"url": url, "files": files, "data": data}
        return _FakeResponse({
            "source": "s", "source_type": "manual", "dry_run": False,
            "uploaded": len(files or []), "accepted": len(files or []),
            "extracted": 0, "failed": 0, "fetched": len(files or []),
            "new": len(files or []), "dup": 0, "reposts": 0,
            "rejected": [], "files": [],
        })


@pytest.fixture()
def client(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.captured = {}

    app = FastAPI()
    app.include_router(intel_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="tester")
    return TestClient(app)


def test_zip_is_allowed_and_forwarded(client):
    """.zip 必须放行：dc 侧会自动解压，网关漏配 = 功能形同没有"""
    r = client.post(
        "/api/v1/intel/upload",
        files=[("files", ("bundle.zip", b"PK\x03\x04fake", "application/zip"))],
        data={"source_name": "公众号A", "source_type": "wechat"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["accepted"] == 1
    sent = _FakeClient.captured["files"]
    assert sent and sent[0][0] == "files" and sent[0][1][0] == "bundle.zip"
    assert _FakeClient.captured["data"]["source_name"] == "公众号A"


def test_all_documented_exts_are_allowed(client):
    """白名单快照：与 dc 侧 app/intel/api.py::UPLOAD_ALLOWED_EXT 必须一致"""
    assert UPLOAD_ALLOWED_EXT == {
        ".html", ".htm", ".mhtml", ".txt", ".md", ".csv", ".zip",
    }


def test_unsupported_ext_is_rejected_before_forwarding(client):
    # 项目统一响应：HTTP 恒 200，业务码在 body.code（见 app/schemas/response.py）
    r = client.post(
        "/api/v1/intel/upload",
        files=[("files", ("x.pdf", b"junk", "application/pdf"))],
        data={"source_name": "s"},
    )
    body = r.json()
    assert body["code"] == 400, body
    assert "不支持的扩展名" in body["msg"]
    assert _FakeClient.captured == {}  # 一个都没转发


def test_gateway_rejections_are_merged_into_dc_response(client):
    """网关拒收的也要回给前端 —— 否则用户以为文件丢了"""
    r = client.post(
        "/api/v1/intel/upload",
        files=[
            ("files", ("a.html", HTML, "text/html")),
            ("files", ("y.exe", b"junk", "application/octet-stream")),
        ],
        data={"source_name": "s"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["failed"] == 1
    assert any(x["file"] == "y.exe" for x in body["rejected"])
    # 只转发了通过网关校验的那一个
    assert len(_FakeClient.captured["files"]) == 1


def _post(client, files, **data):
    return client.post("/api/v1/intel/upload", files=files, data=data)


def test_extract_flag_is_forwarded(client):
    """extract 必须原样传给 dc，否则"上传后立刻抽取"在网关层就被吞了"""
    _post(client, [("files", ("a.html", HTML, "text/html"))],
          source_name="s", extract="true")
    assert _FakeClient.captured["data"]["extract"] == "true"


def test_extract_limit_only_sent_when_positive(client):
    """extract_limit=0 表示"用 dc 默认值"，不该把 0 传下去把抽取清零"""
    _post(client, [("files", ("a.html", HTML, "text/html"))],
          source_name="s", extract="true", extract_limit="20")
    assert _FakeClient.captured["data"]["extract_limit"] == "20"

    _FakeClient.captured = {}
    _post(client, [("files", ("a.html", HTML, "text/html"))],
          source_name="s", extract="true", extract_limit="0")
    assert "extract_limit" not in _FakeClient.captured["data"]


def test_dc_unreachable_returns_502(client, monkeypatch):
    import httpx

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    r = client.post(
        "/api/v1/intel/upload",
        files=[("files", ("a.html", HTML, "text/html"))],
        data={"source_name": "s"},
    )
    body = r.json()
    assert body["code"] == 502, body
    assert "data-cleaner" in body["msg"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
