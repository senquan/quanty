"""用 akshare stock_zh_valuation_baidu 逐标的回填 daily_basic 估值列(pe_ttm/pb/ps_ttm/dv_ttm)历史。

背景
----
- 原 FundamentalSource._fetch_akshare 依赖的 akshare.stock_a_indicator_lg 在当前 akshare 版本已移除，
  故改用 stock_zh_valuation_baidu（每次 1 指标，period='近五年' 覆盖 ~2021-09 起，与 raw_bars 对齐）。
- 本环境未配置 TUSHARE_TOKEN，无法走 tushare daily_basic 市场级接口。

写入陷阱
------
upsert_daily_basic 对 total_mv/circ_mv/pe/turnover 用 EXCLUDED（无 COALESCE），
本脚本先读回该标的已有 daily_basic 行，仅覆盖 4 个估值列，避免清掉 Step0 已写的市值/股本
与原有 turnover/float_share。

用法
----
    .venv\\Scripts\\python.exe backfill_daily_basic_history.py [--reset] [--limit N]
"""
import argparse
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import pandas as pd
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.ingestion.universe import get_a_share_universe

STATE_FILE = Path("data/backfill_daily_basic_state.json")
LOG_FILE = Path("data/backfill_daily_basic.log")

# 注：akshare stock_zh_valuation_baidu 在当前版本仅 市盈率(TTM)/市净率/总市值 可用；
# 市销率/股息率/市盈率(静态) 内部解析报错（取不到）。故本回填只补 pe_ttm + pb，
# ps_ttm / dv_ttm 为已知残留缺口（需 tushare token 或其他源后续补，写进方案 #6 待办）。
INDICATORS = [("市盈率(TTM)", "pe_ttm"), ("市净率", "pb")]
PERIOD = "近五年"
MIN_INTERVAL = 0.8  # akshare 节流(s/调用)

_rate = {"last": 0.0}


def _spend():
    now = time.time()
    w = MIN_INTERVAL - (now - _rate["last"])
    if w > 0:
        time.sleep(w)
    _rate["last"] = time.time()


def log(m: str):
    line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {m}'
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _ak_code(sym: str) -> str:
    return sym.split(".")[0]


def fetch_one(code: str, ind: str) -> dict:
    import akshare as ak

    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=ind, period=PERIOD)
    if df is None or df.empty:
        return {}
    out = {}
    for _, r in df.iterrows():
        d = pd.to_datetime(r["date"], errors="coerce")
        if d is None or pd.isna(d):
            continue
        d = d.date()
        v = pd.to_numeric(r["value"], errors="coerce")
        out[d] = None if pd.isna(v) else float(v)
    return out


def load_existing(sym: str, eng):
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT trade_date, pe, pe_ttm, pb, ps_ttm, dv_ttm, "
                "turnover_rate, turnover_rate_f, total_mv, circ_mv, float_share "
                "FROM factor.daily_basic WHERE symbol=:s"
            ),
            {"s": sym},
        ).fetchall()
    cols = ["pe", "pe_ttm", "pb", "ps_ttm", "dv_ttm", "turnover_rate",
            "turnover_rate_f", "total_mv", "circ_mv", "float_share"]
    return {r[0]: dict(zip(cols, r[1:])) for r in rows}


UPSERT_SQL = """
INSERT INTO factor.daily_basic
 (symbol, trade_date, pe, pe_ttm, pb, ps_ttm, dv_ttm,
  turnover_rate, turnover_rate_f, total_mv, circ_mv, float_share, updated_at)
VALUES %s
ON CONFLICT (symbol, trade_date) DO UPDATE SET
 pe=EXCLUDED.pe, pe_ttm=EXCLUDED.pe_ttm, pb=EXCLUDED.pb, ps_ttm=EXCLUDED.ps_ttm,
 dv_ttm=EXCLUDED.dv_ttm, turnover_rate=EXCLUDED.turnover_rate,
 turnover_rate_f=EXCLUDED.turnover_rate_f, total_mv=EXCLUDED.total_mv,
 circ_mv=EXCLUDED.circ_mv, float_share=EXCLUDED.float_share, updated_at=now()
"""


def bulk_upsert(conn, rows: list[dict]) -> int:
    from psycopg2.extras import execute_values

    tuples = [
        (r["symbol"], r["trade_date"], r["pe"], r["pe_ttm"], r["pb"], r["ps_ttm"],
         r["dv_ttm"], r["turnover_rate"], r["turnover_rate_f"], r["total_mv"],
         r["circ_mv"], r["float_share"], None)
        for r in rows
    ]
    sql = UPSERT_SQL.replace("    ", "")
    n = 0
    for i in range(0, len(tuples), 2000):
        execute_values(conn, sql, tuples[i:i + 2000])
        n += len(tuples[i:i + 2000])
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    univ = get_a_share_universe()
    state = ({}
              if args.reset else
              (json.loads(STATE_FILE.read_text(encoding="utf-8"))
               if STATE_FILE.exists() else {}))
    done = set(state.get("done", []))
    eng = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))
    t0 = time.time()
    processed = 0
    failed = []

    for sym in univ:
        if sym in done:
            continue
        if args.limit and processed >= args.limit:
            break
        processed += 1
        code = _ak_code(sym)
        merged: dict = {}
        for ind_name, col in INDICATORS:
            for attempt in range(3):
                try:
                    _spend()
                    d = fetch_one(code, ind_name)
                    for dt, val in d.items():
                        merged.setdefault(dt, {})[col] = val
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        log(f"{sym} {ind_name} 失败: {str(e)[:120]}")
                    time.sleep(2)

        if not merged:
            done.add(sym)
            continue

        exist = load_existing(sym, eng)
        rows = []
        for dt, vals in merged.items():
            base = exist.get(dt, {})
            rows.append({
                "symbol": sym, "trade_date": dt,
                "pe": base.get("pe"), "pe_ttm": vals.get("pe_ttm"),
                "pb": vals.get("pb"), "ps_ttm": vals.get("ps_ttm"),
                "dv_ttm": vals.get("dv_ttm"),
                "turnover_rate": base.get("turnover_rate"),
                "turnover_rate_f": base.get("turnover_rate_f"),
                "total_mv": base.get("total_mv"),
                "circ_mv": base.get("circ_mv"),
                "float_share": base.get("float_share"),
            })
        try:
            raw = eng.raw_connection()
            try:
                with raw.cursor() as cur:
                    bulk_upsert(cur, rows)
                raw.commit()
            finally:
                raw.close()
            done.add(sym)
            if processed % 50 == 0:
                log(f"已处理 {processed}/{len(univ)}, 完成 {len(done)} 标的, "
                    f"用时 {time.time() - t0:.0f}s")
        except Exception as e:  # noqa: BLE001
            failed.append(sym)
            log(f"{sym} upsert 失败: {str(e)[:160]}")
            time.sleep(2)
        state["done"] = sorted(done)
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    log(f"结束: 处理 {processed}, 完成 {len(done)}, 失败 {len(failed)} {failed[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
