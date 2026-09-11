"""D-1 回归测试：超额收益窗口必须真的按 20/60 交易日计算

背景（2026-09-10 修复前）：
    个股端 ``ORDER BY timestamp ASC LIMIT 1``、基准端 ``LIMIT 2`` + ``rows[-1]/rows[0]``
    —— **EXCESS_WINDOWS 根本没参与计算**，取到的永远是「提及日之后第一个交易日」，
    于是 ``avg_excess_20d ≡ avg_excess_60d ≡ 次日收益``。实测 14/14 画像两值完全相同。

本文件用可手算的假行情锁死正确口径：
    1. 20d 与 60d 必须不同（回归核心）
    2. 数值等于手算结果（线性价格 + 零收益基准）
    3. 个股停牌时窗口按**交易日历**截止，不被悄悄拉长
    4. 行情不足 w 个交易日时该窗口为 None，而不是退回次日收益
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.intel.aggregate.profile import (
    BENCHMARK_SYMBOL,
    EXCESS_WINDOWS,
    _calc_excess_returns,
)

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _mk(symbol, prices, start=T0, skip=()):
    """prices: 按日历日序排列的价格列表；skip: 要跳过（停牌）的日序"""
    rows = []
    for i, p in enumerate(prices):
        if i in skip:
            continue
        rows.append(
            {
                "symbol": symbol,
                "timestamp": start + timedelta(days=i),
                "close": float(p),
                "hfq_close": float(p),
                "adj_factor": None,
            }
        )
    return rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        params = params or {}
        sym = params.get("sym")
        rows = self.data["bench"] if sym == BENCHMARK_SYMBOL else self.data["bars"].get(sym, [])
        start, end = params.get("start"), params.get("end")
        out = [
            r
            for r in rows
            if (start is None or r["timestamp"] >= start)
            and (end is None or r["timestamp"] <= end)
        ]
        return _FakeResult(out)


class _FakeEngine:
    def __init__(self, bars, bench):
        self.data = {"bars": bars, "bench": bench}

    def connect(self):
        return _FakeConn(self.data)


def _engine(stock_prices, bench_price=100.0, n_days=200, skip=(), symbol="600TEST.SH"):
    """基准恒定（收益 0）⇒ 超额收益 = 个股收益，便于手算"""
    bench = _mk(BENCHMARK_SYMBOL, [bench_price] * n_days)
    return _Engine(bars={symbol: _mk(symbol, stock_prices, skip=skip)}, bench=bench)


def _Engine(bars, bench):  # noqa: N802 - 保持与上面一致的构造入口
    return _FakeEngine(bars, bench)


# ---------------------------------------------------------------------------
# 1. 核心回归：20d 与 60d 必须不同
# ---------------------------------------------------------------------------

def test_windows_are_not_identical():
    """修复前两者恒等（都等于次日收益 +1%），修复后必须显著不同"""
    # 线性上涨：第 i 日 = 10 + 0.1*i ⇒ 20d +20%，60d +60%，次日 +1%
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _engine(prices)
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)

    assert res["avg_excess_20d"] == 20.0, res
    assert res["avg_excess_60d"] == 60.0, res
    assert res["avg_excess_20d"] != res["avg_excess_60d"]


def test_windows_not_next_day_return():
    """显式排除「仍是次日收益」这一退化情形"""
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _engine(prices)
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)
    next_day = round((prices[1] / prices[0] - 1) * 100, 2)  # +1.0%
    assert res["avg_excess_20d"] != next_day
    assert res["avg_excess_60d"] != next_day


def test_win_rate_uses_window_not_next_day():
    """下跌标的：60d 胜率应为 0（不是按次日算出来的 0/1 摇摆）"""
    prices = [10 - 0.05 * i for i in range(200)]  # 单调下跌
    eng = _engine(prices)
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)
    assert res["win_rate_20d"] == 0.0
    assert res["win_rate_60d"] == 0.0
    assert res["avg_excess_20d"] < 0 and res["avg_excess_60d"] < 0


# ---------------------------------------------------------------------------
# 2. 基准收益参与计算
# ---------------------------------------------------------------------------

def test_benchmark_is_subtracted_over_same_window():
    """基准同窗口上涨 10% ⇒ 超额 = 个股收益 − 10%"""
    n = 200
    stock = [10 + 0.1 * i for i in range(n)]          # 20d +20%, 60d +60%
    bench = [100 * (1.10 ** (i / 20)) for i in range(n)]  # 每 20 日 +10%
    eng = _FakeEngine(
        bars={"600TEST.SH": _mk("600TEST.SH", stock)},
        bench=_mk(BENCHMARK_SYMBOL, bench),
    )
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)
    # 基准 20d = +10%，60d = +33.1% ⇒ 超额 20d ≈ +10%，60d ≈ +26.9%
    assert res["avg_excess_20d"] == pytest.approx(20 - 10, abs=0.05)
    assert res["avg_excess_60d"] == pytest.approx(60 - 33.1, abs=0.2)


def test_no_benchmark_degrades_to_absolute_return():
    """基准序列为空时退化为绝对收益，但仍按窗口取，不退回次日"""
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _FakeEngine(bars={"600TEST.SH": _mk("600TEST.SH", prices)}, bench=[])
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)
    assert res["avg_excess_20d"] == 20.0
    assert res["avg_excess_60d"] == 60.0


# ---------------------------------------------------------------------------
# 3. 交易日历对齐：停牌不拉长窗口
# ---------------------------------------------------------------------------

def test_suspension_does_not_stretch_window():
    """个股在 d18~d22 停牌：20 日窗口应截止到日历 d20 ⇒ 取到 d17 的价格 11.7

    若按「bar 数偏移」实现，会取到第 20 根 bar（≈ d25，价格 12.5），窗口被拉长。
    """
    prices = [10 + 0.1 * i for i in range(200)]
    skip = set(range(18, 23))  # d18..d22 停牌
    eng = _engine(prices, skip=skip)
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)

    # 日历 d20 之前最后一根是 d17 ⇒ (11.7/10 - 1) = +17%
    assert res["avg_excess_20d"] == 17.0, res
    # 60 日窗口内无停牌影响 ⇒ 正常 +60%
    assert res["avg_excess_60d"] == 60.0, res


# ---------------------------------------------------------------------------
# 4. 样本不足：窗口为 None，而不是退回次日收益
# ---------------------------------------------------------------------------

def test_insufficient_history_yields_none():
    """提及日距行情末尾不足 20/60 个交易日 ⇒ 对应窗口为 None"""
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _engine(prices, n_days=200)
    late = T0 + timedelta(days=195)  # 只剩 5 个交易日
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": late}], eng)
    assert res["avg_excess_20d"] is None
    assert res["avg_excess_60d"] is None
    assert res["accuracy_sample_size"] == 0


def test_60d_drops_when_only_30d_available():
    """只剩 30 个交易日 ⇒ 20d 有值、60d 为 None（两个窗口样本量独立）"""
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _engine(prices, n_days=200)
    pub = T0 + timedelta(days=170)  # 剩 30 个交易日
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": pub}], eng)
    assert res["avg_excess_20d"] is not None
    assert res["avg_excess_60d"] is None
    assert res["accuracy_sample_size"] == 1


def test_published_after_all_bars_is_skipped():
    pub = T0 + timedelta(days=500)
    eng = _engine([10 + 0.1 * i for i in range(200)], n_days=200)
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": pub}], eng)
    assert res["accuracy_sample_size"] == 0
    assert res["avg_excess_20d"] is None


# ---------------------------------------------------------------------------
# 5. 入参与兜底
# ---------------------------------------------------------------------------

def test_empty_rows_returns_blank_result():
    res = _calc_excess_returns([], _engine([10.0] * 200))
    assert res["accuracy_sample_size"] == 0
    for w in EXCESS_WINDOWS:
        assert res[f"avg_excess_{w}d"] is None
        assert res[f"win_rate_{w}d"] is None


def test_missing_published_at_is_skipped():
    eng = _engine([10 + 0.1 * i for i in range(200)])
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": None}], eng)
    assert res["accuracy_sample_size"] == 0


def test_string_published_at_is_accepted():
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _engine(prices)
    res = _calc_excess_returns(
        [{"symbol": "600TEST.SH", "published_at": T0.isoformat()}], eng
    )
    assert res["avg_excess_20d"] == 20.0


def test_hfq_missing_falls_back_to_close_without_crashing():
    """hfq_close 缺失时降级用 close（D-2 兜底），不应抛异常"""
    n = 200
    bars = _mk("600TEST.SH", [10 + 0.1 * i for i in range(n)])
    for b in bars:
        b["hfq_close"] = None
    eng = _FakeEngine(
        bars={"600TEST.SH": bars}, bench=_mk(BENCHMARK_SYMBOL, [100.0] * n)
    )
    res = _calc_excess_returns([{"symbol": "600TEST.SH", "published_at": T0}], eng)
    assert res["avg_excess_20d"] == 20.0  # 用 close 也能算出同样的值


def test_multiple_mentions_are_averaged():
    prices = [10 + 0.1 * i for i in range(200)]
    eng = _engine(prices)
    rows = [
        {"symbol": "600TEST.SH", "published_at": T0},
        {"symbol": "600TEST.SH", "published_at": T0 - timedelta(days=1)},
    ]
    res = _calc_excess_returns(rows, eng)
    assert res["accuracy_sample_size"] == 2
    # 第二天提及少一天数据，窗口值略有差异，但两者都应远离 0
    assert res["avg_excess_20d"] > 15
    assert res["avg_excess_60d"] > 55
