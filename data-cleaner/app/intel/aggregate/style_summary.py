"""P4-4 作者风格总结报告：云端 LLM 对单作者全量文档做风格综述，输出进画像卡片

与逐篇抽取（P1，量大利薄）不同，风格总结是**量小质高**的任务：
每个作者一次调用，输入是其聚合统计 + 近期观点样本，输出一段自然语言综述 + 结构化标签。
成本可控（作者数量级而非文档数量级），故用云端 LLM 而非本地小模型。

落点：``intel.author_style_summaries``（独立于 author_profiles，按 summary_version +
prompt_version 版本化不覆盖）。后端 ``/api/v1/intel/profiles`` LEFT JOIN 最新一条进画像卡片。

纪律：
- 版本化：改 prompt 改 STYLE_PROMPT_VERSION，改逻辑改 SUMMARY_VERSION，旧结果不覆盖
- 成本可查：每次调用写 ``intel.llm_runs``（prompt_version=style_v1）+ 本表留成本快照
- 预算闸：LLMClient 内置日预算闸，超限停批并告警（不静默降级）
- 样本红线：mentions < min_mentions 的作者跳过（不浪费钱，status=skipped）
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.store import get_engine
from sqlalchemy import text

logger = get_logger(__name__)

# 版本纪律：改 prompt → STYLE_PROMPT_VERSION 升版；改总结逻辑 → SUMMARY_VERSION 升版
STYLE_PROMPT_VERSION = "style_v1"
SUMMARY_VERSION = "v1"

DEFAULT_MIN_MENTIONS = 3        # 低于此不送审（样本太少总结不可信）
DEFAULT_SAMPLE_THESIS = 40      # 每位作者送审的观点样本条数（控 token）
_MIGRATION = "migrations/016_intel_style_summaries.sql"

STYLE_SYSTEM_PROMPT = """你是 A 股投研作者风格分析师。给定一位作者的历史观点统计与观点样本，总结其投资风格。规则：
1. 只依据给定的统计与样本归纳，**禁止编造**样本之外的经历、机构、业绩数字。
2. summary 是 2-4 句中文综述，覆盖：偏好的标的/行业、观点倾向（多/空/中性）、持仓周期、论证方式（基本面/事件驱动/技术面）。
3. style_tags 从这些风格标签里挑最贴切的 1-4 个（不要自创）：价值投资、成长股、逆向布局、趋势跟随、事件驱动、短线博弈、行业景气、龙头偏好、困境反转、政策解读、量化统计、宏观择时。
4. sectors 是作者主要覆盖的行业（中文，2-6 个），按提及热度降序。
5. holding_period 只能取 short / mid / long / event 之一；无法判断填 event。
6. conviction 是作者表达观点的笃定程度（high / medium / low），不是观点正确率。
7. caveats 写这条画像的局限（如样本少、时间集中、单一行业、样本窗口短），1-2 句；无局限填空字符串。
8. confidence 是你对本次总结可靠程度的评分（0-1 小数）；样本少于 10 条时不超过 0.5。
9. 只输出 JSON，不要任何解释、markdown 代码块。格式：
{"summary": "...", "style_tags": ["价值投资"], "sectors": ["银行"], "holding_period": "mid", "conviction": "medium", "caveats": "...", "confidence": 0.6}"""

STYLE_USER_TMPL = """作者：{profile_key}（{profile_type}）

统计：
{stats}

观点样本（最近 {n} 条，symbol | stance | horizon | thesis）：
{samples}

按系统规则输出 JSON。"""


# --------------------------------------------------------------------------
# 表与数据加载
# --------------------------------------------------------------------------
def ensure_style_summary_table(engine=None) -> None:
    """幂等建表（走 run_sql_file 原生执行，绕开 text() 对注释冒号的 bind 解析）"""
    from app.intel import store
    path = Path(_MIGRATION)
    if not path.exists():  # 允许从仓库根运行时定位
        path = Path(__file__).resolve().parents[3] / _MIGRATION
    store.run_sql_file(str(path), engine)


def load_profiles(profile_version: str = "v1", engine=None) -> list[dict]:
    """取指定 profile_version 的画像（每个 key 取**最新 as_of** 的一条）

    D-9 起按 as_of 版本化：历史行不再被覆盖，故"最新"必须按 as_of 判定
    （computed_at 只是写入时间，同日重跑会刷新）。
    """
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT profile_key, profile_type, total_mentions, total_docs,
                       unique_symbols, date_first, date_last, stance_dist,
                       style_vector, top_symbols, horizon_dist,
                       avg_excess_20d, accuracy_sample_size, win_rate_20d,
                       sample_insufficient, drift_detected
                FROM intel.author_profiles p
                WHERE p.profile_version = :pv
                  AND (p.profile_key, p.as_of) IN (
                      SELECT profile_key, max(as_of)
                      FROM intel.author_profiles
                      WHERE profile_version = :pv
                      GROUP BY profile_key
                  )
                ORDER BY p.total_mentions DESC
            """),
            {"pv": profile_version},
        ).mappings().all()
    return [dict(r) for r in rows]


