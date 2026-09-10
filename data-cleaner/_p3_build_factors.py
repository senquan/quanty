"""P3-3 运行入口：计算 INTL_* 六因子并写入 intel.factor_values。

用法：
    .venv/Scripts/python.exe _p3_build_factors.py                 # 全量重算并落库
    .venv/Scripts/python.exe _p3_build_factors.py --dry-run       # 只算不写
    .venv/Scripts/python.exe _p3_build_factors.py --prompt-version v2

幂等：唯一键 (symbol, trade_date, factor_code, factor_version, prompt_version)，
重跑覆盖同版本行；LLM 重跑产生新 prompt_version 时插新行，旧版本可见性不变。

计算逻辑统一在 ``app.intel.daily_build.build_factors``（19:30 定时任务走同一函数，
避免"脚本算一套、定时任务算另一套"的分叉），本文件只负责建表、打印与落库开关。
"""
from __future__ import annotations

import argparse
import os

from app.intel.core.logging import get_logger
from app.intel.daily_build import build_factors
from app.intel.factorize import factors as F
from app.intel.store import get_engine

logger = get_logger(__name__)

MIGRATION = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "migrations", "014_intel_factor_values.sql",
)


def run_migration(engine) -> None:
    """执行 migration 014（建 intel.factor_values 表，幂等）。

    用原生执行（store.run_sql_file）—— 迁移注释里的 "键:值" 示例会被
    SQLAlchemy text() 误解析为 bind parameter。
    """
    if not os.path.exists(MIGRATION):
        print(f"[WARN] migration not found: {MIGRATION}")
        return
    from app.intel.store import run_sql_file

    run_sql_file(MIGRATION, engine)
    print(f"[OK] Migration 014 applied (idempotent)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", default=F.PROMPT_VERSION)
    ap.add_argument("--dry-run", action="store_true", help="只计算不写库")
    ap.add_argument("--skip-migration", action="store_true", help="跳过建表")
    args = ap.parse_args()

    engine = get_engine()

    if not args.skip_migration:
        run_migration(engine)

    print("=== P3-3 INTL_* 因子构建 ===")
    cal0 = F.load_trade_calendar(engine)
    print(f"交易日历(行情): {len(cal0)} 天 ({cal0[0]} ~ {cal0[-1]})" if cal0 else "交易日历: 空!")

    res = build_factors(
        prompt_version=args.prompt_version, engine=engine, dry_run=args.dry_run
    )

    print(f"装载 mention: {res['mentions']} 条 (prompt_version={args.prompt_version})")
    if res["calendar_extended"]:
        print(
            f"交易日历外推: {len(cal0)} → {res['calendar_days']} 天 "
            f"(覆盖近期 mention，近似日历，行情待入库)"
        )
    print(f"装载画像: {res['profiles']} 个 profile_key")
    print(f"生成因子行: {res['rows']} 行")

    print("\n--- 各因子行数 / 取值概况 ---")
    for code in sorted(res["by_code"]):
        st = res["by_code"][code]
        print(
            f"  {code:26s} rows={st['rows']:5d}  非零={st['nonzero']:5d}  "
            f"min={st['min']:8.4f} max={st['max']:8.4f} mean={st['mean']:8.4f}"
        )

    print(f"\n覆盖: {res['symbols']} 个标的 × {res['trade_dates']} 个交易日")

    # 防前视自检：任一行的 available_at 必须 <= 其 trade_date 收盘（即 trade_date 之后）
    print(
        f"防前视自检: available_at 晚于 trade_date 的行 = "
        f"{res['anti_lookahead_violations']} (应为 0)"
    )

    if args.dry_run:
        print("\n[dry-run] 未写库")
        return 0

    print(f"\n✅ 写入 intel.factor_values: {res['written']} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
