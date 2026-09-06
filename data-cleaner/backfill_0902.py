"""回填 2026-09-02 缺失的日线（AlphaFeed 盘后流水线在 9/2 部分失败）。

策略：用 tushare 按 trade_date 全市场批量拉取 9/1/9/2 原始日K，
对缺失 9/2 的标的，采用「链式前复权」拼接：
    qfq(9/2) = qfq(9/1) * (raw_9_2 / raw_9_1)
其中 qfq(9/1) 取自 raw_bars 既有行（与历史同锚、同尺度，时序因子不会在 9/2 假跳变）。
默认 9/2 无除权（r=1）；tushare adj_factor 接口限频 1次/小时且与已存 AlphaFeed 因子
口径混用会致比值偏差，故不取。少数 9/2 当天除权股的 qfq 会有细微偏差，后续可单独补。

要求：data-cleaner .venv（已装 tushare），DATABASE_URL 指向共享 Postgres。
用法：python backfill_0902.py            # 执行回填
      python backfill_0902.py --dry      # 只统计缺口，不写库
"""
import sys
import time

import pandas as pd
from datetime import date

from sqlalchemy import text

from app.core.config import settings
from app.storage.raw_store import repository

TARGET = date(2026, 9, 2)
PREV = date(2026, 9, 1)
DRY = "--dry" in sys.argv

_tsd = TARGET.strftime("%Y%m%d")
_psd = PREV.strftime("%Y%m%d")


def get_tushare(table: str, trade_date: str) -> pd.DataFrame:
    """tushare daily / adj_factor 按交易日全市场取数，带简单重试。"""
    import tushare as ts

    pro = ts.pro_api(settings.TUSHARE_TOKEN)
    for attempt in range(4):
        try:
            if table == "daily":
                return pro.daily(trade_date=trade_date)
            return pro.adj_factor(trade_date=trade_date)
        except Exception as e:  # noqa: BLE001
            wait = 2 ** attempt
            print(f"  tushare {table} {trade_date} 失败({e})，{wait}s 后重试")
            time.sleep(wait)
    raise RuntimeError(f"tushare {table} {trade_date} 重试耗尽")


def main() -> None:
    eng = repository._engine
    if eng is None:
        raise RuntimeError("raw_store 未连接 PG，无法回填")

    # 1) 既有 9/2 覆盖、宇宙（<=9/1 已建）、9/1 历史行
    with eng.connect() as c:
        have_9_2 = {r[0] for r in c.execute(text(
            "SELECT DISTINCT symbol FROM factor.raw_bars "
            "WHERE freq='1d' AND timestamp=:d"), {"d": TARGET})}
        universe = {r[0] for r in c.execute(text(
            "SELECT DISTINCT symbol FROM factor.raw_bars "
            "WHERE freq='1d' AND timestamp <= :d"), {"d": PREV})}
        prev_rows = {}
        for r in c.execute(text(
            "SELECT symbol, open, high, low, close, volume, adj_factor, hfq_close "
            "FROM factor.raw_bars WHERE freq='1d' AND timestamp=:d"), {"d": PREV}):
            prev_rows[r[0]] = {
                "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                "volume": r[5], "adj_factor": r[6], "hfq_close": r[7],
            }

    missing = sorted(universe - have_9_2)
    print(f"宇宙(<=9/1): {len(universe)} | 已有 9/2: {len(have_9_2)} | "
          f"缺失 9/2: {len(missing)}")
    if not missing:
        print("无缺口，退出")
        return

    # 2) tushare 全市场批量取数（4 次调用）
    print("拉取 tushare 日K / 复权因子 ...")
    d1 = get_tushare("daily", _psd)
    d2 = get_tushare("daily", _tsd)
    d1m = {r["ts_code"]: r for _, r in d1.iterrows()}
    d2m = {r["ts_code"]: r for _, r in d2.iterrows()}
    print(f"  tushare 9/1 bars={len(d1m)} 9/2 bars={len(d2m)}")
    print("  注: 跳过 adj_factor(tushare 该接口限 1次/小时，且与已存 AlphaFeed 因子混用"
          "会致比值偏差)。默认 r=1(假定 9/2 无除权)，qfq 由 9/1 按原始收益率链式拼接，"
          "与历史同尺度；少数 9/2 当天除权股 qfq 略有偏差，后续可补。")

    # 3) 链式拼接
    recs = []
    skipped = []
    for sym in missing:
        prev = prev_rows.get(sym)
        bar = d2m.get(sym)
        if prev is None or bar is None:
            skipped.append(sym)
            continue
        raw_prev = d1m.get(sym, {}).get("close")
        if raw_prev is None or float(raw_prev) <= 0:
            skipped.append(sym)
            continue
        raw_prev = float(raw_prev)
        raw_o, raw_h, raw_l, raw_c = (
            float(bar["open"]), float(bar["high"]),
            float(bar["low"]), float(bar["close"]),
        )
        if raw_c <= 0:
            skipped.append(sym)
            continue

        # 默认 r=1（9/2 无除权）：qfq 直接由 9/1 qfq 按原始收益率拼接，与历史同尺度
        qfq_prev = float(prev["close"])
        factor = qfq_prev / raw_prev  # raw -> qfq 缩放系数（r=1）

        adj_factor_9_2 = (
            float(prev["adj_factor"]) if prev["adj_factor"] is not None else None
        )
        hfq_9_2 = (
            float(prev["hfq_close"]) * (raw_c / raw_prev)
            if prev["hfq_close"] is not None else None
        )

        recs.append((
            sym, pd.Timestamp(TARGET),
            raw_o * factor, raw_h * factor, raw_l * factor, raw_c * factor,
            float(bar["vol"]), "tushare-backfill", "1d",
            adj_factor_9_2, hfq_9_2, float(bar.get("amount", 0) or 0),
        ))

    df = pd.DataFrame(recs, columns=[
        "symbol", "timestamp", "open", "high", "low", "close",
        "volume", "source", "freq", "adj_factor", "hfq_close", "amount",
    ])
    print(f"构造 {len(df)} 行；跳过(无 9/2 交易/缺前置) {len(skipped)} 只")
    if skipped:
        print("  跳过样本:", skipped[:10])

    if DRY:
        print("DRY 模式，不写库。预览前 3 行：")
        print(df.head(3).to_string())
        return

    written = repository.bulk_upsert(df)
    print(f"bulk_upsert 写入 {written} 行")

    # 4) 校验
    with eng.connect() as c:
        n = c.execute(text(
            "SELECT COUNT(DISTINCT symbol) FROM factor.raw_bars "
            "WHERE freq='1d' AND timestamp=:d"), {"d": TARGET}).scalar()
    print(f"回填后 9/2 覆盖: {n} / {len(universe)}")


if __name__ == "__main__":
    main()
