"""P2 画像基线构建 + 报告输出

用法：
    python _p2_build_profiles.py [--prompt-version v2] [--profile-version v1]

输出：
    1. intel.author_profiles 表写入（migration 013 需先执行）
    2. p2_baseline_report.csv（画像摘要）
    3. 控制台打印关键指标
"""
from __future__ import annotations

import argparse
import csv
import sys
import os

# 确保项目根在 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.intel.aggregate.profile import build_profiles, SAMPLE_THRESHOLD
from app.intel.core.config import settings
from app.intel.store import get_engine
from sqlalchemy import text


def run_migration(engine) -> None:
    """执行 migration 013（建 author_profiles 表）"""
    mig_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "migrations", "013_intel_profiles.sql",
    )
    if not os.path.exists(mig_path):
        print(f"[WARN] Migration file not found: {mig_path}")
        return
    # 用原生执行（store.run_sql_file）—— 迁移注释里的 "键:值" 示例会被
    # SQLAlchemy text() 误解析为 bind parameter（报 "A value is required ..."）。
    from app.intel.store import run_sql_file

    run_sql_file(mig_path, engine)
    print(f"[OK] Migration 013 applied: {mig_path}")


def export_report(profiles: list[dict], output_csv: str) -> None:
    """导出画像报告 CSV"""
    rows = []
    for p in profiles:
        import json as _json
        sd = _json.loads(p.get("stance_dist", "{}")) if isinstance(p.get("stance_dist"), str) else (p.get("stance_dist") or {})
        sv = _json.loads(p.get("style_vector", "{}")) if isinstance(p.get("style_vector"), str) else (p.get("style_vector") or {})
        rows.append({
            "profile_key": p["profile_key"],
            "profile_type": p["profile_type"],
            "total_mentions": p["total_mentions"],
            "total_docs": p["total_docs"],
            "unique_symbols": p["unique_symbols"],
            "date_first": str(p.get("date_first") or ""),
            "date_last": str(p.get("date_last") or ""),
            "bullish": sd.get("bullish", 0),
            "neutral": sd.get("neutral", 0),
            "bearish": sd.get("bearish", 0),
            "bullish_ratio_tw": sv.get("bullish_ratio", 0),
            "bearish_ratio_tw": sv.get("bearish_ratio", 0),
            "symbol_concentration": sv.get("symbol_concentration", 0),
            "avg_excess_20d": p.get("avg_excess_20d") or "",
            "avg_excess_60d": p.get("avg_excess_60d") or "",
            "accuracy_sample_size": p.get("accuracy_sample_size", 0),
            "win_rate_20d": p.get("win_rate_20d") or "",
            "win_rate_60d": p.get("win_rate_60d") or "",
            "sample_insufficient": "Y" if p.get("sample_insufficient") else "N",
            "drift_detected": "Y" if p.get("drift_detected") else "N",
        })

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"[OK] Report exported: {output_csv} ({len(rows)} profiles)")


def main():
    parser = argparse.ArgumentParser(description="P2 画像基线构建")
    parser.add_argument("--prompt-version", default="v2", help="mention prompt version (default: v2)")
    parser.add_argument("--profile-version", default="v1", help="profile version tag (default: v1)")
    parser.add_argument("--skip-migration", action="store_true", help="跳过 migration 执行")
    parser.add_argument("--output", default="p2_baseline_report.csv", help="报告输出文件名")
    args = parser.parse_args()

    engine = get_engine()

    # Step 0: migration
    if not args.skip_migration:
        run_migration(engine)

    # Step 1: build profiles
    print(f"\n{'='*60}")
    print(f"P2 Profile Build: prompt_version={args.prompt_version}, profile_version={args.profile_version}")
    print(f"{'='*60}\n")

    profiles = build_profiles(
        prompt_version=args.prompt_version,
        profile_version=args.profile_version,
        engine=engine,
    )

    if not profiles:
        print("[WARN] No profiles built. Check if doc_mentions has data for this prompt_version.")
        return

    # Step 2: summary
    print(f"\n{'='*60}")
    print(f"P2 BASELINE SUMMARY ({len(profiles)} profiles)")
    print(f"{'='*60}")

    sufficient = [p for p in profiles if not p.get("sample_insufficient")]
    insufficient = [p for p in profiles if p.get("sample_insufficient")]

    print(f"\n样本充足 (≥{SAMPLE_THRESHOLD} mentions): {len(sufficient)}")
    print(f"样本不足 (<{SAMPLE_THRESHOLD} mentions): {len(insufficient)}")

    # Top profiles by mentions
    sorted_p = sorted(profiles, key=lambda x: -x["total_mentions"])
    print(f"\n--- Top 10 by mentions ---")
    for p in sorted_p[:10]:
        flag = " ⚠️ 样本不足" if p.get("sample_insufficient") else ""
        acc = p.get("accuracy_sample_size", 0)
        e20 = p.get("avg_excess_20d")
        e60 = p.get("avg_excess_60d")
        acc_str = f" | acc_n={acc}" if acc > 0 else ""
        exc_str = ""
        if e20 is not None:
            exc_str = f" | excess_20d={e20:+.2f}%"
        if e60 is not None:
            exc_str += f" | excess_60d={e60:+.2f}%"
        print(
            f"  [{p['profile_type']}] {p['profile_key']:<30s} "
            f"mentions={p['total_mentions']:>4} docs={p['total_docs']:>3} "
            f"symbols={p['unique_symbols']:>3}"
            f"{acc_str}{exc_str}{flag}"
        )

    # Accuracy summary across all profiles
    total_acc_samples = sum(p.get("accuracy_sample_size", 0) for p in profiles)
    valid_e20 = [p["avg_excess_20d"] for p in profiles if p.get("avg_excess_20d") is not None]
    valid_e60 = [p["avg_excess_60d"] for p in profiles if p.get("avg_excess_60d") is not None]
    print(f"\n--- 准确度汇总 ---")
    print(f"总 accuracy sample (有行情数据的 mention): {total_acc_samples}")
    if valid_e20:
        avg_e20 = sum(valid_e20) / len(valid_e20)
        print(f"平均 excess_20d (跨 profile): {avg_e20:+.2f}% ({len(valid_e20)} 个 profile 有数据)")
    if valid_e60:
        avg_e60 = sum(valid_e60) / len(valid_e60)
        print(f"平均 excess_60d (跨 profile): {avg_e60:+.2f}% ({len(valid_e60)} 个 profile 有数据)")

    # Drift detection
    drifted = [p for p in profiles if p.get("drift_detected")]
    if drifted:
        print(f"\n⚠️ 风格漂移检测: {len(drifted)} 个 profile 标记漂移")
        for p in drifted[:5]:
            print(f"   - [{p['profile_type']}] {p['profile_key']}")

    # Step 3: export report
    export_report(profiles, args.output)

    print(f"\n✅ P2 基线构建完成。报告: {args.output}")
    print(f"   数据库表: intel.author_profiles (version={args.profile_version})")


if __name__ == "__main__":
    main()
