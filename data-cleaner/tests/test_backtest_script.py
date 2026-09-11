"""dc 脚本策略回测回归测试（R2）

覆盖三部分：
1. **迁移保真** —— backend 已修的撮合 / on_data / 胜率口径，搬到 dc 后行为不变
   （成交时间、两遍执行、FIFO、整手、涨跌停、费用）
2. **dc 新增** —— 停牌拒单（volume<=0）、复权口径（qfq / hfq 缩放）、
   闸口的 A 股归口与两段式样本判定
3. **服务层** —— 校验 → 闸口 → 取数 → 撮合 → 指标 全链路

运行：cd data-cleaner && .venv/Scripts/python.exe -m pytest tests/test_backtest_script.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.data import BacktestDataError, load_bars
from app.backtest.engine import BacktestEngine
from app.backtest.gate import Plan, Refusal, check_sample, plan_backtest
from app.backtest.market_rules import (
    MARKETS,
    a_share_limit_pct,
    calc_fees,
    classify_symbol,
    normalize_symbol,
)
from app.backtest.service import BacktestRefused, ScriptBacktestRequest, run_script_backtest
from app.backtest.validator import StrategyValidator

A = MARKETS["a_share"]


# ── 辅助 ────────────────────────────────────────────────

def make_bars(close_values, start="2023-01-01", volume=1_000_000.0):
    """用给定收盘价序列构造日线 DataFrame。"""
    close = np.asarray(close_values, dtype=float)
    idx = pd.date_range(start, periods=len(close), freq="B")
    vol = np.asarray(volume, dtype=float)
    if vol.ndim == 0:
        vol = np.full(len(close), float(vol))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": vol,
        },
        index=idx,
    )


def raw_bars(close_values, start="2023-01-01", hfq_factor=None, with_hfq=True):
    """模拟 factor.raw_bars 的返回形态（timestamp 是列，不是索引）。"""
    close = np.asarray(close_values, dtype=float)
    df = pd.DataFrame(
        {
            "symbol": "600519.SH",
            "timestamp": pd.date_range(start, periods=len(close), freq="B"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(len(close), 1_000_000.0),
            "source": "pandadata",
            "freq": "1d",
        }
    )
    if with_hfq:
        df["hfq_close"] = close * (hfq_factor or 1.0)
    return df


def run(code, bars, capital=100_000, market=None, symbol=None):
    engine = BacktestEngine(initial_capital=capital, market=market, symbol=symbol)
    results = engine.execute_strategy(code, bars)
    return engine, results, engine.calculate_metrics(results)


BUY_AND_HOLD = """
def on_data(data, context):
    close = data['close']
    buy(close.iloc[0])
