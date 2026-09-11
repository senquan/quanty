"""因子 API 与因子库集成测试（不依赖外部数据库/数据源）"""
import pandas as pd
from fastapi.testclient import TestClient

from app.factors.registry import list_factors
from app.main import app

client = TestClient(app)


def test_factor_list():
    resp = client.get("/api/v1/factor/")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) >= 15  # 方案要求 ≥15 个因子


def test_factor_list_by_category():
    resp = client.get("/api/v1/factor/", params={"category": "momentum"})
    assert resp.status_code == 200
    items = resp.json()
    assert all(i["category"] == "momentum" for i in items)


def test_factor_search():
    resp = client.get("/api/v1/factor/", params={"search": "rsi"})
    assert resp.status_code == 200
    items = resp.json()
    assert any("RSI" in i["name"] for i in items)


def test_factor_detail():
    resp = client.get("/api/v1/factor/TECH_RSI_14")
    assert resp.status_code == 200
    assert resp.json()["code"] == "TECH_RSI_14"


def test_factor_detail_not_found():
    resp = client.get("/api/v1/factor/NOPE")
    assert resp.status_code == 404


def test_registry_has_four_categories():
    cats = {i["category"] for i in list_factors()}
    assert cats >= {"momentum", "volatility", "technical", "sentiment"}
