"""回填 finance_reports 成长历史（营收/净利同比），逐报告期 akshare。

源：FundamentalSource.fetch_growth_akshare_bulk（每报告期 1 次 stock_yjbb_em 调用）。
finance_reports 与 daily_basic 为不同表，可与 backfill_daily_basic_history.py 并行跑。

用法：
    .venv\\Scripts\\python.exe backfill_growth_history.py
"""
import sys
import time
import warnings

sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.ingestion.fundamental_source import FundamentalSource
from app.storage import fundamental_store


def main() -> int:
    periods = []
    for y in range(2020, 2027):
        for (m, d) in ((3, 31), (6, 30), (9, 30), (12, 31)):
            p = f"{y}{m:02d}{d:02d}"
            if p <= "20260630":  # 截至最新已披露季报(2026Q2)
                periods.append(p)
    print(f"报告期数: {len(periods)}，范围 {periods[0]}~{periods[-1]}", flush=True)

    src = FundamentalSource(token=None, provider="akshare")
    t0 = time.time()
    df = src.fetch_growth_akshare_bulk(periods)
    print(f"成长抓取完成: rows={len(df) if df is not None else 0}, 用时 {time.time() - t0:.0f}s",
          flush=True)
    if df is None or df.empty:
        print("无成长数据，跳过", flush=True)
        return 1
    rows = df.to_dict("records")
    n = fundamental_store.upsert_finance_reports(rows)
    print(f"upsert_finance_reports 写入 {n} 行", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