""".strip()


MA_CROSS = """
# 双均线交叉策略
def on_data(data, context):
    import pandas as pd

    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()

    for i in range(50, len(close)):
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
""".strip()


# ── 一、市场规则 ────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("600519", "600519.SH"),
    ("688981", "688981.SH"),
    ("000001", "000001.SZ"),
    ("301237", "301237.SZ"),
    ("920808", "920808.BJ"),
    ("600519.SH", "600519.SH"),
    ("600519.SS", "600519.SH"),
    ("000001.sz", "000001.SZ"),
])
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["AAPL", "00700.HK", "BTC/USDT", "", None, "12345"])
def test_normalize_rejects_non_a_share(raw):
    assert normalize_symbol(raw) is None


def test_classify_non_a_share_returns_none():
    """dc 只有 A 股：认不出的代码必须由闸口转成拒绝，不能默认放行。"""
    assert classify_symbol("600519.SH") is A
    assert classify_symbol("AAPL") is None
    assert classify_symbol("00700.HK") is None


@pytest.mark.parametrize("symbol,pct", [
    ("600519.SH", 0.10),   # 主板
    ("000001.SZ", 0.10),   # 主板
    ("300750.SZ", 0.20),   # 创业板
    ("688981.SH", 0.20),   # 科创板
    ("689009.SH", 0.20),   # 科创板 CDR（九号公司）—— 曾按主板误判
    ("920808.BJ", 0.30),   # 北交所
])
def test_a_share_limit_pct(symbol, pct):
    assert a_share_limit_pct(symbol) == pytest.approx(pct)


def test_calc_fees_a_share():
    """A 股：佣金万 2.5（最低 5 元）+ 过户费万 0.1 双边 + 印花税万 5 卖出单边。"""
    amount = 100_000.0
    buy_fee = calc_fees("buy", amount, A)
    sell_fee = calc_fees("sell", amount, A)
    assert buy_fee == pytest.approx(25.0 + 1.0)        # 佣金 25 + 过户费 1
    assert sell_fee == pytest.approx(25.0 + 1.0 + 50.0)  # + 印花税 50
    # 小额成交时佣金取最低 5 元（过户费万 0.1 = 0.001）
    assert calc_fees("buy", 100.0, A) == pytest.approx(5.0 + 0.001)


def test_calc_fees_none_market():
    assert calc_fees("buy", 100_000.0, None) == 0.0


# ── 二、闸口 ────────────────────────────────────────────

def _plan(**kw):
    payload = dict(
        symbol="600519.SH", start="2021-01-01", end="2026-01-01", style="swing",
        initial_capital=100_000.0,
    )
    payload.update(kw)
    return plan_backtest(**payload)


def test_gate_accepts_valid_request():
    p = _plan()
    assert isinstance(p, Plan)
    assert p.symbol == "600519.SH"
    assert p.market is A
    assert p.price_field == "qfq"
    assert p.data_source == "factor.raw_bars"
    assert p.limit_pct == pytest.approx(0.10)
    assert any("T+1" in x for x in p.limits)
    assert any("印花税" in x for x in p.limits)


def test_gate_refuses_missing_symbol():
    r = _plan(symbol="")
    assert isinstance(r, Refusal) and not r


def test_gate_refuses_non_a_share():
    """dc 没有港美股行情 —— 必须明确说，而不是给一份跑不出数据的 Plan。"""
    r = _plan(symbol="AAPL")
    assert isinstance(r, Refusal)
    assert "A股" in r.remedy


def test_gate_refuses_intraday():
    r = _plan(style="intraday")
    assert isinstance(r, Refusal)
    assert "分钟" in r.reason


def test_gate_refuses_unknown_style():
    assert isinstance(_plan(style="martingale"), Refusal)


def test_gate_refuses_short_window():
    r = _plan(start="2026-01-01", end="2026-03-01")
    assert isinstance(r, Refusal)
    assert "240" in r.reason


def test_gate_refuses_allow_short():
    r = _plan(allow_short=True)
    assert isinstance(r, Refusal)
    assert "做空" in r.reason


def test_gate_refuses_tiny_capital():
    r = _plan(initial_capital=5_000)
    assert isinstance(r, Refusal)
    assert "一手" in r.reason


def test_gate_refuses_bad_price_field():
    assert isinstance(_plan(price_field="raw"), Refusal)


def test_gate_refuses_reversed_dates():
    assert isinstance(_plan(start="2026-01-01", end="2021-01-01"), Refusal)


def test_check_sample_uses_real_bar_count():
    """取数后按真实 bar 数复核 —— 区间够长不代表实际有那么多 bar。"""
    p = _plan()
    assert check_sample(p, 300) is None
    r = check_sample(p, 100)
    assert isinstance(r, Refusal) and "100" in r.reason


def test_check_sample_refuses_empty():
    r = check_sample(_plan(), 0)
    assert isinstance(r, Refusal) and "没有行情数据" in r.reason


def test_plan_dict_and_engine_config():
    p = _plan()
    d = p.as_dict()
    assert d["symbol"] == "600519.SH" and d["market"] == "a_share"
    cfg = p.to_engine_config()
    assert set(cfg) == {"symbol", "market", "allow_short"}
    BacktestEngine(initial_capital=100_000, **cfg)  # 不能炸


# ── 三、取数与复权 ──────────────────────────────────────

def test_load_bars_qfq_keeps_price():
    df, meta, warns = load_bars("600519.SH", loader=lambda s, a, b: raw_bars([10.0] * 30))
    assert meta["price_field"] == "qfq"
    assert meta["bars"] == 30
    assert df["close"].iloc[0] == pytest.approx(10.0)


def test_load_bars_hfq_scales_ohlc():
    """hfq 模式下 OHLC 必须按同一个 k 缩放 —— 只缩放 close 会让 ATR / 布林带失真。"""
    k = 3.0
    df, meta, warns = load_bars(
        "600519.SH", price_field="hfq", loader=lambda s, a, b: raw_bars([10.0] * 30, hfq_factor=k)
    )
    assert meta["price_field"] == "hfq"
    assert meta["hfq_factor"] == pytest.approx(k)
    assert df["close"].iloc[0] == pytest.approx(30.0)
    assert df["high"].iloc[0] == pytest.approx(30.0 * 1.01)
    assert df["low"].iloc[0] == pytest.approx(30.0 * 0.99)
    assert any("后复权" in w for w in warns)


def test_load_bars_hfq_missing_falls_back_to_qfq():
    df, meta, warns = load_bars(
        "600519.SH", price_field="hfq",
        loader=lambda s, a, b: raw_bars([10.0] * 30, with_hfq=False),
    )
    assert meta["price_field"] == "qfq"
    assert df["close"].iloc[0] == pytest.approx(10.0)
    assert any("退回前复权" in w for w in warns)


def test_load_bars_empty_raises():
    with pytest.raises(BacktestDataError):
        load_bars("600519.SH", loader=lambda s, a, b: pd.DataFrame())


def test_load_bars_missing_column_raises():
    with pytest.raises(BacktestDataError):
        load_bars("600519.SH", loader=lambda s, a, b: pd.DataFrame({"timestamp": [], "foo": []}))


def test_load_bars_drops_bad_close():
    raw = raw_bars([10.0] * 10)
    raw.loc[0, "close"] = 0.0
    df, meta, warns = load_bars("600519.SH", loader=lambda s, a, b: raw)
    assert meta["bars"] == 9
    assert any("收盘价缺失" in w for w in warns)


# ── 四、引擎（迁移保真） ────────────────────────────────

def test_buy_and_hold_tracks_price_up():
    _, _, m = run(BUY_AND_HOLD, make_bars(np.linspace(100, 150, 200)))
    assert m["total_return"] == pytest.approx(50.0, abs=0.5)
    assert m["total_trades"] == 1


def test_buy_and_hold_tracks_price_down():
    _, _, m = run(BUY_AND_HOLD, make_bars(np.linspace(150, 100, 200)))
    assert m["total_return"] == pytest.approx(-33.33, abs=0.5)


def test_portfolio_value_moves_with_position():
    _, res, _ = run(BUY_AND_HOLD, make_bars(np.linspace(100, 150, 200)))
    assert len(set(round(float(v), 2) for v in res["portfolio_values"])) > 10


def test_trade_timestamps_are_bar_times():
    """成交时间必须是 bar 时间，不是 now()。"""
    bars = make_bars(120.0 + 20.0 * np.sin(2 * np.pi * np.arange(260) / 60.0))
    _, res, _ = run(MA_CROSS, bars)
    assert res["trades"]
    lo, hi = bars.index[0], bars.index[-1]
    stamps = [t["timestamp"] for t in res["trades"]]
    assert all(lo <= t <= hi for t in stamps)
    assert stamps == sorted(stamps)


def test_on_data_entrypoint_is_invoked():
    bars = make_bars(120.0 + 20.0 * np.sin(2 * np.pi * np.arange(260) / 60.0))
    _, res, _ = run(MA_CROSS, bars)
    assert len(res["trades"]) > 0


def test_on_data_single_param():
    code = "def on_data(data):\n    buy(data['close'].iloc[0])"
    _, res, _ = run(code, make_bars(np.linspace(100, 120, 100)))
    assert len(res["trades"]) == 1


def test_module_level_strategy_still_works():
    code = """
