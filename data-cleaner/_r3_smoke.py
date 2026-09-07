"""R3 冒烟：用真实 HTTP（TestClient）+ 真实库跑一次脚本策略回测。

验证的是「HTTP 层 → service → raw_bars」整条链路，不是单元逻辑：
路由有没有挂上、422 语义对不对、真库数据能不能跑完。

用法：.venv/Scripts/python.exe _r3_smoke.py
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

CASES = [
    ("口径表", "GET", "/api/v1/backtest/styles", None),
    ("正常回测", "POST", "/api/v1/backtest/script", {
        "symbol": "600519.SH", "start": "2021-10-08", "end": "2026-09-04",
        "code": MA_CROSS, "style": "swing", "initial_capital": 1_000_000.0,
    }),
    ("闸口拒绝(非A股)", "POST", "/api/v1/backtest/script", {
        "symbol": "AAPL", "start": "2021-10-08", "end": "2026-09-04",
        "code": MA_CROSS,
    }),
    ("闸口拒绝(本金太小)", "POST", "/api/v1/backtest/script", {
        "symbol": "600519.SH", "start": "2021-10-08", "end": "2026-09-04",
        "code": MA_CROSS, "initial_capital": 1000.0,
    }),
    ("取数失败(区间无数据)", "POST", "/api/v1/backtest/script", {
        "symbol": "600519.SH", "start": "1990-01-01", "end": "1995-12-31",
        "code": MA_CROSS,
    }),
]


def main() -> int:
    for name, method, path, payload in CASES:
        resp = client.get(path) if method == "GET" else client.post(path, json=payload)
        print("=" * 72)
        print(f"{name}  →  {method} {path}  [{resp.status_code}]")
        body = resp.json()
        if resp.status_code == 200:
            if path.endswith("styles"):
                print("  styles:", [s["key"] for s in body["styles"]])
                print("  price_fields:", [p["key"] for p in body["price_fields"]])
                print("  data_source:", body["data_source"])
            else:
                print(f"  {body['symbol']}  bars={body['data']['bars']}"
                      f"  {body['data']['first_date']}~{body['data']['last_date']}")
                print(f"  总收益={body['total_return']}%  夏普={body['sharpe_ratio']}  "
                      f"回撤={body['max_drawdown']}%  胜率={body['win_rate']}%  "
                      f"成交={body['total_trades']}  费用={body['total_fees']}")
                print(f"  期末组合={body['final_capital']}  持仓={body['final_position']}股")
                print(f"  plan={json.dumps({k: body['plan'][k] for k in ('market','style','price_field','limit_pct')}, ensure_ascii=False)}")
                print(f"  limits={len(body['limits'])}条  warnings={len(body['warnings'])}条  "
                      f"rejections={len(body['rejections'])}条")
        else:
            print("  detail:", json.dumps(body.get("detail"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
