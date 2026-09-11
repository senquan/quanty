"""全局 HTTP 异常处理器回归测试。

2026-09-06 R5 修的 bug:``Response.msg`` 是 ``str``,而闸口拒绝的 422 给的
``detail`` 是 **dict** ``{"reason", "remedy"}``。原实现把它直接塞进 ``msg``,
pydantic 在**异常处理器内部**二次抛 ValidationError ——
「这个回测不成立」被翻译成 500,用户以为服务挂了,去翻根本没有错误的日志。

⚠️ 这个 bug 单测 proxy 层测不出来:它只在完整 app(带全局 handler)上才暴露。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _client() -> TestClient:
    """建一个只带全局 handler 的最小 app —— 与 main.py 的注册方式一致。"""
    from app.schemas.response import Response
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def handler(request, exc):
        # 与 main.py 保持同一份实现
        detail = exc.detail
        if isinstance(detail, (dict, list)):
            msg = "请求被拒绝" if exc.status_code == 422 else "请求失败"
            body = Response.fail(code=exc.status_code, msg=msg, data=detail)
        else:
            body = Response.fail(code=exc.status_code, msg=str(detail))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.get("/dict-422")
    async def dict_422():
        raise HTTPException(
            status_code=422,
            detail={"reason": "样本不够", "remedy": "换更长的区间"},
        )

    @app.get("/str-404")
    async def str_404():
        raise HTTPException(status_code=404, detail="Strategy not found")

    @app.get("/list-400")
    async def list_400():
        raise HTTPException(status_code=400, detail=["a", "b"])

    return TestClient(app, raise_server_exceptions=False)


def test_dict_detail_goes_to_data_not_msg():
    """闸口拒绝:dict 必须落进 data,不能塞 msg(会二次抛错变 500)。"""
    r = _client().get("/dict-422")
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["data"]["reason"] == "样本不够"
    assert body["data"]["remedy"] == "换更长的区间"
    # msg 退化成一句人话,不再承载结构化内容
    assert isinstance(body["msg"], str) and body["msg"] == "请求被拒绝"


def test_string_detail_stays_in_msg():
    """字符串形态保持原样 —— 不能为了修 dict 把普通 404 也改坏。"""
    r = _client().get("/str-404")
    assert r.status_code == 404
    body = r.json()
    assert body["msg"] == "Strategy not found"
    assert body["data"] is None


def test_list_detail_also_goes_to_data():
    r = _client().get("/list-400")
    assert r.status_code == 400
    assert r.json()["data"] == ["a", "b"]


def test_main_app_registers_the_same_behaviour():
    """真正的 app 也要能跑 —— 防止 main.py 里那份实现被改回去。"""
    import main

    client = TestClient(main.app, raise_server_exceptions=False)
    # 一个不存在的路径会走 StarletteHTTPException(404, detail 是字符串)
    r = client.get("/api/v1/__definitely_not_here__")
    assert r.status_code == 404
    assert isinstance(r.json()["msg"], str)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
