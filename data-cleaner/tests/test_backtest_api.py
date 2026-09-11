"""脚本策略回测 HTTP 接口测试（R3）

重点不是「能不能算出数字」，而是**状态码语义**：
闸口拒绝必须是 422（这是「这个请求不成立」），不能被 500 或 200 吞掉 ——
backend 转发层按这个码决定透传还是报错，前端按这个码决定展示哪块 UI。

service 层用 monkeypatch 替身，不连库。
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.backtest.data import BacktestDataError
from app.backtest.gate import Refusal
from app.backtest.market_rules import DATA_SOURCE
from app.backtest.service import BacktestRefused
from app.main import app

client = TestClient(app)

OK_RESULT = {
    "symbol": "600519.SH",
    "start": "2021-01-01",
    "end": "2024-12-31",
    "total_return": 12.34,
    "sharpe_ratio": 0.5,
    "max_drawdown": 20.0,
    "win_rate": 50.0,
    "total_trades": 4,
    "final_capital": 112_340.0,
    "final_position": 100,
    "cash": 340.0,
    "trades": [{"type": "buy", "price": 10.0, "quantity": 100,
                "timestamp": "2021-02-01", "bar": 20, "fee": 5.01}],
    "daily_returns": [0.01],
    "portfolio_values": [100_000.0, 101_000.0],
    "portfolio_dates": ["2021-01-04", "2021-01-05"],
    "limits": ["T+1"],
    "warnings": ["撮合假设…"],
    "rejections": [],
    "total_fees": 10.02,
    "plan": {"symbol": "600519.SH", "market": "a_share", "price_field": "qfq"},
    "data": {"bars": 300, "price_field": "qfq", "hfq_factor": None},
}

PAYLOAD = {
    "symbol": "600519.SH",
    "start": "2021-01-01",
    "end": "2024-12-31",
    "code": "def on_data(data, context):\n    buy(data['close'].iloc[0])",
}


@pytest.fixture
def ok(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.backtest.run_script_backtest", lambda req: OK_RESULT
    )


# ── 元信息 ──────────────────────────────────────────────

def test_styles_returns_gate_tables():
    r = client.get("/api/v1/backtest/styles")
    assert r.status_code == 200
    body = r.json()
    assert body["data_source"] == DATA_SOURCE
    keys = {s["key"] for s in body["styles"]}
    assert {"long", "swing", "intraday"} == keys
    swing = next(s for s in body["styles"] if s["key"] == "swing")
    assert swing["min_bars"] == 240 and swing["why_min"]
    assert {p["key"] for p in body["price_fields"]} == {"qfq", "hfq"}
    assert all(p["note"] for p in body["price_fields"])


# ── 正常路径 ────────────────────────────────────────────

def test_script_backtest_ok(ok):
    r = client.post("/api/v1/backtest/script", json=PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "600519.SH"
    assert body["total_return"] == 12.34
    assert body["limits"] and body["warnings"]


def test_script_backtest_defaults(ok, monkeypatch):
    """未传的可选项要落到闸口认得的默认值上。"""
    seen = {}

    def fake(req):
        seen["style"] = req.style
        seen["price_field"] = req.price_field
        seen["capital"] = req.initial_capital
        seen["market_rules"] = req.apply_market_rules
        return OK_RESULT

    monkeypatch.setattr("app.api.v1.backtest.run_script_backtest", fake)
    assert client.post("/api/v1/backtest/script", json=PAYLOAD).status_code == 200
    assert seen == {
        "style": "swing", "price_field": "qfq",
        "capital": 100_000.0, "market_rules": True,
    }


# ── 422：这个请求不成立 ─────────────────────────────────

def test_refusal_becomes_422(monkeypatch):
    def fake(req):
        raise BacktestRefused(Refusal("区间太短", "把区间拉长"))

    monkeypatch.setattr("app.api.v1.backtest.run_script_backtest", fake)
    r = client.post("/api/v1/backtest/script", json=PAYLOAD)
    assert r.status_code == 422
    assert r.json()["detail"] == {"reason": "区间太短", "remedy": "把区间拉长"}


def test_data_error_becomes_422(monkeypatch):
    def fake(req):
        raise BacktestDataError("600519.SH 没有行情", "换个区间")

    monkeypatch.setattr("app.api.v1.backtest.run_script_backtest", fake)
    r = client.post("/api/v1/backtest/script", json=PAYLOAD)
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "600519.SH 没有行情"


def test_missing_required_field_is_422(ok):
    r = client.post("/api/v1/backtest/script", json={"symbol": "600519.SH"})
    assert r.status_code == 422


def test_bad_capital_is_422(ok):
    r = client.post("/api/v1/backtest/script", json={**PAYLOAD, "initial_capital": 0})
    assert r.status_code == 422


# ── 500：服务端出错 ─────────────────────────────────────

def test_unexpected_error_becomes_500(monkeypatch):
    def fake(req):
        raise RuntimeError("PG 连接断了")

    monkeypatch.setattr("app.api.v1.backtest.run_script_backtest", fake)
    r = client.post("/api/v1/backtest/script", json=PAYLOAD)
    assert r.status_code == 500
    assert "PG 连接断了" in r.json()["detail"]
