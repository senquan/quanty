"""因子 CRUD + formula 沙箱测试（不依赖外部 PG，用 monkeypatch 模拟 db）"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.factors.formula import FormulaError, compile_formula
from app.main import app

client = TestClient(app)


def test_formula_basic():
    df = pd.DataFrame(
        {
            "symbol": ["A"] * 10,
            "adj_close": list(range(100, 110)),
            "volume": [1000] * 10,
        }
    )
    fn = compile_formula("adj_close / delay(adj_close, 1) - 1")
    out = fn(df)
    assert len(out) == 10
    assert pd.isna(out.iloc[0])  # 首个为 NaN（delay 无前值）
    assert abs(out.iloc[2] - ((102 / 101) - 1)) < 1e-9


def test_formula_forbidden_func():
    df = pd.DataFrame({"symbol": ["A"], "adj_close": [1.0]})
    with pytest.raises(FormulaError):
        compile_formula("__import__('os')")


def test_formula_forbidden_col():
    df = pd.DataFrame({"symbol": ["A"], "adj_close": [1.0]})
    with pytest.raises(FormulaError):
        compile_formula("secret_col * 2")(df)


def test_factor_crud_flow(monkeypatch):
    # 用内存字典模拟 db 层，避免依赖 PG
    store = {}

    async def fake_upsert(meta, author="user"):
        store[meta["code"]] = meta

    async def fake_list():
        return list(store.values())

    async def fake_delete(code, author):
        return store.pop(code, None) is not None

    async def fake_metrics(code):
        return []

    import app.storage.db as dbmod

    monkeypatch.setattr(dbmod, "upsert_factor_definition", fake_upsert)
    monkeypatch.setattr(dbmod, "list_factor_definitions", fake_list)
    monkeypatch.setattr(dbmod, "delete_factor_definition", fake_delete)
    monkeypatch.setattr(dbmod, "get_factor_metrics", fake_metrics)

    # 创建
    r = client.post(
        "/api/v1/factor/",
        json={
            "code": "CUSTOM_TEST",
            "name": "测试因子",
            "category": "momentum",
            "formula": "adj_close / delay(adj_close, 5) - 1",
        },
    )
    assert r.status_code == 201, r.text

    # 列表可见
    r = client.get("/api/v1/factor/", params={"search": "CUSTOM"})
    assert r.status_code == 200
    assert any(i["code"] == "CUSTOM_TEST" for i in r.json())

    # 删除
    r = client.delete("/api/v1/factor/CUSTOM_TEST")
    assert r.status_code == 200


def test_factor_evaluate():
    r = client.post(
        "/api/v1/factor/MOM_RET_20/evaluate",
        json={"factorValues": [0.1, 0.2, -0.1, 0.3, 0.05] * 10,
              "forwardReturns": [0.01, -0.02, 0.03, 0.01, -0.01] * 10},
    )
    assert r.status_code == 200
    body = r.json()
    assert "icMean" in body


def test_factor_batch_evaluate():
    """批量评估应返回每项结果与汇总"""
    import numpy as np

    rng = np.random.RandomState(7)
    n = 120

    def mk(seed):
        fv = rng.normal(0, 1, n).tolist()
        fr = (0.01 * np.array(fv) + rng.normal(0, 0.02, n)).tolist()
        return {"code": seed, "factorValues": fv, "forwardReturns": fr}

    items = [mk("MOM_A"), mk("MOM_B"), mk("MOM_C")]
    r = client.post(
        "/api/v1/factor/batch-evaluate",
        json={"items": items, "asOf": "2026-08-26"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["total"] == 3
    assert body["summary"]["succeeded"] == 3
    assert len(body["results"]) == 3
    # 每个成功项都应含 icMean / sharpeRatio
    for res in body["results"]:
        assert res["status"] == "ok"
        assert "icMean" in res and "sharpeRatio" in res
    assert 0.0 <= body["summary"]["avgIcMean"] <= 1.0
