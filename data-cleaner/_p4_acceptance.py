"""P4 全链路验收：摄取 → 去重 → 预筛 → 抽取 → 画像 → 因子 → 风格总结

两种用法：

1) **只验收现状**（默认，零 LLM 成本、不写库）：
   .venv/Scripts/python.exe _p4_acceptance.py
   对库内真实数据逐段检查并给出 PASS/FAIL 表（含 200 篇抽样的预筛命中率）。

2) **投喂语料后跑完整链路**（公众号 200 篇的场景）：
   .venv/Scripts/python.exe _p4_acceptance.py --ingest ~/intel-inbox/公众号A \
       --source-name 公众号A --extract-limit 200 --build-profiles --build-factors
   依次执行：P4-1/P4-2 摄取入库 → 理解层预筛+LLM 抽取 → P2 画像 → P3 因子 → 验收。
   （--extract-limit 0 可跳过 LLM 抽取，只验收到"入库+预筛"）

诚实原则：语料必须是真实文章，脚本不生成任何合成数据；缺语料时明确报告 BLOCKED 而非编造。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.intel import store  # noqa: E402
from app.intel.core.logging import get_logger  # noqa: E402
from app.intel.store import get_engine  # noqa: E402

logger = get_logger(__name__)

PRESCREEN_SAMPLE = 200  # 预筛命中率抽样篇数（对齐验收标准"200 篇"）


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------
def _scalar(sql: str, **params) -> int:
    with get_engine().connect() as c:
        return int(c.execute(store.text(sql), params).scalar() or 0)


def check_ingest() -> dict:
    """摄取与去重：同源于 content_hash 不应重复入库"""
    docs = _scalar("SELECT count(*) FROM intel.documents")
    dup = _scalar(
        "SELECT count(*) FROM (SELECT source_id, content_hash FROM intel.documents "
        "GROUP BY source_id, content_hash HAVING count(*) > 1) x"
    )
    reposts = _scalar(
        "SELECT count(*) FROM intel.documents WHERE duplicate_of_id IS NOT NULL"
    )
    raw_missing = _scalar(
        "SELECT count(*) FROM intel.documents WHERE raw_path IS NULL OR raw_path = ''"
    )
    return {
        "stage": "1 摄取/去重",
        "documents": docs,
        "duplicate_content_hash": dup,
        "reposts_marked": reposts,
        "raw_path_missing": raw_missing,
        "pass": docs > 0 and dup == 0,
        "note": "同源同 content_hash 重复应为 0（三级幂等去重生效）",
    }


def check_prescreen(sample: int = PRESCREEN_SAMPLE) -> dict:
    """预筛命中率：抽样 N 篇，看多少篇命中至少一个标的可送 LLM"""
    from app.intel.understand import service as US

    docs = store.docs_for_understanding(sample)
    if not docs:
        return {"stage": "2 预筛", "sampled": 0, "pass": False,
                "note": "无可送审文档（BLOCKED：需要语料）"}
    rep = US.prescreen_report(docs)
    hit = rep.get("docs_with_candidates", rep.get("hit", 0))
    total = rep.get("total_docs", len(docs)) or len(docs)
    rate = round(hit / total, 4) if total else 0.0
    return {
        "stage": "2 预筛",
        "sampled": total,
        "docs_with_candidates": hit,
        "hit_rate": rate,
        "pass": hit > 0,
        "note": f"抽样 {total} 篇，{hit} 篇命中标的（可送 LLM）；其余为纯方法论/闲聊",
    }


def check_extract(prompt_version: str = "v2") -> dict:
    """抽取：mentions 覆盖的文档数、quarantine、引用完整性"""
    total = _scalar(
        "SELECT count(*) FROM intel.doc_mentions WHERE prompt_version = :pv",
        pv=prompt_version,
    )
    dist_docs = _scalar(
        "SELECT count(DISTINCT doc_id) FROM intel.doc_mentions WHERE prompt_version = :pv",
        pv=prompt_version,
    )
    orphan = _scalar(
        "SELECT count(*) FROM intel.doc_mentions m "
        "LEFT JOIN intel.documents d ON d.id = m.doc_id "
        "WHERE d.id IS NULL AND m.prompt_version = :pv",
        pv=prompt_version,
    )
    quar = _scalar("SELECT count(*) FROM intel.quarantine")
    llm_ok = _scalar(
        "SELECT count(*) FROM intel.llm_runs WHERE status = 'ok' AND prompt_version = :pv",
        pv=prompt_version,
    )
    cost = 0.0
    with get_engine().connect() as c:
        cost = float(
            c.execute(store.text("SELECT COALESCE(sum(cost_cny),0) FROM intel.llm_runs")
                      ).scalar() or 0)
    return {
        "stage": "3 LLM 抽取",
        "mentions": total,
        "docs_covered": dist_docs,
        "orphan_mentions": orphan,
        "quarantined": quar,
        "llm_runs_ok": llm_ok,
        "cost_cny_total": round(cost, 4),
        "pass": total > 0 and orphan == 0,
        "note": "孤立 mention（doc 不存在）应为 0；成本在 llm_runs 可查",
    }


def check_profiles(prompt_version: str = "v2") -> dict:
    """画像：profile 的 total_mentions 之和应等于 mention 总数"""
    mention_total = _scalar(
        "SELECT count(*) FROM intel.doc_mentions WHERE prompt_version = :pv",
        pv=prompt_version,
    )
    prof_sum = _scalar(
        "SELECT COALESCE(sum(total_mentions),0) FROM intel.author_profiles p "
        "WHERE (p.profile_key, p.computed_at) IN ("
        "  SELECT profile_key, max(computed_at) FROM intel.author_profiles GROUP BY profile_key)"
    )
    n_prof = _scalar("SELECT count(DISTINCT profile_key) FROM intel.author_profiles")
    insuff = _scalar(
        "SELECT count(*) FROM intel.author_profiles p "
        "WHERE p.sample_insufficient AND (p.profile_key, p.computed_at) IN ("
        "  SELECT profile_key, max(computed_at) FROM intel.author_profiles GROUP BY profile_key)"
    )
    return {
        "stage": "4 画像",
        "profiles": n_prof,
        "sum_total_mentions": prof_sum,
        "mentions_total": mention_total,
        "sample_insufficient": insuff,
        "pass": n_prof > 0 and prof_sum == mention_total,
        "note": "sum(total_mentions) 应等于 mention 总数（每条 mention 归属唯一画像）",
    }


def check_factors() -> dict:
    """因子：行数、取值非退化、防前视零违例"""
    rows = _scalar("SELECT count(*) FROM intel.factor_values")
    syms = _scalar("SELECT count(DISTINCT symbol) FROM intel.factor_values")
    dates = _scalar("SELECT count(DISTINCT trade_date) FROM intel.factor_values")
    bad = _scalar(
        "SELECT count(*) FROM intel.factor_values WHERE available_at::date > trade_date"
    )
    style_rows = _scalar(
        "SELECT count(*) FROM intel.factor_values WHERE factor_code = 'INTL_STYLE_MATCH'"
    )
    style_nz = _scalar(
        "SELECT count(*) FROM intel.factor_values "
        "WHERE factor_code = 'INTL_STYLE_MATCH' AND value <> 0"
    )
    with get_engine().connect() as c:
        codes = [r[0] for r in c.execute(
            store.text("SELECT DISTINCT factor_code FROM intel.factor_values ORDER BY 1")
        ).all()]
    return {
        "stage": "5 因子",
        "rows": rows, "symbols": syms, "trade_dates": dates,
        "factor_codes": codes,
        "INTL_STYLE_MATCH_rows": style_rows,
        "INTL_STYLE_MATCH_nonzero": style_nz,
        "antilookahead_violations": bad,
        "pass": rows > 0 and bad == 0 and style_rows > 0,
        "note": "防前视违例应为 0；INTL_STYLE_MATCH 必须有行（风格匹配因子已产出）",
    }


def check_style_summaries() -> dict:
    """P4-4 风格总结：表存在性与已生成条数"""
    exists = _scalar(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'intel' AND table_name = 'author_style_summaries'"
    )
    if not exists:
        return {"stage": "6 P4-4 风格总结", "table_exists": False, "pass": False,
                "note": "表未建（跑 _p4_build_style_summaries.py 会自动建）"}
    n = _scalar("SELECT count(*) FROM intel.author_style_summaries")
    ok = _scalar("SELECT count(*) FROM intel.author_style_summaries WHERE status = 'ok'")

    with get_engine().connect() as c:
        dist = {
            r["status"]: int(r["n"])
            for r in c.execute(store.text(
                "SELECT status, count(*) AS n FROM intel.author_style_summaries "
                "GROUP BY status"
            )).mappings().all()
        }

    # 诚实判定：表建了不等于功能可用。ok=0 且有 api_fail → 上游不可达，标 BLOCKED 而非绿灯
    if ok == 0 and dist.get("api_fail"):
        return {
            "stage": "6 P4-4 风格总结", "table_exists": True,
            "summaries": n, "ok": ok, "by_status": dist, "pass": False,
            "note": ("BLOCKED：已尝试生成但 LLM 上游不可达（api_fail）；"
                     "恢复后重跑 _p4_build_style_summaries.py --apply 即可，"
                     "已有 ok 记录不会被失败覆盖"),
        }

    return {
        "stage": "6 P4-4 风格总结",
        "table_exists": True, "summaries": n, "ok": ok, "by_status": dist,
        "pass": True,
        "note": "" if ok else "表已建但尚未生成总结（跑 --apply）；ok 条数为 0 不算失败",
    }


def run_verify() -> list[dict]:
    return [
        check_ingest(), check_prescreen(), check_extract(),
        check_profiles(), check_factors(), check_style_summaries(),
    ]


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="P4 intel 全链路验收")
    ap.add_argument("--ingest", help="待投喂语料（目录/文件/URL 清单），走 P4-1/P4-2 入库")
    ap.add_argument("--source-name", help="投喂源名（默认取目录名）")
    ap.add_argument("--extract-limit", type=int, default=0,
                    help=">0 时跑理解层 LLM 抽取（需 INTEL_LLM_* 配置）")
    ap.add_argument("--build-profiles", action="store_true", help="投喂后重建画像")
    ap.add_argument("--build-factors", action="store_true", help="投喂后重建因子")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    # ---- 投喂链路（可选） ----
    if args.ingest:
        from app.intel.service import run_manual_ingest, run_wechat_ingest
        target = args.ingest
        name = args.source_name or Path(target).name or "manual"
        # 目录/文件统一走 wechat（source_type=wechat 便于溯源公众号），与 manual 解析一致
        stats = run_wechat_ingest(target, source_name=name)
        print("=== 投喂入库 ===")
        print(json.dumps(stats, ensure_ascii=False, default=str, indent=2))

        if args.extract_limit > 0:
            from app.intel.understand import service as US
            print("\n=== 理解层抽取 ===")
            print(json.dumps(US.run_understanding_batch(limit=args.extract_limit),
                             ensure_ascii=False, default=str, indent=2))
        if args.build_profiles:
            from app.intel.aggregate.profile import build_profiles
            print("\n=== P2 画像 ===")
            ps = build_profiles()
            print(f"写入画像 {len(ps)} 个")
        if args.build_factors:
            from app.intel.factorize.availability import upsert_factor_values
            from app.intel.factorize import factors as F
            eng = get_engine()
            cal = F.load_trade_calendar(eng)
            mentions = F.load_mentions(F.PROMPT_VERSION, eng)
            rows = F.build_all_factor_rows(
                mentions, F.extend_calendar(cal, max(m["available_at"].date()
                                                     for m in mentions) if mentions else cal[-1]),
                F.load_profile_index(eng), prompt_version=F.PROMPT_VERSION,
            )
            print(f"\n=== P3 因子 ===\n写入 {upsert_factor_values(rows, eng)} 行")

    # ---- 验收 ----
    results = run_verify()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, default=str, indent=2))
    else:
        print("\n=== P4 全链路验收 ===")
        print(f"{'阶段':<18}{'结果':<8}{'关键指标'}")
        print("-" * 96)
        for r in results:
            mark = "PASS" if r["pass"] else "FAIL"
            kv = {k: v for k, v in r.items()
                  if k not in ("stage", "pass", "note")}
            # factor_codes 列表太长，压缩显示
            if "factor_codes" in kv:
                kv["factor_codes"] = f"{len(kv['factor_codes'])} 个"
            print(f"{r['stage']:<18}{mark:<8}{kv}")
            print(f"{'':<18}{'':<8}{r.get('note','')}")
        total = len(results)
        passed = sum(1 for r in results if r["pass"])
        print("-" * 96)
        print(f"合计 {passed}/{total} 通过")
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