close = data['close']
ma5 = close.rolling(5).mean()
ma20 = close.rolling(20).mean()
for i in range(20, len(close)):
    if ma5.iloc[i-1] <= ma20.iloc[i-1] and ma5.iloc[i] > ma20.iloc[i]:
        buy(close.iloc[i])
    elif ma5.iloc[i-1] >= ma20.iloc[i-1] and ma5.iloc[i] < ma20.iloc[i]:
        if get_position() > 0:
            sell(close.iloc[i], get_position())
""".strip()
    bars = make_bars(120.0 + 20.0 * np.sin(2 * np.pi * np.arange(260) / 60.0))
    _, res, _ = run(code, bars)
    assert len(res["trades"]) > 0


def test_round_trips_fifo_pairing():
    engine = BacktestEngine()
    trades = [
        {"type": "buy", "price": 10.0, "quantity": 100, "timestamp": None, "bar": 0},
        {"type": "buy", "price": 20.0, "quantity": 100, "timestamp": None, "bar": 1},
        {"type": "sell", "price": 15.0, "quantity": 100, "timestamp": None, "bar": 2},
    ]
    trips = engine._round_trips(trades)
    assert len(trips) == 1
    assert trips[0]["entry_price"] == pytest.approx(10.0)
    assert trips[0]["pnl"] == pytest.approx(500.0)


def test_win_rate_counts_profitable_closes_only():
    engine = BacktestEngine()
    trades = [
        {"type": "buy", "price": 10.0, "quantity": 100, "timestamp": None, "bar": 0},
        {"type": "sell", "price": 12.0, "quantity": 100, "timestamp": None, "bar": 1},
        {"type": "buy", "price": 10.0, "quantity": 100, "timestamp": None, "bar": 2},
        {"type": "sell", "price": 8.0, "quantity": 100, "timestamp": None, "bar": 3},
    ]
    assert engine._calculate_win_rate(trades) == pytest.approx(50.0)


def test_win_rate_ignores_open_position():
    engine = BacktestEngine()
    trades = [
        {"type": "buy", "price": 10.0, "quantity": 100, "timestamp": None, "bar": 0},
        {"type": "sell", "price": 12.0, "quantity": 100, "timestamp": None, "bar": 1},
        {"type": "buy", "price": 10.0, "quantity": 100, "timestamp": None, "bar": 2},
    ]
    assert engine._calculate_win_rate(trades) == pytest.approx(100.0)


def test_no_signals_yields_zero_return():
    _, res, m = run("def on_data(data, context):\n    pass", make_bars(np.linspace(100, 150, 100)))
    assert m["total_return"] == 0.0
    assert set(round(float(v), 2) for v in res["portfolio_values"]) == {100_000.0}


def test_sell_without_position_is_skipped():
    _, res, _ = run(
        "def on_data(data, context):\n    sell(data['close'].iloc[0], 100)",
        make_bars(np.linspace(100, 150, 50)),
    )
    assert res["trades"] == []


def test_final_capital_reflects_open_position():
    _, res, m = run(BUY_AND_HOLD, make_bars(np.linspace(100, 150, 200)))
    assert res["final_capital"] < 100.0
    assert m["final_capital"] == pytest.approx(150_000, rel=0.01)


def test_empty_data_raises():
    with pytest.raises(ValueError):
        BacktestEngine().execute_strategy(BUY_AND_HOLD, pd.DataFrame())


# ── 五、引擎（市场规则：dc 侧新增停牌 + 迁移的 T+1/涨跌停/整手/费用） ──

def _run_a_share(code, bars, capital=100_000, symbol="600519.SH"):
    return run(code, bars, capital=capital, market=A, symbol=symbol)


def test_t1_blocks_same_day_sell():
    """A 股 T+1：当日买入的股份当日不可卖出，且必须被记进 rejections。"""
    code = """
