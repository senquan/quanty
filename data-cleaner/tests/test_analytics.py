"""相关性矩阵与回测接口测试（依赖已落库 Parquet，先跑一次 pipeline）"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1 import pipeline as pipeline_api

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def ensure_parquet():
    # 清理旧 Parquet，避免 latest 读取到更早日期的 fixture 造成污染
    import shutil

    if __import__("os").path.exists("data/factors"):
        shutil.rmtree("data/factors")
    # 先触发一次 CSV 流水线，确保 Parquet 落库
    import asyncio

    asyncio.run(
        pipeline_api.run_default_pipeline(
            source="csv",
            csv_path="tests/fixtures/big_bars.csv",
            freq="1d",
        )
    )
    yield


def test_correlation():
    r = client.post(
        "/api/v1/factor/correlation",
        json={"codes": ["MOM_RET_20", "VOL_STD_20", "TECH_RSI_14"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "MOM_RET_20" in body["correlation"]


def test_correlation_too_few():
    r = client.post("/api/v1/factor/correlation", json={"codes": ["MOM_RET_20"]})
    assert r.status_code == 400


def test_backtest():
    r = client.post(
        "/api/v1/factor/backtest",
        json={"codes": ["MOM_RET_20", "TECH_RSI_14"], "weights": [0.5, 0.5]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "longShortReturn" in body
    assert body["sampleSize"] > 0


def test_backtest_length_mismatch():
    r = client.post(
        "/api/v1/factor/backtest",
        json={"codes": ["MOM_RET_20"], "weights": [0.5, 0.5]},
    )
    assert r.status_code == 400
