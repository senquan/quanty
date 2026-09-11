"""P3-4 消费侧单测：可选依赖降级 + 防前视 SQL 契约

覆盖 plan §5 P3-4 与 P3 验收第 3 条：
  - intel 停跑 / 表缺失 / 查询异常 → 返回空结果 + warning，**绝不抛异常**
  - 读取必须按 ``available_at <= as_of`` 过滤（防前视），且 as_of 正确下推到 SQL
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from app.intel.factorize import consumer as C


# --------------------------------------------------------------------------
# 假引擎：记录执行的 SQL 与参数，返回可控行（无需真库）
# --------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConn:
    def __init__(self, rec):
        self.rec = rec

    def execute(self, sql, params=None):
        self.rec["sql"] = str(sql)
        self.rec["params"] = params or {}
        return _FakeResult(self.rec.get("rows", []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, rows=None):
        self.rec = {"rows": rows or []}

    def connect(self):
        return _FakeConn(self.rec)


@pytest.fixture(autouse=True)
def _reset_cache():
    C.reset_table_cache()
    yield
    C.reset_table_cache()


# --------------------------------------------------------------------------
# 1. 可选依赖降级
# --------------------------------------------------------------------------
def test_load_panel_unavailable_returns_empty_without_raise(monkeypatch):
    """表不可用时：返回空面板（列齐全）、不抛异常"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: False)

    out = C.load_factor_panel(codes=["INTL_MENTION_HEAT_5"], as_of="2026-09-08")

    assert out.empty
    assert list(out.columns) == C.PANEL_COLUMNS


def test_load_panel_query_error_degrades_instead_of_raise(monkeypatch):
    """查询抛异常时：降级为空面板，不向上抛"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)

    class _Boom:
        def connect(self):
            raise RuntimeError("connection refused")

    out = C.load_factor_panel(codes=["INTL_SENTIMENT_10"], engine=_Boom())
    assert out.empty


def test_merge_unavailable_keeps_columns_and_warns(monkeypatch):
    """intel 缺席：INTL_* 列仍然存在（值 NaN）+ 给出 warning，主链路继续"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: False)

    df = pd.DataFrame(
        {"symbol": ["600519.SH", "000001.SZ"], "trade_date": ["2026-09-07", "2026-09-08"]}
    )
    out, warns = C.merge_intel_factors(df, codes=["INTL_MENTION_HEAT_5"])

    assert "INTL_MENTION_HEAT_5" in out.columns
    assert out["INTL_MENTION_HEAT_5"].isna().all()
    assert len(out) == 2
    assert any("不可用" in w for w in warns)


def test_merge_missing_required_column_warns():
    """面板缺 symbol/trade_date：给 warning 并原样返回，不炸"""
    df = pd.DataFrame({"close": [1.0, 2.0]})
    out, warns = C.merge_intel_factors(df)
    assert out is df or out.equals(df)
    assert any("缺少" in w for w in warns)


# --------------------------------------------------------------------------
# 2. 防前视 SQL 契约
# --------------------------------------------------------------------------
def test_as_of_is_pushed_down_as_antilookahead_filter(monkeypatch):
    """核心：as_of 必须以 available_at 过滤下推到 SQL（防前视）"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)
    eng = _FakeEngine()

    C.load_factor_panel(codes=["INTL_MENTION_HEAT_5"], as_of="2026-09-08 09:30", engine=eng)

    assert "available_at <= CAST(:as_of AS timestamptz)" in eng.rec["sql"]
    assert eng.rec["params"]["as_of"] == datetime(2026, 9, 8, 9, 30)


def test_no_as_of_means_no_antilookahead_filter(monkeypatch):
    """不传 as_of 时不加可见性过滤（调用方显式负责）"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)
    eng = _FakeEngine()

    C.load_factor_panel(codes=["INTL_RESONANCE_5"], engine=eng)

    # 注意：SELECT 列表里也有 available_at 列，故只断言过滤条件不存在
    assert "available_at <=" not in eng.rec["sql"]
    assert "as_of" not in eng.rec["params"]


def test_date_as_of_normalized_to_midnight(monkeypatch):
    """date 型 as_of 按当日 00:00 处理（严格口径）"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)
    eng = _FakeEngine()

    C.load_factor_panel(as_of=date(2026, 9, 8), engine=eng)

    assert eng.rec["params"]["as_of"] == datetime(2026, 9, 8, 0, 0)


def test_prompt_version_none_disables_version_filter(monkeypatch):
    """prompt_version=None 表示不限制版本（避免隐式混版需显式声明）"""
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)
    eng = _FakeEngine()

    C.load_factor_panel(prompt_version=None, engine=eng)
    assert "prompt_version = :pv" not in eng.rec["sql"]

    eng2 = _FakeEngine()
    C.load_factor_panel(prompt_version="v2", engine=eng2)
    assert "prompt_version = :pv" in eng2.rec["sql"]


# --------------------------------------------------------------------------
# 3. 形态与合并正确性
# --------------------------------------------------------------------------
def test_empty_panel_has_stable_columns():
    assert list(C._empty_panel().columns) == C.PANEL_COLUMNS


def test_to_wide_shape(monkeypatch):
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)
    rows = [
        {
            "symbol": "600519.SH",
            "trade_date": "2026-09-07",
            "factor_code": "INTL_MENTION_HEAT_5",
            "value": 2.0,
            "available_at": datetime(2026, 9, 7, 8, 0),
            "factor_version": "v1",
            "prompt_version": "v2",
        },
        {
            "symbol": "000001.SZ",
            "trade_date": "2026-09-07",
            "factor_code": "INTL_MENTION_HEAT_5",
            "value": 1.0,
            "available_at": datetime(2026, 9, 7, 8, 0),
            "factor_version": "v1",
            "prompt_version": "v2",
        },
    ]
    panel = C.load_factor_panel(engine=_FakeEngine(rows=rows))
    wide = C.to_wide(panel, "INTL_MENTION_HEAT_5")

    assert list(wide.columns) == ["000001.SZ", "600519.SH"]
    assert wide.loc[pd.Timestamp("2026-09-07"), "600519.SH"] == 2.0


def test_merge_fills_values_by_symbol_and_date(monkeypatch):
    monkeypatch.setattr(C, "intel_factors_available", lambda engine=None: True)
    rows = [
        {
            "symbol": "600519.SH",
            "trade_date": "2026-09-07",
            "factor_code": "INTL_MENTION_HEAT_5",
            "value": 3.0,
            "available_at": datetime(2026, 9, 7, 8, 0),
            "factor_version": "v1",
            "prompt_version": "v2",
        }
    ]
    df = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "trade_date": ["2026-09-07", "2026-09-07"],
            "close": [10.0, 20.0],
        }
    )
    out, _warns = C.merge_intel_factors(
        df, codes=["INTL_MENTION_HEAT_5"], engine=_FakeEngine(rows=rows)
    )

    assert out.loc[0, "INTL_MENTION_HEAT_5"] == 3.0
    assert pd.isna(out.loc[1, "INTL_MENTION_HEAT_5"])
    assert "close" in out.columns  # 原有列保留


def test_all_intel_codes_are_known():
    """因子代码集合与生产侧一致（防止改名后消费侧静默失配）"""
    assert set(C.INTEL_FACTOR_CODES) == {
        "INTL_MENTION_HEAT_5",
        "INTL_SENTIMENT_10",
        "INTL_RESONANCE_5",
        "INTL_FIRST_MENTION",
        "INTL_AUTHOR_CONVICTION",
        "INTL_STYLE_MATCH",
    }