def on_data(data, context):
    p = data['close'].iloc[0]
    buy(p, 100, bar=0)
    sell(p, 100, bar=0)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0] * 5))
    assert [t["type"] for t in res["trades"]] == ["buy"]
    assert any("T+1" in r["reason"] for r in res["rejections"])


def test_t1_allows_next_day_sell():
    code = """
def on_data(data, context):
    p = data['close'].iloc[0]
    buy(p, 100, bar=0)
    sell(p, 100, bar=1)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0] * 5))
    assert [t["type"] for t in res["trades"]] == ["buy", "sell"]
    assert res["rejections"] == []


def test_limit_up_blocks_buy():
    """涨停封板（前收 10 → 11.5，超过 ±10% 的 11.0）买不进。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[1], 100, bar=1)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0, 11.5, 11.5]))
    assert res["trades"] == []
    assert any("涨停" in r["reason"] for r in res["rejections"])


def test_limit_down_blocks_sell():
    """跌停封板卖不出。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[0], 100, bar=0)
    sell(data['close'].iloc[1], 100, bar=1)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0, 8.9, 8.9]))
    assert [t["type"] for t in res["trades"]] == ["buy"]
    assert any("跌停" in r["reason"] for r in res["rejections"])


def test_limit_pct_varies_by_board():
    """创业板 ±20%：前收 10 → 11.5 不算封板，能买进。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[1], 100, bar=1)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0, 11.5, 11.5]), symbol="300750.SZ")
    assert len(res["trades"]) == 1


