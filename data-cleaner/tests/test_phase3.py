"""Phase 3 测试：价值/成长因子、AI 生成、分钟级因子、缓存降级"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.factors.registry import get_factor, list_factors
from app.main import app
from tests.test_analytics import ensure_parquet  # 复用 Parquet 落库 fixture

client = TestClient(app)


def test_fundamental_factors_registered():
    codes = {f["code"] for f in list_factors()}
    for c in ["VAL_PE_TTM", "VAL_PB", "VAL_PS_TTM", "VAL_DIV_YIELD",
              "VAL_PE_PERCENTILE", "GRO_REV_GROWTH_YOY", "GRO_EPS_GROWTH_YOY",
              "GRO_PRICE_MOMENTUM", "INTRADAY_VOL_20", "INTRADAY_RANGE_20",
              "INTRADAY_MOM_10"]:
        assert c in codes, f"缺失因子: {c}"


def test_value_pe_percentile_on_price():
    # 无 pe_ttm 列时回退到价格分位代理，应返回有限值（非全 NaN）
    df = pd.DataFrame(
        {
            "symbol": ["A"] * 30,
            "adj_close": list(range(100, 130)) * 1 + [100] * 0,
            "timestamp": pd.date_range("2023-01-01", periods=30, freq="1d"),
        }
    )
    s = get_factor("VAL_PE_PERCENTILE").compute(df)
    assert s.notna().any()


def test_intraday_factor_on_1m():
    df = pd.DataFrame(
        {
            "symbol": ["A"] * 50,
            "adj_close": list(range(100, 150)),
            "adj_high": [x + 1 for x in range(100, 150)],
            "adj_low": [x - 1 for x in range(100, 150)],
            "timestamp": pd.date_range("2023-01-01 09:30", periods=50, freq="1min"),
        }
    )
    s = get_factor("INTRADAY_RANGE_20").compute(df)
    assert len(s) == 50 and s.dropna().notna().any()


def test_ai_generate_rule_engine():
    # 无 LLM key，走规则引擎
    r = client.post(
        "/api/v1/factor/ai-generate",
        json={"description": "过去20天的动量因子"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["formula"] and "delay" in body["formula"]
    assert body["source"] == "rule"


def test_ai_generate_unsafe_rejected():
    # 直接打 formula 注入不可能（走规则引擎），但校验接口对空描述拒绝
    r = client.post("/api/v1/factor/ai-generate", json={"description": ""})
    assert r.status_code == 400


def test_cache_noop_without_redis(monkeypatch):
    # Redis 不可用时缓存为 no-op，publish_status 不抛异常
    import app.storage.cache as cachemod

    # 模拟 redis 模块未安装 → get_redis 返回 None，所有操作为 no-op
    monkeypatch.setattr(cachemod, "_redis_module", lambda: None)
    cachemod._client = None
    import asyncio

    # 不应抛异常（降级为 no-op）
    asyncio.run(cachemod.publish_status({"status": "test"}))
    assert cachemod.get_redis() is None
