"""AlphaFeed ex_factor 数值校验（手动运行，需 ALPHAFEED_KEY + 联网）

用法：
    ALPHAFEED_KEY=xxx python run_validate_alphafeed.py 600519.SH 2020-01-01 2024-12-31

做了什么：
- 拉 forward(前复权) 与 none(不复权) 两套日线 + ex-factors(除权因子)。
- 交叉验证 AlphaFeed 的 `ex_factor` 口径：qfq / raw 应 ≈ ex_factor / ex_factor_latest。
  若成立，说明本接入层 `adj_factor=ex_factor`、`hfq_close=close*f_latest/f_first` 的映射正确。
- 打印 qfq / hfq / adj_factor 对照表，便于人工核对已知除权日前后价格跳变是否被正确复权。

仅作诊断，不参与 pytest。
"""
import sys

import httpx
import pandas as pd

from app.core.config import settings
from app.ingestion.alphafeed_source import AlphafeedSource


def _ms(d: str) -> int:
    cst = pd.Timestamp(d).tz_localize("Asia/Shanghai").tz_convert("UTC")
    return int(cst.timestamp() * 1000)


def _klines(base: str, key: str, symbol: str, start: str, end: str, adjust: str) -> dict:
    url = f"{base.rstrip('/')}/v1/klines"
    resp = httpx.get(
        url,
        params={
            "symbol": symbol,
            "period": "1d",
            "start_time": _ms(start),
            "end_time": _ms(end),
            "adjust": adjust,
        },
        headers={"X-API-Key": key},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def _ex_factors(base: str, key: str, symbol: str, end: str) -> pd.Series:
    resp = httpx.get(
        f"{base.rstrip('/')}/v1/klines/ex-factors",
        params={"symbols": symbol, "start_time": _ms("19900101"), "end_time": _ms(end)},
        headers={"X-API-Key": key},
        timeout=30.0,
    )
    resp.raise_for_status()
    entries = resp.json()["data"].get(symbol, [])
    ts = [pd.Timestamp(int(e["timestamp"]), unit="ms", tz="UTC").tz_convert("Asia/Shanghai") for e in entries]
    return pd.Series([float(e["ex_factor"]) for e in entries], index=pd.DatetimeIndex(ts)).sort_index()


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    symbol, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    key = getattr(settings, "ALPHAFEED_KEY", None)
    if not key:
        print("缺少 ALPHAFEED_KEY（env 或 settings）")
        sys.exit(1)
    base = getattr(settings, "ALPHAFEED_BASE_URL", "https://api.alphafeed.org")

    fwd = _klines(base, key, symbol, start, end, "forward")
    raw = _klines(base, key, symbol, start, end, "none")
    ex = _ex_factors(base, key, symbol, end)
    f_latest, f_first = float(ex.max()), float(ex.min())

    def to_df(d):
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(d["timestamp"], unit="ms", utc=True).tz_convert("Asia/Shanghai"),
                "close": [float(x) for x in d["close"]],
            }
        )
        return df.set_index("timestamp").sort_index()

    qfq = to_df(fwd)
    qraw = to_df(raw)
    qfq["adj_factor"] = ex.reindex(qfq.index, method="ffill").values
    qfq["hfq_close"] = qfq["close"] * (f_latest / f_first)
    qfq["raw_close"] = qraw["close"]

    # 交叉验证：qfq/raw 应 ≈ ex_factor/ex_factor_latest
    ratio = (qfq["close"] / qfq["raw_close"]).dropna()
    expected = (qfq["adj_factor"] / f_latest).dropna()
    joined = pd.concat([ratio, expected], axis=1).dropna()
    max_err = (joined.iloc[:, 0] - joined.iloc[:, 1]).abs().max()
    print(f"[校验] qfq/raw 与 ex_factor/ex_factor_latest 最大偏差 = {max_err:.3e}  "
          f"(f_first={f_first}, f_latest={f_latest})")
    if max_err < 1e-6:
        print("[校验] 通过：AlphaFeed ex_factor 口径与 forward 复权一致，映射正确。")
    else:
        print("[校验] 偏差偏大，请人工核对 ex_factor 定义（可能非累计型/锚点不同）。")

    cols = ["close", "raw_close", "adj_factor", "hfq_close"]
    with pd.option_context("display.max_rows", 30, "display.width", 120):
        print(f"\n{symbol} 前复权/不复权/复权因子对照（近 30 行）：")
        print(qfq[cols].tail(30).to_string())


if __name__ == "__main__":
    main()