def test_lot_size_rounds_down():
    """A 股 100 股整手：买 150 股实际成交 100 股。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[0], 150, bar=0)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0] * 3))
    assert res["trades"][0]["quantity"] == 100


def test_lot_size_below_one_lot_is_rejected_loudly():
    """不足一手：不静默丢弃，必须进 rejections。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[0], 50, bar=0)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0] * 3))
    assert res["trades"] == []
    assert any("最小交易单位" in r["reason"] for r in res["rejections"])


def test_suspended_bar_blocks_trading():
    """dc 新增：停牌日（volume <= 0）不撮合。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[1], 100, bar=1)
""".strip()
    bars = make_bars([10.0, 10.0, 10.0], volume=[1e6, 0.0, 1e6])
    _, res, _ = _run_a_share(code, bars)
    assert res["trades"] == []
    assert any("停牌" in r["reason"] for r in res["rejections"])


def test_fees_are_charged():
    """成交必须计费用 —— 否则回测结果系统性偏乐观。"""
    code = """
def on_data(data, context):
    buy(data['close'].iloc[0], 100, bar=0)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([10.0] * 3))
    assert res["total_fees"] == pytest.approx(5.01, abs=0.02)   # 佣金最低 5 + 过户费 0.01
    assert res["trades"][0]["fee"] > 0


def test_insufficient_capital_buying_one_lot():
    code = """
def on_data(data, context):
    buy(data['close'].iloc[0], 100, bar=0)
