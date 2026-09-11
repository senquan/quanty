"""AlphaFeed 接入层 ex_factors -> adj_factor/hfq_close 映射的单测（mock httpx，无需联网）

覆盖：
1. 复权因子 ffill 映射：bar 取「不晚于其日期的最近一次除权因子」。
2. hfq_close 公式：close * (f_latest / f_first)，与 Tushare 接入层语义对齐。
3. 降级：ex-factors 限频(429) 或无数据时，adj_factor/hfq_close 置空，qfq close 仍正常。
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest

from app.core.config import settings
from app.ingestion.alphafeed_source import AlphafeedSource


def _ms(y: int, m: int, d: int) -> int:
    # 与源文件一致：naive datetime -> 本地时间戳(ms)，源文件再 fromtimestamp 还原，可往返
    return int(datetime(y, m, d).timestamp() * 1000)


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)[:200]

    def json(self):
        return self._payload


def _router(responses: dict):
    """responses: {url子串: _FakeResp}；按插入顺序匹配（ex-factors 须先于 /v1/klines）。"""

    def _get(url, params=None, headers=None):
        for key, resp in responses.items():
            if key in url:
                return resp
        raise AssertionError(f"未预期的请求 URL: {url}")

    client = MagicMock()
    client.get.side_effect = _get
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


def _patch_client(responses: dict):
    client = _router(responses)
    return patch("app.ingestion.alphafeed_source.httpx.Client", return_value=client)


# 场景：两次除权
#   2023-06-01 除权，ex_factor=1.0（首日基准）
#   2024-01-02 除权，ex_factor=1.5（之后因子跳变）
# bar1 在 2023-06-02（取 1.0），bar2 在 2024-01-03（取 1.5）
A, B = _ms(2023, 6, 1), _ms(2024, 1, 2)
BAR1, BAR2 = _ms(2023, 6, 2), _ms(2024, 1, 3)
KLINES = {
    "data": {
        "timestamp": [BAR1, BAR2],
        "open": [100.0, 110.0],
        "high": [101.0, 111.0],
        "low": [99.0, 109.0],
        "close": [100.0, 110.0],
        "volume": [1000.0, 1100.0],
    }
}
EX = {
    "data": {
        "600519.SH": [
            {"timestamp": A, "ex_factor": 1.0},
            {"timestamp": B, "ex_factor": 1.5},
        ]
    }
}


@patch.object(AlphafeedSource, "_key", return_value="test-key")
def test_ex_factor_mapping_and_hfq(_):
    with _patch_client({"ex-factors": _FakeResp(200, EX), "/v1/klines": _FakeResp(200, KLINES)}):
        df = AlphafeedSource().fetch("600519.SH", "2023-01-01", "2024-12-31")

    assert len(df) == 2
    # bar1：因子 1.0；hfq = 100 * (1.5/1.0) = 150
    r1 = df.iloc[0]
    assert r1["adj_factor"] == pytest.approx(1.0)
    assert r1["hfq_close"] == pytest.approx(150.0)
    assert r1["close"] == pytest.approx(100.0)  # qfq 不变
    # bar2：因子 1.5；hfq = 110 * 1.5 = 165
    r2 = df.iloc[1]
    assert r2["adj_factor"] == pytest.approx(1.5)
    assert r2["hfq_close"] == pytest.approx(165.0)
    assert r2["close"] == pytest.approx(110.0)


@patch.object(AlphafeedSource, "_key", return_value="test-key")
def test_ex_factor_429_degrades_to_none(_):
    # ex-factors 触发限频 -> 降级：adj_factor/hfq_close 置空，qfq close 仍在
    with _patch_client(
        {"ex-factors": _FakeResp(429, {"retry_after_ms": 1000}), "/v1/klines": _FakeResp(200, KLINES)}
    ):
        df = AlphafeedSource().fetch("600519.SH", "2023-01-01", "2024-12-31")

    assert len(df) == 2
    assert df["adj_factor"].isna().all()
    assert df["hfq_close"].isna().all()
    assert df["close"].tolist() == pytest.approx([100.0, 110.0])


@patch.object(AlphafeedSource, "_key", return_value="test-key")
def test_ex_factor_empty_degrades_to_none(_):
    # ex-factors 返回无数据 -> 同样降级
    with _patch_client(
        {"ex-factors": _FakeResp(200, {"data": {}}), "/v1/klines": _FakeResp(200, KLINES)}
    ):
        df = AlphafeedSource().fetch("600519.SH", "2023-01-01", "2024-12-31")

    assert df["adj_factor"].isna().all()
    assert df["hfq_close"].isna().all()
    assert len(df) == 2


@patch.object(AlphafeedSource, "_key", return_value="test-key")
def test_quality_filter_still_applied(_):
    # high<low 的脏行应被剔除（质量过滤不被复权逻辑影响）
    dirty = {
        "data": {
            "timestamp": [BAR1],
            "open": [100.0],
            "high": [90.0],  # high < low
            "low": [99.0],
            "close": [100.0],
            "volume": [1000.0],
        }
    }
    with _patch_client({"ex-factors": _FakeResp(200, EX), "/v1/klines": _FakeResp(200, dirty)}):
        df = AlphafeedSource().fetch("600519.SH", "2023-01-01", "2024-12-31")
    assert len(df) == 0