def fetch_author_mentions(
    profile_key: str, profile_type: str,
    prompt_version: str = "v2", limit: int = DEFAULT_SAMPLE_THESIS,
    engine=None,
) -> list[dict]:
    """取该作者/源的 mention 样本（最近 limit 条），供 LLM 归纳风格"""
    if engine is None:
        engine = get_engine()
    if profile_type == "author":
        where = "d.author = :key"
    else:
        where = "COALESCE(s.name, '') = :key"
    sql = f"""
        SELECT m.symbol, m.stance, m.horizon, m.thesis, d.title, d.published_at
        FROM intel.doc_mentions m
        JOIN intel.documents d ON d.id = m.doc_id
        LEFT JOIN intel.sources s ON s.id = d.source_id
        WHERE m.prompt_version = :pv AND {where}
        ORDER BY d.published_at DESC NULLS LAST, m.id DESC
        LIMIT :lim
    """
    with engine.connect() as c:
        rows = c.execute(
            text(sql), {"pv": prompt_version, "key": profile_key, "lim": limit}
        ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Prompt 构造
# --------------------------------------------------------------------------
def _fmt_stats(p: dict) -> str:
    """把画像统计压成人类可读（也是 LLM 输入）文本"""
    def j(v):
        if v is None:
            return {}
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return {}

    stance = j(p.get("stance_dist"))
    horizon = j(p.get("horizon_dist"))
    tops = [f"{t.get('symbol')}({t.get('count')})"
            for t in (j(p.get("top_symbols")) or []) if isinstance(t, dict)][:8]
    acc = p.get("avg_excess_20d")
    win = p.get("win_rate_20d")
    d1 = p.get("date_first")
    d2 = p.get("date_last")
    span = ""
    if d1 and d2:
        span = f"{str(d1)[:10]} ~ {str(d2)[:10]}"
    return "\n".join([
        f"- 观点数 {p.get('total_mentions')}，文章数 {p.get('total_docs')}，"
        f"涉及标的 {p.get('unique_symbols')} 个" + (f"，时间跨度 {span}" if span else ""),
        f"- 倾向分布 看多{stance.get('bullish', 0)} / 中性{stance.get('neutral', 0)} / "
        f"看空{stance.get('bearish', 0)}",
        f"- 周期分布 {json.dumps(horizon, ensure_ascii=False)}",
        f"- 高频标的 {', '.join(tops) if tops else '无'}"
        + (f"，20日平均超额 {acc}%（胜率 {win}）"
           if acc is not None else ""),
        f"- 样本不足标记 {'是' if p.get('sample_insufficient') else '否'}，"
        f"风格漂移 {'有' if p.get('drift_detected') else '无'}",
    ])


def _fmt_samples(mentions: list[dict]) -> str:
    out = []
    for m in mentions:
        th = (m.get("thesis") or "").strip().replace("\n", " ")
        out.append(f"{m.get('symbol')} | {m.get('stance')} | {m.get('horizon')} | {th}")
    return "\n".join(out) if out else "（无样本）"


def build_style_prompt(profile: dict, mentions: list[dict]) -> tuple[str, str]:
    """返回 (system, user)"""
    user = STYLE_USER_TMPL.format(
        profile_key=profile.get("profile_key"),
        profile_type=profile.get("profile_type"),
        stats=_fmt_stats(profile),
        n=len(mentions),
        samples=_fmt_samples(mentions),
    )
    return STYLE_SYSTEM_PROMPT, user


# --------------------------------------------------------------------------
# 输出解析与校验
# --------------------------------------------------------------------------
def _extract_json(raw: str) -> dict:
    """容忍 markdown 围栏 / 前后废话：取首个 { 到末个 }"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("未找到 JSON 对象")
    return json.loads(s[i:j + 1])


_ALLOWED_TAGS = {
    "价值投资", "成长股", "逆向布局", "趋势跟随", "事件驱动", "短线博弈",
    "行业景气", "龙头偏好", "困境反转", "政策解读", "量化统计", "宏观择时",
}
_ALLOWED_HOLDING = {"short", "mid", "long", "event"}
_ALLOWED_CONVICTION = {"high", "medium", "low"}


def _as_str_list(v, limit: int) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:24])
        if len(out) >= limit:
            break
    return out


def parse_style_summary(raw: str) -> dict:
    """解析并校验 LLM 输出。不合规抛 ValueError（调用方记 schema_fail）。

    summary 是唯一必填（画像卡片要展示），其余字段缺失则给安全默认值。
    """
    d = _extract_json(raw)
    summary = d.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 4:
        raise ValueError("summary 缺失或过短")
    summary = summary.strip()[:2000]

    tags = [t for t in _as_str_list(d.get("style_tags"), 6) if t in _ALLOWED_TAGS][:4]
    sectors = _as_str_list(d.get("sectors"), 8)[:6]
    holding = d.get("holding_period")
    holding = holding if holding in _ALLOWED_HOLDING else None
    conviction = d.get("conviction")
    conviction = conviction if conviction in _ALLOWED_CONVICTION else None
    caveats = d.get("caveats")
    caveats = caveats.strip()[:1000] if isinstance(caveats, str) else None
    conf = d.get("confidence")
    try:
        conf = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    if conf is not None:
        conf = max(0.0, min(1.0, conf))

    return {
        "summary": summary,
        "style_tags": tags,
        "sectors": sectors,
        "holding_period": holding,
        "conviction": conviction,
        "caveats": caveats,
        "confidence": conf,
    }


# --------------------------------------------------------------------------
# 落库
# --------------------------------------------------------------------------
def upsert_style_summary(rec: dict, engine=None, protect_ok: bool = True) -> bool:
    """版本化 upsert：同 (key, type, summary_version) 覆盖，旧版本保留

    protect_ok（默认 True）：status != 'ok' 的记录**不覆盖**已存在的 'ok' 记录。
    否则一次 LLM 网络抖动 / 上游 502 就会把已生成的好总结洗成 api_fail，
    等网络恢复前前端只能看到空白。失败只记日志，数据留在库里。
    返回是否实际写入（False = 被保护跳过）。
    """
    if engine is None:
        engine = get_engine()
    with engine.begin() as c:
        if protect_ok and rec.get("status") != "ok":
            prev = c.execute(
                text("""
                    SELECT status FROM intel.author_style_summaries
                    WHERE profile_key = :k AND profile_type = :t
                      AND summary_version = :v
                """),
                {"k": rec["profile_key"], "t": rec["profile_type"],
                 "v": rec["summary_version"]},
            ).scalar()
            if prev == "ok":
                logger.warning(
                    f"P4-4 保留已有成功总结，不覆盖为 {rec.get('status')}："
                    f"{rec['profile_key']}"
                )
                return False
        c.execute(
            text("""
                INSERT INTO intel.author_style_summaries
                    (profile_key, profile_type, summary_version, prompt_version,
                     summary, style_tags, sectors, holding_period, conviction,
                     caveats, confidence, input_stats, sample_size,
                     model, input_tokens, output_tokens, cost_cny, latency_ms,
                     status, error)
                VALUES
                    (:profile_key, :profile_type, :summary_version, :prompt_version,
                     :summary, :style_tags, :sectors, :holding_period, :conviction,
                     :caveats, :confidence, :input_stats, :sample_size,
                     :model, :input_tokens, :output_tokens, :cost_cny, :latency_ms,
                     :status, :error)
                ON CONFLICT (profile_key, profile_type, summary_version)
                DO UPDATE SET
                    prompt_version = EXCLUDED.prompt_version,
                    summary        = EXCLUDED.summary,
                    style_tags     = EXCLUDED.style_tags,
                    sectors        = EXCLUDED.sectors,
                    holding_period = EXCLUDED.holding_period,
                    conviction     = EXCLUDED.conviction,
                    caveats        = EXCLUDED.caveats,
                    confidence     = EXCLUDED.confidence,
                    input_stats    = EXCLUDED.input_stats,
                    sample_size    = EXCLUDED.sample_size,
                    model          = EXCLUDED.model,
                    input_tokens   = EXCLUDED.input_tokens,
                    output_tokens  = EXCLUDED.output_tokens,
                    cost_cny       = EXCLUDED.cost_cny,
                    latency_ms     = EXCLUDED.latency_ms,
                    status         = EXCLUDED.status,
                    error          = EXCLUDED.error,
                    computed_at    = now()
            """),
            rec,
        )
    return True


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def build_style_summaries(
    profile_version: str = "v1",
    summary_version: str = SUMMARY_VERSION,
    prompt_version: str = "v2",
    *,
    authors: list[str] | None = None,
    limit: int | None = None,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
    sample_size: int = DEFAULT_SAMPLE_THESIS,
    client=None,
    dry_run: bool = False,
    engine=None,
) -> dict:
    """P4-4 主入口：为每位作者生成风格总结并落 intel.author_style_summaries。

    - authors 只跑指定的 profile_key；limit 限制作者数（成本控制）
    - min_mentions 以下的作者 skip（status=skipped，不调 LLM）
    - dry_run 只返回将要送审的 prompt，不调 LLM 不落库
    - 预算超限时停批（BudgetExceeded 上抛前先记日志，不静默降级）
    返回汇总 dict。
    """
    if engine is None:
        engine = get_engine()
    ensure_style_summary_table(engine)

    profiles = load_profiles(profile_version, engine)
    if authors:
        profiles = [p for p in profiles if p["profile_key"] in set(authors)]
    if limit:
        profiles = profiles[:limit]

    out = {
        "profile_version": profile_version, "summary_version": summary_version,
        "prompt_version": prompt_version, "candidates": len(profiles),
        "ok": 0, "skipped": 0, "failed": 0, "kept_previous": 0, "cost_cny": 0.0,
        "dry_run": dry_run, "details": [],
    }

    for p in profiles:
        key, ptype = p["profile_key"], p["profile_type"]
        mentions = fetch_author_mentions(
            key, ptype, prompt_version=prompt_version,
            limit=sample_size, engine=engine,
        )
        system, user = build_style_prompt(p, mentions)

        if dry_run:
            would_skip = (p.get("total_mentions") or 0) < min_mentions
            out["details"].append({
                "profile_key": key, "profile_type": ptype,
                "total_mentions": p.get("total_mentions"),
                "sample_size": len(mentions),
                "status": "dry_run_skip" if would_skip else "dry_run",
                "user_prompt_chars": len(user),
            })
            if would_skip:
                out["skipped"] += 1
                out["ok"] += 0
            continue

        # 样本红线
        if (p.get("total_mentions") or 0) < min_mentions:
            upsert_style_summary({
                "profile_key": key, "profile_type": ptype,
                "summary_version": summary_version,
                "prompt_version": STYLE_PROMPT_VERSION,
                "summary": None, "style_tags": json.dumps([]),
                "sectors": json.dumps([]), "holding_period": None,
                "conviction": None, "caveats": None, "confidence": None,
                "input_stats": json.dumps({
                    "total_mentions": p.get("total_mentions"),
                    "total_docs": p.get("total_docs"),
                }, ensure_ascii=False),
                "sample_size": len(mentions), "model": None,
                "input_tokens": 0, "output_tokens": 0, "cost_cny": 0.0,
                "latency_ms": 0, "status": "skipped",
                "error": f"mentions={p.get('total_mentions')} < min_mentions={min_mentions}",
            }, engine)
            out["skipped"] += 1
            out["details"].append({"profile_key": key, "status": "skipped"})
            logger.info(f"P4-4 跳过 {key}：样本不足（<{min_mentions}）")
            continue

        # 调 LLM
        if client is None:
            from app.intel.understand.llm.client import LLMClient
            client = LLMClient()
        try:
            resp = client.extract_once(
                system, user, doc_id=None, prompt_version=STYLE_PROMPT_VERSION,
            )
        except Exception as e:  # noqa: BLE001
            is_budget = "BudgetExceeded" in type(e).__name__
            logger.error(
                f"P4-4 LLM 调用失败 {key}（{'预算闸' if is_budget else 'API'}）: {e}",
                extra={"task": "intel_style_summary"},
            )
            written = upsert_style_summary({
                "profile_key": key, "profile_type": ptype,
                "summary_version": summary_version,
                "prompt_version": STYLE_PROMPT_VERSION,
                "summary": None, "style_tags": json.dumps([]),
                "sectors": json.dumps([]), "holding_period": None,
                "conviction": None, "caveats": None, "confidence": None,
                "input_stats": json.dumps(
                    {"total_mentions": p.get("total_mentions")}, ensure_ascii=False),
                "sample_size": len(mentions), "model": None,
                "input_tokens": 0, "output_tokens": 0, "cost_cny": 0.0,
                "latency_ms": 0, "status": "api_fail",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }, engine)
            out["failed"] += 1
            out["kept_previous"] += 0 if written else 1
            out["details"].append({
                "profile_key": key,
                "status": "api_fail" if written else "api_fail_kept_previous",
                "error": str(e)[:200],
            })
            if is_budget:
                out["budget_exceeded"] = True
                break  # 停批，不静默降级
            continue

        # 解析校验
        try:
            parsed = parse_style_summary(resp.content)
        except Exception as e:  # noqa: BLE001
            written = upsert_style_summary({
                "profile_key": key, "profile_type": ptype,
                "summary_version": summary_version,
                "prompt_version": STYLE_PROMPT_VERSION,
                "summary": None, "style_tags": json.dumps([]),
                "sectors": json.dumps([]), "holding_period": None,
                "conviction": None, "caveats": None, "confidence": None,
                "input_stats": json.dumps(
                    {"total_mentions": p.get("total_mentions")}, ensure_ascii=False),
                "sample_size": len(mentions), "model": resp.model,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "cost_cny": resp.cost_cny, "latency_ms": resp.latency_ms,
                "status": "schema_fail",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }, engine)
            out["failed"] += 1
            out["kept_previous"] += 0 if written else 1
            out["cost_cny"] += float(resp.cost_cny or 0)
            out["details"].append({
                "profile_key": key,
                "status": "schema_fail" if written else "schema_fail_kept_previous",
                "error": str(e)[:200],
            })
            logger.warning(f"P4-4 输出不合规 {key}: {e}")
            continue

        upsert_style_summary({
            "profile_key": key, "profile_type": ptype,
            "summary_version": summary_version,
            "prompt_version": STYLE_PROMPT_VERSION,
            "summary": parsed["summary"],
            "style_tags": json.dumps(parsed["style_tags"], ensure_ascii=False),
            "sectors": json.dumps(parsed["sectors"], ensure_ascii=False),
            "holding_period": parsed["holding_period"],
            "conviction": parsed["conviction"],
            "caveats": parsed["caveats"],
            "confidence": parsed["confidence"],
            "input_stats": json.dumps({
                "total_mentions": p.get("total_mentions"),
                "total_docs": p.get("total_docs"),
                "unique_symbols": p.get("unique_symbols"),
                "sample_insufficient": p.get("sample_insufficient"),
            }, ensure_ascii=False),
            "sample_size": len(mentions), "model": resp.model,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
            "cost_cny": resp.cost_cny, "latency_ms": resp.latency_ms,
            "status": "ok", "error": None,
        }, engine)
        out["ok"] += 1
        out["cost_cny"] += float(resp.cost_cny or 0)
        out["details"].append({
            "profile_key": key, "status": "ok",
            "cost_cny": resp.cost_cny, "latency_ms": resp.latency_ms,
            "tags": parsed["style_tags"],
        })
        logger.info(
            f"P4-4 风格总结完成 {key}: tags={parsed['style_tags']} "
            f"cost={resp.cost_cny:.4f}元",
            extra={"task": "intel_style_summary"},
        )

    out["cost_cny"] = round(out["cost_cny"], 4)
    return out


def latest_summaries(engine=None) -> list[dict]:
    """取每位作者最新一条总结（后端画像卡片用）"""
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT profile_key, profile_type, summary, style_tags, sectors,
                       holding_period, conviction, caveats, confidence,
                       prompt_version, summary_version, status, cost_cny, computed_at
                FROM intel.author_style_summaries s
                WHERE (s.profile_key, s.computed_at) IN (
                    SELECT profile_key, max(computed_at)
                    FROM intel.author_style_summaries
                    GROUP BY profile_key
                )
            """)
        ).mappings().all()
    return [dict(r) for r in rows]
