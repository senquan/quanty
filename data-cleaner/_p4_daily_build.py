"""手动触发 intel 每日构建（等价于 19:30 定时任务 intel_daily_build）

编排在 ``app.intel.daily_build.run_daily_build``，与定时任务**同一个函数**——
本文件只负责参数与打印，避免"手动跑一套、定时跑另一套"。

用法：
  .venv/Scripts/python.exe _p4_daily_build.py                  # 抽取+画像+因子（抽取花钱）
  .venv/Scripts/python.exe _p4_daily_build.py --no-extract     # 只画像+因子（零成本）
  .venv/Scripts/python.exe _p4_daily_build.py --limit 20       # 本轮最多抽 20 篇
  .venv/Scripts/python.exe _p4_daily_build.py --dry-run-factors # 因子只算不写库
  .venv/Scripts/python.exe _p4_daily_build.py --emit           # 构建后广播 factor_updated

提示：抽取需要 INTEL_LLM_* 已配置且 LLM 端点可达（办公内网 vLLM 需连 VPN）；
未配置时抽取自动 skipped，其余两步照跑。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.intel import daily_build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="intel 每日构建（抽取→画像→因子）")
    ap.add_argument("--no-extract", action="store_true", help="跳过抽取（零成本）")
    ap.add_argument("--no-profiles", action="store_true", help="跳过画像")
    ap.add_argument("--no-factors", action="store_true", help="跳过因子")
    ap.add_argument("--limit", type=int, default=None, help="本轮抽取篇数上限")
    ap.add_argument("--prompt-version", default=None, help="mention prompt 版本（默认 v2）")
    ap.add_argument("--profile-version", default=daily_build.PROFILE_VERSION)
    ap.add_argument("--dry-run-factors", action="store_true", help="因子只算不写库")
    ap.add_argument("--emit", action="store_true", help="构建后广播 factor_updated（默认不广播）")
    args = ap.parse_args()

    print("=== intel 每日构建（手动）===")
    summary = daily_build.run_daily_build(
        do_extract=not args.no_extract,
        do_profiles=not args.no_profiles,
        do_factors=not args.no_factors,
        extract_limit=args.limit,
        prompt_version=args.prompt_version,
        profile_version=args.profile_version,
        dry_run_factors=args.dry_run_factors,
    )
    print(daily_build.format_summary(summary))
    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if args.emit and summary.get("factor_codes"):
        try:
            from app.ws.events import emit_factor_updated

            emit_factor_updated(summary["factor_codes"], reason="intel_daily_build_manual")
            summary["emitted"] = True
            print(f"\n已广播 factor_updated: {summary['factor_codes']}")
        except Exception as e:  # noqa: BLE001
            print(f"\n[WARN] 广播失败（数据已落库）: {type(e).__name__}: {e}")

    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
