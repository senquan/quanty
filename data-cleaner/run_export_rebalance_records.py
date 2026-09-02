"""导出历史调仓记录（在 data-cleaner 侧运行）

backend 与 data-cleaner 分属独立 Postgres 实例，无法库内迁移，故用文件中转：

    1) data-cleaner 侧：  python run_export_rebalance_records.py [输出路径]
    2) backend 侧：       python run_import_rebalance_records.py [输入路径]

默认输出 rebalance_records.jsonl（每行一条 JSON）。
"""
import json
import sys

import app.storage.db as db
from app.strategy import store as strat_store


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "rebalance_records.jsonl"

    strategies = db.run_async(strat_store.list_strategies())
    if not strategies:
        print("没有策略记录，无需导出")
        return

    total = 0
    with open(out, "w", encoding="utf-8") as f:
        for s in strategies:
            rows = db.run_async(strat_store.list_executions(s["id"], limit=100_000))
            for r in rows or []:
                rec = {
                    "strategy_id": r.get("strategy_id") or s["id"],
                    "strategy_name": s.get("name"),
                    "rebalance_date": _s(r.get("rebalance_date")),
                    "trade_date": _s(r.get("trade_date")),
                    "target_count": r.get("target_count"),
                    "orders_placed": r.get("orders_placed"),
                    "amount": r.get("amount"),
                    "status": r.get("status"),
                    "detail": r.get("detail"),
                }
                if not rec["rebalance_date"]:
                    continue
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += 1

    print(f"已导出 {total} 条调仓记录 -> {out}")


def _s(v) -> str | None:
    return None if v is None else str(v)[:10]


if __name__ == "__main__":
    main()