""".strip()
    _, res, _ = _run_a_share(code, make_bars([100.0] * 3), capital=100.0)
    assert res["trades"] == []
    assert any("资金不足" in r["reason"] for r in res["rejections"])


def test_market_rule_warnings_present():
    _, res, _ = _run_a_share(BUY_AND_HOLD, make_bars(np.linspace(10, 15, 60)))
    joined = " ".join(res["warnings"])
    assert "T+1" in joined and "印花税" in joined and "涨跌停" in joined


# ── 六、服务层全链路 ────────────────────────────────────

def _loader(n=400, symbol="600519.SH"):
    close = 20.0 + 5.0 * np.sin(2 * np.pi * np.arange(n) / 60.0)
    return lambda s, a, b: raw_bars(close, start="2020-01-01")


def _request(**kw):
    payload = dict(
        symbol="600519.SH",
        start="2020-01-01",
        end="2024-12-31",
        code=MA_CROSS,
        style="swing",
        initial_capital=200_000.0,
        loader=_loader(),
    )
    payload.update(kw)
    return ScriptBacktestRequest(**payload)


def test_service_end_to_end():
    res = run_script_backtest(_request())
    assert res["symbol"] == "600519.SH"
    assert res["data"]["bars"] == 400
    assert res["data"]["price_field"] == "qfq"
    assert len(res["portfolio_values"]) == 400
    assert len(res["portfolio_dates"]) == 400
    assert res["trades"], "双均线策略应当产生交易"
    assert res["plan"]["market"] == "a_share"
    assert res["limits"] and res["warnings"]
    assert res["total_fees"] > 0
    # JSON 可序列化
    import json
    json.dumps(res)


def test_hfq_and_qfq_agree_after_capital_scaling():
    """k 是常数 ⇒ 两条序列收益率相同；本金同比缩放后购买力等价，成交也应一致。

    这条防的是实测踩到的坑：茅台 k≈8.88，名义价被放大后同样的本金一手都买不起，
    hfq 模式 13 笔买单全部因「不足最小交易单位」被拒，成交 0 笔。
    """
    k = 8.88
    close = 1400.0 + 200.0 * np.sin(2 * np.pi * np.arange(400) / 60.0)
    loader = lambda s, a, b: raw_bars(close, start="2020-01-01", hfq_factor=k)  # noqa: E731

    q = run_script_backtest(_request(loader=loader, price_field="qfq"))
    h = run_script_backtest(_request(loader=loader, price_field="hfq"))

    assert h["data"]["hfq_factor"] == pytest.approx(k)
    assert q["trades"] and h["trades"], "两种口径都应有成交"
    assert h["total_trades"] == q["total_trades"]
    assert h["total_return"] == pytest.approx(q["total_return"], abs=1.0)
    # 金额已还原到真实量级，可直接与 qfq 对比
    assert h["final_capital"] == pytest.approx(q["final_capital"], rel=0.02)
    assert h["trades"][0]["price"] == pytest.approx(q["trades"][0]["price"], rel=1e-6)
    assert h["trades"][0]["quantity"] == q["trades"][0]["quantity"]


def test_service_hfq_end_to_end():
    res = run_script_backtest(_request(price_field="hfq"))
    assert res["data"]["price_field"] == "hfq"
    assert res["data"]["hfq_factor"] == pytest.approx(1.0)
    assert len(res["portfolio_values"]) == 400


def test_service_refuses_bad_code():
    with pytest.raises(BacktestRefused) as e:
        run_script_backtest(_request(code="import os\ndef on_data(d,c):\n    pass"))
    assert "校验未通过" in str(e.value)


def test_service_refuses_unknown_symbol():
    with pytest.raises(BacktestRefused) as e:
        run_script_backtest(_request(symbol="AAPL"))
    assert "A股" in e.value.as_dict()["remedy"]


def test_service_refuses_insufficient_bars():
    """区间够长、但实际只有 100 根 bar → 取数后的复核必须拦住。"""
    with pytest.raises(BacktestRefused) as e:
        run_script_backtest(_request(loader=_loader(n=100)))
    assert "100" in e.value.as_dict()["reason"]


def test_service_refuses_no_data():
    with pytest.raises(BacktestDataError):
        run_script_backtest(_request(loader=lambda s, a, b: pd.DataFrame()))


def test_service_broken_strategy_is_refusal_not_crash():
    """策略里访问不存在的列 → 闸口式拒绝，不是 500。"""
    with pytest.raises(BacktestRefused) as e:
        run_script_backtest(_request(code="def on_data(d,c):\n    buy(d['nope'].iloc[0])"))
    assert "策略执行失败" in e.value.as_dict()["reason"]


def test_service_without_market_rules():
    res = run_script_backtest(_request(apply_market_rules=False))
    assert any("关闭了市场规则" in w for w in res["warnings"])


# ── 七、策略校验（AST） ─────────────────────────────────

def test_validator_blocks_import_os():
    r = StrategyValidator().validate_strategy(
        "import os\ndef on_data(d,c):\n    os.system('rm -rf /')"
    )
    assert r["valid"] is False


def test_validator_blocks_importlib_bypass():
    r = StrategyValidator().validate_strategy(
        "import importlib\ndef on_data(d,c):\n    importlib.import_module('os')"
    )
    assert r["valid"] is False


def test_validator_blocks_dynamic_exec():
    assert StrategyValidator().validate_strategy(
        "def on_data(d,c):\n    eval('1+1')"
    )["valid"] is False


def test_validator_ignores_words_in_comments():
    code = """# 不使用 open 或 eval，只是注释里提到
def on_data(data, context):
    buy(data['close'].iloc[0])
""".strip()
    assert StrategyValidator().validate_strategy(code)["valid"] is True


def test_validator_allows_pandas_in_strategy():
    assert StrategyValidator().validate_strategy(MA_CROSS)["valid"] is True


def test_validator_rejects_syntax_error():
    r = StrategyValidator().validate_strategy("def on_data(data, context)\n    pass")
    assert r["valid"] is False and any("语法错误" in e for e in r["errors"])


def test_validator_flags_no_entrypoint():
    r = StrategyValidator().validate_strategy("def helper():\n    return 1")
    assert r["valid"] is False and any("入口" in e for e in r["errors"])


def test_validator_warns_without_trade_calls():
    r = StrategyValidator().validate_strategy("def on_data(data, context):\n    x = data['close'].mean()")
    assert r["valid"] is True and any("buy" in w for w in r["warnings"])
