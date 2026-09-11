"""脚本策略回测转发层测试（R3）

核心只有一条：**dc 的 422 必须原样透传成 ScriptBacktestRefused**。
包成「转发失败」的话，前端只会弹一句没用的报错，用户去翻根本没有错误的日志 ——
而「这个回测不成立」本来是有明确 remedy 的（区间太短 / 不是 A 股 / 本金买不起一手）。

用 ``asyncio.run`` 驱动（backend 未装 pytest-asyncio，不为此加依赖）。
"""
import asyncio

import pytest

from app.services import factor_strategy_proxy as fs
from app.services.script_backtest_proxy import (
    ScriptBacktestProxyError,
    ScriptBacktestRefused,
    backtest_styles,
    run_script_backtest,
)


class FakeService:
    base_url = "http://dc.test"

    def primary_api_key(self):
        return "k"


async def _svc(db=None, code=None):
    return FakeService()


def run(coro):
    return asyncio.run(coro)


PAYLOAD = {
    "symbol": "600519.SH",
    "start": "2021-01-01",
    "end": "2024-12-31",
    "code": "def on_data(d, c):\n    buy(d['close'].iloc[0])",
}


def _patch(monkeypatch, request_fn):
    monkeypatch.setattr(fs, "pick_service", _svc)
    monkeypatch.setattr(fs, "_request", request_fn)


def test_forwards_and_returns_result(monkeypatch):
    captured = {}

    async def fake_request(s, method, path, *, params=None, payload=None, timeout=30.0):
        captured.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"total_return": 12.3, "symbol": "600519.SH"}

    _patch(monkeypatch, fake_request)
    out = run(run_script_backtest(None, PAYLOAD))
    assert out["total_return"] == 12.3
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/backtest/script"
    assert captured["payload"] == PAYLOAD
    assert captured["timeout"] == 60.0      # 回测比普通 CRUD 慢一档


def test_dc_422_becomes_refusal(monkeypatch):
    async def fake_request(*a, **kw):
        raise fs.FactorStrategyProxyError(
            "清洗服务返回 422：…",
            status_code=422,
            detail={"reason": "区间太短", "remedy": "把区间拉长"},
        )

    _patch(monkeypatch, fake_request)
    with pytest.raises(ScriptBacktestRefused) as e:
        run(run_script_backtest(None, PAYLOAD))
    assert e.value.as_dict() == {"reason": "区间太短", "remedy": "把区间拉长"}


def test_dc_5xx_becomes_proxy_error(monkeypatch):
    async def fake_request(*a, **kw):
        raise fs.FactorStrategyProxyError("清洗服务返回 500：炸了", status_code=500, detail="炸了")

    _patch(monkeypatch, fake_request)
    with pytest.raises(ScriptBacktestProxyError) as e:
        run(run_script_backtest(None, PAYLOAD))
    assert not isinstance(e.value, ScriptBacktestRefused)
    assert "500" in str(e.value)


def test_connect_error_becomes_proxy_error(monkeypatch):
    """连不上 dc：没有 status_code，绝不能当成闸口拒绝。"""
    async def fake_request(*a, **kw):
        raise fs.FactorStrategyProxyError("无法连接清洗服务：…")

    _patch(monkeypatch, fake_request)
    with pytest.raises(ScriptBacktestProxyError) as e:
        run(run_script_backtest(None, PAYLOAD))
    assert not isinstance(e.value, ScriptBacktestRefused)


def test_422_without_detail_falls_back(monkeypatch):
    """dc 返回 422 但 detail 不是 dict：仍然是拒绝，只是没有 remedy。"""
    async def fake_request(*a, **kw):
        raise fs.FactorStrategyProxyError("清洗服务返回 422", status_code=422, detail="区间太短")

    _patch(monkeypatch, fake_request)
    with pytest.raises(ScriptBacktestRefused) as e:
        run(run_script_backtest(None, PAYLOAD))
    assert e.value.reason == "区间太短"
    assert e.value.remedy == ""


def test_styles(monkeypatch):
    async def fake_request(s, method, path, *, params=None, payload=None, timeout=30.0):
        assert method == "GET" and path == "/api/v1/backtest/styles"
        return {"styles": [{"key": "swing", "min_bars": 240}]}

    _patch(monkeypatch, fake_request)
    assert run(backtest_styles(None))["styles"][0]["min_bars"] == 240


def test_styles_error(monkeypatch):
    async def fake_request(*a, **kw):
        raise fs.FactorStrategyProxyError("无法连接清洗服务：…")

    _patch(monkeypatch, fake_request)
    with pytest.raises(ScriptBacktestProxyError):
        run(backtest_styles(None))


def test_non_dict_result_becomes_empty(monkeypatch):
    async def fake_request(*a, **kw):
        return None

    _patch(monkeypatch, fake_request)
    assert run(run_script_backtest(None, PAYLOAD)) == {}
