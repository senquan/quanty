"""R4 冒烟：验证前端新消费的契约字段真在响应里。

R3 验的是「链路通不通」，R4 验的是「前端要读的每个字段都在」。
前端读不到的字段 = 页面上一片空白或 undefined，测试跑不出来。

用法：.venv/Scripts/python.exe _r4_smoke.py
"""
import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MA_CROSS = """
def on_data(data, context):
    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()
    for i in range(50, len(close)):
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            pos = get_position()
            if pos > 0:
                sell(close.iloc[i], pos)
""".strip()

# 前端 index.vue / api/quant.ts 实际会读的字段
REQUIRED = [
    "total_return", "sharpe_ratio", "max_drawdown", "win_rate", "total_trades",
    "final_capital", "cash", "final_position",
    "trades", "portfolio_values", "portfolio_dates", "daily_returns",
    "limits", "warnings", "rejections", "total_fees", "plan", "data",
]
REQUIRED_DATA = ["bars", "price_field", "first_date", "last_date"]


def check_styles() -> bool:
    resp = client.get("/api/v1/backtest/styles")
    print("=" * 72)
    print(f"GET /api/v1/backtest/styles  [{resp.status_code}]")
    if resp.status_code != 200:
        print("  失败：", resp.text[:200])
        return False
    body = resp.json()
    missing = [k for k in ("data_source", "symbol_hints", "styles", "price_fields")
               if k not in body]
    style_keys = [s["key"] for s in body["styles"]]
    pf_keys = [p["key"] for p in body["price_fields"]]
    print(f"  styles      : {style_keys}")
    print(f"  price_fields: {pf_keys}")
    print(f"  data_source : {body['data_source']}")
    print(f"  symbol_hints: {body['symbol_hints']}")
    # 每个 style 项要有前端 styleOptions 读的 5 个字段
    for s in body["styles"]:
        miss = [k for k in ("key", "label", "holding", "min_bars", "why_min") if k not in s]
        if miss:
            missing.append(f"styles[{s.get('key')}]缺{miss}")
    if missing:
        print("  缺字段：", missing)
        return False
    print("  ✓ 前端口径表契约齐备")
    return True


def check_backtest(symbol: str, price_field: str) -> bool:
    payload = {
        "symbol": symbol, "start": "2021-10-08", "end": "2026-09-04",
        "code": MA_CROSS, "style": "swing", "initial_capital": 1_000_000.0,
        "price_field": price_field,
    }
    resp = client.post("/api/v1/backtest/script", json=payload)
    print("=" * 72)
    print(f"POST /api/v1/backtest/script  {symbol} [{price_field}]  [{resp.status_code}]")
    if resp.status_code != 200:
        print("  失败：", json.dumps(resp.json(), ensure_ascii=False)[:300])
        return False

    body = resp.json()
    missing = [k for k in REQUIRED if k not in body]
    meta = body.get("data") or {}
    missing += [f"data.{k}" for k in REQUIRED_DATA if k not in meta]

    m = meta
    print(f"  bars={m.get('bars')}  {m.get('first_date')} ~ {m.get('last_date')}"
          f"  口径={m.get('price_field')}  k={m.get('hfq_factor')}")
    print(f"  总收益={body['total_return']}%  成交={body['total_trades']}"
          f"  期末价值={body['final_capital']}  现金={body['cash']}"
          f"  持仓={body['final_position']}股  费用={body['total_fees']}")
    if missing:
        print("  ✗ 缺字段：", missing)
        return False

    # 期末价值必须 ≥ 现金（前者含持仓市值），且持仓>0 时严格大于
    if body["final_position"] > 0 and body["final_capital"] <= body["cash"]:
        print("  ✗ 期末价值未含持仓市值")
        return False
    print("  ✓ 契约齐备")
    return True


def main() -> int:
    ok = check_styles()
    ok = check_backtest("600519.SH", "qfq") and ok
    ok = check_backtest("600519.SH", "hfq") and ok
    ok = check_backtest("920808.BJ", "qfq") and ok
    print("=" * 72)
    print("全部通过" if ok else "存在失败用例")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
