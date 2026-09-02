"""导入历史调仓记录（在 backend 侧运行）

配合 data-cleaner 侧的 run_export_rebalance_records.py 使用（两库独立，文件中转）：

    python run_import_rebalance_records.py [输入路径] [mode]

- mode 默认 paper（历史调仓均为模拟盘）
- 幂等：按 (strategy_id, rebalance_date, mode) upsert，可重复执行
"""
import asyncio
import json
import sys

from app.core.database import AsyncSessionLocal
from app.models.trading import MODE_PAPER
from app.services import trading_repository as repo

_FIELDS = (
    "strategy_id",
    "strategy_name",
    "rebalance_date",
    "trade_date",
    "target_count",
    "orders_placed",
    "amount",
    "status",
    "detail",
)


async def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "rebalance_records.jsonl"
    mode = (sys.argv[2] if len(sys.argv) > 2 else MODE_PAPER).strip().lower()

    await repo.ensure_trading_tables()

    n = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if not rec.get("rebalance_date") or rec.get("strategy_id") is None:
                    skipped += 1
                    continue
                await repo.upsert_rebalance(
                    session,
                    mode=mode,
                    **{k: rec.get(k) for k in _FIELDS},
                )
                n += 1
        await session.commit()

    print(f"已导入 {n} 条（跳过 {skipped} 条）-> trading_rebalance_records, mode={mode}")


if __name__ == "__main__":
    asyncio.run(main())
