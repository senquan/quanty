"""增量回填 factor.raw_bars.hfq_close（D-2）—— CLI 入口

真正的实现在 ``app/tasks/hfq_refresh.py``（定时任务与 CLI 共用同一份，避免两份漂移）。
本文件只负责：命令行参数、akshare 真值抽样校验。

用法
----
    python backfill_hfq_incremental.py              # dry-run，只报告
    python backfill_hfq_incremental.py --apply      # 真正写入
    python backfill_hfq_incremental.py --verify     # akshare 真值抽样校验
    python backfill_hfq_incremental.py --apply --verify

幂等：只更新 ``hfq_close IS NULL`` 的行，重复运行无副作用。
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text

from app.tasks.hfq_refresh import FREQ_DAILY, backfill, count_missing


def _sync_url_from_settings() -> str:
    """DB URL 统一从 app 配置取（读 .env）。

    ⚠️ 此前这里硬编码了生产库连接串（含口令），已改为走配置 —— 口令绝不进源码/版本库。
    """
    from app.intel.store import _sync_url

    return _sync_url()


DATABASE_URL = os.getenv("DATABASE_URL") or _sync_url_from_settings()

def verify(eng, samples: int = 3) -> bool:
    """用 akshare 真值比对最近几个交易日的 hfq_close（探测是否发生新的除权重锚）"""
    try:
        import time

        import akshare as ak
        import pandas as pd
    except ImportError:
        print("[校验] 未安装 akshare，跳过")
        return True

    with eng.connect() as c:
        days = [
            str(r[0])
            for r in c.execute(
                text("""
                    SELECT DISTINCT timestamp::date FROM factor.raw_bars
                    WHERE freq = :frq ORDER BY 1 DESC LIMIT 3
                """),
                {"frq": FREQ_DAILY},
            ).all()
        ]
        picks = [
            r[0]
            for r in c.execute(
                text("""
                    SELECT symbol FROM factor.raw_bars WHERE freq = :frq
                    GROUP BY symbol ORDER BY count(*) DESC LIMIT :n
                """),
                {"frq": FREQ_DAILY, "n": samples},
            ).all()
        ]

    ok = True
    print(f"[校验] 采样 {len(picks)} 只 × 最近 {len(days)} 个交易日")
    for s in picks:
        code, mkt = s.split(".")
        ak_sym = ("sh" if mkt == "SH" else "sz") + code
        try:
            df = ak.stock_zh_a_daily(symbol=ak_sym, adjust="hfq")
        except Exception as ex:  # noqa: BLE001
            print(f"  {s}: akshare 拉取失败（{ex}），跳过")
            continue
        df["d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        with eng.connect() as c:
            cur = {
                str(r[0]): float(r[1])
                for r in c.execute(
                    text("""
                        SELECT timestamp::date, hfq_close FROM factor.raw_bars
                        WHERE symbol = :s AND freq = :frq AND timestamp >= DATE :d0
                    """),
                    {"s": s, "frq": FREQ_DAILY, "d0": min(days)},
                ).all()
            }
        for d in days:
            row = df[df["d"] == d]
            if row.empty or d not in cur or cur[d] is None:
                continue
            true_v = float(row.iloc[0]["close"])
            got = cur[d]
            diff = got / true_v - 1 if true_v else 0.0
            bad = abs(diff) > 0.002  # 0.2% 容差
            ok = ok and not bad
            print(
                f"  {s} {d}: 库内={got:.4f} akshare={true_v:.4f} "
                f"偏差={diff:+.4%} {'✗ 不一致' if bad else '✓'}"
            )
        time.sleep(0.3)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="增量回填 raw_bars.hfq_close")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--verify", action="store_true", help="用 akshare 真值抽样校验")
    ap.add_argument("--samples", type=int, default=3, help="校验抽样只数，默认 3")
    ap.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        metavar="CODE",
        help="只补指定标的，可重复传（用于处理「无历史 hfq」的孤儿标的）",
    )
    args = ap.parse_args()

    eng = create_engine(DATABASE_URL)
    if args.symbols:
        res = backfill(eng, symbols=args.symbols)
        print(f"[定向] {args.symbols} → 写入 {res['updated']} 行")
        if res["orphans"]:
            print(f"[遗留] 仍缺（无历史 hfq，算不出 k）: {res['orphans']}")
        return 0

    before = count_missing(eng)
    print(
        f"[缺失] {before['rows']} 行 / {before['symbols']} 只标的"
        + (f" / {before['date_min']} ~ {before['date_max']}" if before["rows"] else "")
    )

    if args.apply:
        res = backfill(eng)
        if res["orphans"]:
            print(f"[遗留] {len(res['orphans'])} 只标的无历史 hfq，需单独全量重拉：")
            for s in res["orphans"][:20]:
                print(f"       - {s}")
            if len(res["orphans"]) > 20:
                print(f"       ... 其余 {len(res['orphans']) - 20} 只略")
        print(f"[结果] 缺失 {res['before']['rows']} → {res['after']['rows']}")
    else:
        print("[dry-run] 未写入，加 --apply 执行")

    return 0 if (not args.verify or verify(eng, args.samples)) else 1


if __name__ == "__main__":
    sys.exit(main())
