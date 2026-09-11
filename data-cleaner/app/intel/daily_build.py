"""intel 每日构建编排：抽取（P1）→ 画像（P2）→ 因子（P3）→ 风格总结（P4-4）

把原先只打日志的 19:30 空占位（``intel_daily_build``）实装成真正跑的链路。
本模块**只做编排与统计**，算法各自留在 ``understand/``、``aggregate/``、``factorize/``。

纪律：
- **各步互相隔离**：任一步抛异常只记 ``errors``，后续步骤照跑（抽取挂了不代表
  画像/因子不能刷新；反之亦然）。
- **花钱的两步是抽取与风格总结**：LLM 未配置 → ``skipped``（不是失败）；日预算用尽由
  BudgetGate 在客户端侧中止，摘要里体现为 ``stopped_reason="budget"``。
- **不静默**：抽不完（limit 截断）、防前视违例、因子 0 行都写进摘要，调用方照抄日志。
- 因子变更广播（WS）由调用方（``app.intel.tasks``）负责——emit 必须在事件循环
  线程里做，本模块是同步函数，不碰 WS。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.intel.core.config import settings
from app.intel.core.logging import get_logger

logger = get_logger(__name__)

# 六因子 code（广播 factor_updated 时用；与 factorize/factors.py 保持一致）
FACTOR_CODES: tuple[str, ...] = (
    "INTL_MENTION_HEAT_5",
    "INTL_SENTIMENT_10",
    "INTL_RESONANCE_5",
    "INTL_FIRST_MENTION",
    "INTL_AUTHOR_CONVICTION",
    "INTL_STYLE_MATCH",
)

PROFILE_VERSION = "v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _llm_configured() -> bool:
    return bool(
        getattr(settings, "INTEL_LLM_BASE_URL", "")
        and getattr(settings, "INTEL_LLM_API_KEY", "")
        and getattr(settings, "INTEL_LLM_MODEL", "")
    )


# ---------------------------------------------------------------- 单步（可被测试打桩）

def _run_extract(limit: int) -> dict:
    """P1 理解层抽取（花钱）。未配置 LLM 时由调用方提前拦下。"""
    from app.intel.understand import service as usvc

    return usvc.run_understanding_batch(limit=limit)


def _run_profiles(
    prompt_version: str, profile_version: str, engine=None, as_of=None
) -> dict:
    """P2 画像构建（零 LLM）。

    D-9：as_of 是「画像知识截止日」，并入唯一键 ⇒ 跨天重算新增历史行而非覆盖。
    定时任务不传 as_of = 取今天（Asia/Shanghai），即"今天的画像"。
    """
    from app.intel.aggregate.profile import build_profiles

    profiles = build_profiles(
        prompt_version=prompt_version,
        profile_version=profile_version,
        as_of=as_of,
        engine=engine,
    )
    sufficient = sum(1 for p in profiles if not p.get("sample_insufficient"))
    return {
        "profiles": len(profiles),
        "sample_sufficient": sufficient,
        "sample_insufficient": len(profiles) - sufficient,
    }


def build_factors(
    *,
    prompt_version: str,
    engine=None,
    dry_run: bool = False,
) -> dict:
    """P3 因子构建 + 落库（零 LLM，幂等）。

    与 ``_p3_build_factors.py`` 同一套逻辑（CLI 已改为调用本函数），避免
    "脚本算一套、定时任务算另一套"的分叉。

    Returns:
        ``{"mentions","rows","written","symbols","trade_dates","calendar_extended",
          "calendar_days","profiles","anti_lookahead_violations","codes","by_code"}``
    """
    from app.intel.factorize import factors as F
    from app.intel.factorize.availability import compute_available_at, upsert_factor_values

    if engine is None:
        from app.intel.store import get_engine

        engine = get_engine()

    cal = F.load_trade_calendar(engine)
    mentions = F.load_mentions(prompt_version, engine)

    # raw_bars 行情 T+0 滞后 → 不外推日历会让"近期"mention 全部被丢弃（378→0 行）
    maxd = None
    for m in mentions:
        av = compute_available_at(m.get("published_at"), m.get("ingested_at"))
        if av is not None and (maxd is None or av.date() > maxd):
            maxd = av.date()
    n0 = len(cal)
    if maxd and cal:
        cal = F.extend_calendar(cal, maxd + timedelta(days=1), max_extra=10)

    profiles = F.load_profile_index(engine)
    rows = F.build_all_factor_rows(mentions, cal, profiles, prompt_version=prompt_version)

    by_code: dict[str, dict[str, Any]] = {}
    for r in rows:
        st = by_code.setdefault(
            r["factor_code"], {"rows": 0, "nonzero": 0, "min": None, "max": None, "sum": 0.0}
        )
        v = float(r["value"])
        st["rows"] += 1
        st["nonzero"] += 1 if v != 0 else 0
        st["sum"] += v
        st["min"] = v if st["min"] is None else min(st["min"], v)
        st["max"] = v if st["max"] is None else max(st["max"], v)
    for st in by_code.values():
        st["mean"] = round(st["sum"] / st["rows"], 6) if st["rows"] else 0.0
        st.pop("sum", None)

    # 防前视自检：available_at 不得晚于该行的 trade_date 收盘
    violations = [r for r in rows if r["available_at"].date() > r["trade_date"]]
    if violations:
        logger.warning(
            f"P3 防前视自检违例 {len(violations)} 行（available_at 晚于 trade_date）"
            + "".join(
                f" | {r['symbol']} {r['trade_date']} {r['available_at']}"
                for r in violations[:3]
            ),
            extra={"task": "intel_daily_build"},
        )

    written = 0 if dry_run else upsert_factor_values(rows, engine)
    return {
        "mentions": len(mentions),
        "rows": len(rows),
        "written": written,
        "dry_run": dry_run,
        "symbols": len({r["symbol"] for r in rows}),
        "trade_dates": len({r["trade_date"] for r in rows}),
        "calendar_extended": len(cal) > n0,
        "calendar_days": len(cal),
        "profiles": len(profiles),
        "anti_lookahead_violations": len(violations),
        "codes": sorted({r["factor_code"] for r in rows}),
        "by_code": by_code,
    }


def _run_style_summaries(
    prompt_version: str,
    profile_version: str,
    *,
    limit: int | None = None,
    engine=None,
) -> dict:
    """P4-4 风格总结（花钱，受 BudgetGate 约束）。

    D-12：并入 19:30 每日构建 —— 此前只能手动跑 ``_p4_build_style_summaries.py``，
    导致新出现的作者永远没有风格总结（实测"散户森" 332 mentions 却漏了总结）。

    成本可控性：
    - 低于 ``DEFAULT_MIN_MENTIONS``（3）的作者直接 skip，**不调 LLM**（零成本）；
    - 每个作者一次调用，预算闸逐调用拦截 ⇒ 不会超支（与抽取共用同一份日预算）；
    - 已 ok 的作者默认仍会重算（画像变了总结也该刷新），可用 limit 限制本轮数量。
    """
    from app.intel.aggregate.style_summary import build_style_summaries

    return build_style_summaries(
        profile_version=profile_version,
        prompt_version=prompt_version,
        limit=limit,
        engine=engine,
    )


# ---------------------------------------------------------------- 编排

def run_daily_build(
    *,
    do_extract: bool | None = None,
    do_profiles: bool | None = None,
    do_factors: bool = True,
    do_styles: bool | None = None,
    extract_limit: int | None = None,
    style_limit: int | None = None,
    prompt_version: str | None = None,
    profile_version: str = PROFILE_VERSION,
    dry_run_factors: bool = False,
    engine=None,
) -> dict:
    """跑一轮每日构建。同步函数（重活），由调度器丢进 executor。

    Returns:
        ``{"started_at","finished_at","steps":{"extract","profiles","factors","styles"},
          "errors","cost_cny","factor_codes","emitted":False}``
        —— ``factor_codes`` 供调用方广播；本函数不发 WS。
    """
    from app.intel.factorize import factors as F

    pv = prompt_version or F.PROMPT_VERSION
    limit = int(extract_limit if extract_limit is not None
                else getattr(settings, "INTEL_DAILY_BUILD_EXTRACT_LIMIT", 200))
    # None = 跟随配置（定时任务走这条）；显式 True/False 优先（CLI / 测试）
    if do_extract is None:
        do_extract = bool(getattr(settings, "INTEL_DAILY_BUILD_EXTRACT", True))
    if do_profiles is None:
        do_profiles = bool(getattr(settings, "INTEL_DAILY_BUILD_PROFILES", True))
    if do_styles is None:
        do_styles = bool(getattr(settings, "INTEL_DAILY_BUILD_STYLES", False))
    if style_limit is None:
        cfg_lim = getattr(settings, "INTEL_DAILY_BUILD_STYLE_LIMIT", None)
        style_limit = int(cfg_lim) if cfg_lim else None

    out: dict[str, Any] = {
        "started_at": _now(),
        "finished_at": None,
        "steps": {},
        "errors": [],
        "cost_cny": 0.0,
        "factor_codes": [],
        "emitted": False,
    }

    # ---- Step 1 抽取（唯一花钱的一步）----
    if do_extract:
        if not _llm_configured():
            out["steps"]["extract"] = {
                "status": "skipped", "reason": "INTEL_LLM_* 未配置", "limit": limit
            }
        else:
            try:
                s = _run_extract(limit)
                out["steps"]["extract"] = {"status": "ok", "limit": limit, **s}
                out["cost_cny"] += float(s.get("cost_cny") or 0.0)
                if s.get("stopped_reason") == "budget":
                    out["steps"]["extract"]["status"] = "budget_stopped"
            except Exception as e:  # noqa: BLE001 —— 单步失败不拖垮整轮
                out["steps"]["extract"] = {
                    "status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}",
                    "limit": limit,
                }
                out["errors"].append(f"extract: {type(e).__name__}: {str(e)[:200]}")
                logger.error(f"intel 每日构建-抽取失败: {type(e).__name__}: {e}",
                             extra={"task": "intel_daily_build"})
    else:
        out["steps"]["extract"] = {"status": "skipped", "reason": "本次不跑抽取"}

    # ---- Step 2 画像（零 LLM）----
    if do_profiles:
        try:
            out["steps"]["profiles"] = {
                "status": "ok",
                **_run_profiles(pv, profile_version, engine),
            }
        except Exception as e:  # noqa: BLE001
            out["steps"]["profiles"] = {
                "status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"
            }
            out["errors"].append(f"profiles: {type(e).__name__}: {str(e)[:200]}")
            logger.error(f"intel 每日构建-画像失败: {type(e).__name__}: {e}",
                         extra={"task": "intel_daily_build"})
    else:
        out["steps"]["profiles"] = {"status": "skipped", "reason": "本次不跑画像"}

    # ---- Step 3 因子（零 LLM，幂等）----
    if do_factors:
        try:
            out["steps"]["factors"] = {
                "status": "ok",
                **build_factors(prompt_version=pv, engine=engine, dry_run=dry_run_factors),
            }
            codes = out["steps"]["factors"].get("codes") or []
            out["factor_codes"] = [c for c in FACTOR_CODES if c in codes] or sorted(codes)
        except Exception as e:  # noqa: BLE001
            out["steps"]["factors"] = {
                "status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"
            }
            out["errors"].append(f"factors: {type(e).__name__}: {str(e)[:200]}")
            logger.error(f"intel 每日构建-因子失败: {type(e).__name__}: {e}",
                         extra={"task": "intel_daily_build"})
    else:
        out["steps"]["factors"] = {"status": "skipped", "reason": "本次不跑因子"}

    # ---- Step 4 风格总结（花钱，BudgetGate 约束）----
    # D-12：并入每日构建。放在最后 —— 前一步挂了也不影响这步（各步隔离），
    # 且预算闸与抽取共享同一份日预算，抽取先用、剩余给风格总结。
    if do_styles:
        if not _llm_configured():
            out["steps"]["styles"] = {
                "status": "skipped", "reason": "INTEL_LLM_* 未配置",
            }
        else:
            try:
                s = _run_style_summaries(
                    pv, profile_version, limit=style_limit, engine=engine
                )
                out["steps"]["styles"] = {"status": "ok", **s}
                out["cost_cny"] += float(s.get("cost_cny") or 0.0)
            except Exception as e:  # noqa: BLE001 —— 单步失败不拖垮整轮
                out["steps"]["styles"] = {
                    "status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"
                }
                out["errors"].append(f"styles: {type(e).__name__}: {str(e)[:200]}")
                logger.error(f"intel 每日构建-风格总结失败: {type(e).__name__}: {e}",
                             extra={"task": "intel_daily_build"})
    else:
        out["steps"]["styles"] = {"status": "skipped", "reason": "本次不跑风格总结"}

    out["cost_cny"] = round(float(out["cost_cny"]), 6)
    out["finished_at"] = _now()
    return out


def format_summary(summary: dict) -> str:
    """一行人读摘要（日志/CLI 用）。"""
    ex = summary["steps"].get("extract", {})
    pr = summary["steps"].get("profiles", {})
    fa = summary["steps"].get("factors", {})
    st = summary["steps"].get("styles", {})
    return (
        f"抽取[{ex.get('status')}] 命中={ex.get('prescreen_hit')} "
        f"抽出={ex.get('extracted')} 隔离={ex.get('quarantined')} "
        f"失败={ex.get('api_fail')} 成本=¥{float(ex.get('cost_cny') or 0):.4f}｜"
        f"画像[{pr.get('status')}] {pr.get('profiles', 0)} 个｜"
        f"因子[{fa.get('status')}] {fa.get('written', fa.get('rows', 0))} 行 "
        f"{fa.get('symbols', 0)} 标的 违例={fa.get('anti_lookahead_violations', 0)}｜"
        f"风格[{st.get('status')}] ok={st.get('ok', 0)} skip={st.get('skipped', 0)} "
        f"fail={st.get('failed', 0)} 成本=¥{float(st.get('cost_cny') or 0):.4f}"
    )
