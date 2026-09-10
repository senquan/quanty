"""P2 画像聚合：从 doc_mentions + documents + raw_bars 聚合作者/源画像

核心逻辑：
1. 按 author（fallback 到 source）分组聚合 mention 统计
2. 从 factor.raw_bars 只读计算超额收益（20d/60d）
3. 时间加权风格向量（半衰期 180 天）
4. 风格漂移检测（近 90 天 vs 全量）
5. 样本量红线：<30 mentions → sample_insufficient=true

入口：build_profiles(prompt_version, profile_version) → 写入 intel.author_profiles
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta

from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.store import get_engine
from sqlalchemy import text

logger = get_logger(__name__)

# 常量
HALF_LIFE_DAYS = 180  # 风格向量时间加权半衰期
SAMPLE_THRESHOLD = 30  # 画像最低样本量（P2-5 红线：mentions 与 accuracy 样本均须 ≥ 此值）
EXCESS_WINDOWS = [20, 60]  # 超额收益窗口（交易日）


def is_sample_sufficient(
    total_mentions: int,
    accuracy_sample: int,
    threshold: int = SAMPLE_THRESHOLD,
) -> bool:
    """样本量红线（P2-5）：mentions 与 accuracy 样本任一 < threshold 即样本不足。

    决定画像卡片的准确度字段显示数字还是「样本不足」。抽成纯函数便于单测。
    """
    return total_mentions >= threshold and accuracy_sample >= threshold


# 基准指数：中证全指（000985.SZ）。
# 注意：raw_bars 里没有 000300.SH（沪深300），实测仅有 000985.SZ 覆盖全日历（1194 天）。
# FREQ_DAILY 同理：raw_bars 的 freq 取值是 '1d'，不是 'daily'（与 backfill_0902.py /
# app/backtest/data.py / app/storage/raw_store.py 保持一致）。写错会静默返回 0 行。
BENCHMARK_SYMBOL = "000985.SZ"
FREQ_DAILY = "1d"


def _fetch_mentions_with_docs(
    prompt_version: str, engine=None,
) -> list[dict]:
    """取指定 prompt_version 的全部 mention，JOIN documents 取 author/source/published_at"""
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT
                    m.id AS mention_id,
                    m.doc_id,
                    m.symbol,
                    m.stance,
                    m.confidence,
                    m.horizon,
                    m.thesis,
                    m.evidence,
                    d.author,
                    d.source_id,
                    s.name AS source_name,
                    d.published_at,
                    d.ingested_at,
                    d.title
                FROM intel.doc_mentions m
                JOIN intel.documents d ON d.id = m.doc_id
                LEFT JOIN intel.sources s ON s.id = d.source_id
                WHERE m.prompt_version = :pv
                ORDER BY d.published_at, m.id
            """),
            {"pv": prompt_version},
        ).mappings().all()
    return [dict(r) for r in rows]


def _profile_key(row: dict) -> tuple[str, str]:
    """确定画像主键：(key, type)

    author 非空 → (author, 'author')
    author 为空 → (source_name or f'source_{source_id}', 'source')
    """
    author = (row.get("author") or "").strip()
    if author:
        return author, "author"
    src_name = (row.get("source_name") or "").strip()
    src_id = row.get("source_id")
    key = src_name if src_name else f"source_{src_id}"
    return key, "source"


def _time_weight(published_at, ref_time: datetime | None = None) -> float:
    """指数衰减时间权重：越新权重越高，半衰期 HALF_LIFE_DAYS"""
    if published_at is None:
        return 1.0
    if ref_time is None:
        ref_time = datetime.now(timezone.utc)
    if isinstance(published_at, str):
        published_at = datetime.fromisoformat(published_at)
    delta_days = (ref_time - published_at).total_seconds() / 86400
    if delta_days < 0:
        delta_days = 0
    return math.exp(-delta_days * math.log(2) / HALF_LIFE_DAYS)


def _aggregate_group(rows: list[dict], ref_time: datetime | None = None) -> dict:
    """对一组 mention 行聚合成画像字典（不含准确度，那需要行情数据）"""
    n = len(rows)
    docs = set(r["doc_id"] for r in rows)
    symbols: dict[str, int] = {}
    stances: dict[str, int] = {}
    horizons: dict[str, int] = {}
    sources: dict[int, tuple[str, int]] = {}  # source_id → (name, count)
    tw_stance = {"bullish": 0.0, "neutral": 0.0, "bearish": 0.0}
    total_tw = 0.0
    date_first = None
    date_last = None

    for r in rows:
        # symbol
        sym = r["symbol"]
        symbols[sym] = symbols.get(sym, 0) + 1
        # stance
        stance = r.get("stance", "neutral")
        stances[stance] = stances.get(stance, 0) + 1
        # horizon
        h = r.get("horizon") or "unknown"
        horizons[h] = horizons.get(h, 0) + 1
        # source
        sid = r.get("source_id")
        sname = r.get("source_name") or ""
        if sid:
            sources[sid] = (sname, sources.get(sid, (sname, 0))[1] + 1)
        # time weight
        tw = _time_weight(r.get("published_at"), ref_time)
        tw_stance[stance] = tw_stance.get(stance, 0) + tw
        total_tw += tw
        # dates
        pub = r.get("published_at")
        if pub:
            if isinstance(pub, str):
                pub = datetime.fromisoformat(pub)
            if date_first is None or pub < date_first:
                date_first = pub
            if date_last is None or pub > date_last:
                date_last = pub

    # top symbols (by count desc, top 10)
    top_syms = sorted(symbols.items(), key=lambda x: -x[1])[:10]
    top_srcs = sorted(sources.items(), key=lambda x: -x[1][1])[:10]

    # style vector (normalized time-weighted ratios)
    sv = {}
    if total_tw > 0:
        for k in ("bullish", "neutral", "bearish"):
            sv[f"{k}_ratio"] = round(tw_stance.get(k, 0) / total_tw, 4)
    sv["symbol_concentration"] = round(
        len(symbols) / max(n, 1), 4
    )  # 越低越集中
    sv["event_ratio"] = round(
        horizons.get("event", 0) / max(n, 1), 4
    )

    return {
        "total_mentions": n,
        "total_docs": len(docs),
        "unique_symbols": len(symbols),
        "date_first": date_first,
        "date_last": date_last,
        "stance_dist": stances,
        "style_vector": sv,
        "top_symbols": [
            {"symbol": s, "count": c} for s, c in top_syms
        ],
        "top_sources": [
            {"source_id": sid, "name": nm, "count": ct} for sid, (nm, ct) in top_srcs
        ],
        "horizon_dist": horizons,
        "sample_insufficient": n < SAMPLE_THRESHOLD,
    }


def _fetch_raw_bar(
    symbol: str, timestamp: datetime, engine=None,
) -> dict | None:
    """从 factor.raw_bars 取指定日期的行（用于计算后续收益）"""
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        row = c.execute(
            text("""
                SELECT timestamp, close, hfq_close, adj_factor
                FROM factor.raw_bars
                WHERE symbol = :sym AND timestamp >= CAST(:ts AS date)
                  AND freq = '1d'
                ORDER BY timestamp ASC
                LIMIT 1
            """),
            {"sym": symbol, "ts": timestamp},
        ).mappings().first()
    return dict(row) if row else None


def _fetch_benchmark_returns(
    start_date: datetime, window_days: int, engine=None,
) -> float | None:
    """取基准指数在 [start_date, start_date+window_days] 的收益率"""
    if engine is None:
        engine = get_engine()
    end_date = start_date + timedelta(days=window_days * 7 // 5)  # 粗略转交易日
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT close FROM factor.raw_bars
                WHERE symbol = :sym AND freq = '1d'
                  AND timestamp >= CAST(:start AS date) AND timestamp <= CAST(:end AS date)
                ORDER BY timestamp ASC
                LIMIT 2
            """),
            {"sym": BENCHMARK_SYMBOL, "start": start_date, "end": end_date},
        ).mappings().all()
    if len(rows) < 2:
        return None
    return (rows[-1]["close"] / rows[0]["close"] - 1) * 100


def _calc_excess_returns(
    rows: list[dict], engine=None,
) -> dict:
    """对一组 mention 计算超额收益统计

    返回：
      avg_excess_20d, avg_excess_60d, accuracy_sample_size, win_rate_20d, win_rate_60d
    """
    result = {
        "avg_excess_20d": None,
        "avg_excess_60d": None,
        "accuracy_sample_size": 0,
        "win_rate_20d": None,
        "win_rate_60d": None,
    }
    excess_20: list[float] = []
    excess_60: list[float] = []

    for r in rows:
        pub = r.get("published_at")
        if not pub:
            continue
        if isinstance(pub, str):
            pub = datetime.fromisoformat(pub)
        sym = r["symbol"]

        # 取提及日及之后的行情
        bar = _fetch_raw_bar(sym, pub, engine)
        if bar is None:
            continue
        mention_close = bar.get("hfq_close") or bar.get("close")
        if not mention_close or mention_close == 0:
            continue

        for window in EXCESS_WINDOWS:
            end_date = pub + timedelta(days=int(window * 7 / 5 * 1.5))  # 留余量
            try:
                with (engine or get_engine()).connect() as c:
                    later = c.execute(
                        text("""
                            SELECT hfq_close, close FROM factor.raw_bars
                            WHERE symbol = :sym AND freq = '1d'
                              AND timestamp > CAST(:pub AS date) AND timestamp <= CAST(:end AS date)
                            ORDER BY timestamp ASC
                            LIMIT 1
                        """),
                        {"sym": sym, "pub": pub, "end": end_date},
                    ).mappings().first()
                if later is None:
                    continue
                later_close = later.get("hfq_close") or later.get("close")
                if not later_close or later_close == 0:
                    continue
                ret = (later_close / mention_close - 1) * 100  # 百分比

                # 基准收益
                bench = _fetch_benchmark_returns(pub, window, engine)
                if bench is not None:
                    excess = ret - bench
                else:
                    excess = ret  # 无基准时用绝对收益代替

                if window == 20:
                    excess_20.append(excess)
                else:
                    excess_60.append(excess)
            except Exception:
                continue

    result["accuracy_sample_size"] = len(excess_20)  # 以 20d 窗口为准

    if excess_20:
        result["avg_excess_20d"] = round(sum(excess_20) / len(excess_20), 2)
        result["win_rate_20d"] = round(
            sum(1 for x in excess_20 if x > 0) / len(excess_20), 4
        )
    if excess_60:
        result["avg_excess_60d"] = round(sum(excess_60) / len(excess_60), 2)
        result["win_rate_60d"] = round(
            sum(1 for x in excess_60 if x > 0) / len(excess_60), 4
        )

    return result


def _detect_drift(
    rows: list[dict], agg: dict, ref_time: datetime | None = None,
) -> tuple[bool, dict | None]:
    """风格漂移检测：近 90 天 vs 全量的 stance 分布变化"""
    if ref_time is None:
        ref_time = datetime.now(timezone.utc)
    cutoff = ref_time - timedelta(days=90)

    recent = [r for r in rows if r.get("published_at")]
    if isinstance(recent[0].get("published_at"), str) if recent else False:
        recent = [
            r for r in recent
            if datetime.fromisoformat(r["published_at"]) >= cutoff
        ]
    else:
        recent = [r for r in recent if r.get("published_at") and r["published_at"] >= cutoff]

    if len(recent) < 5:  # 近 90 天样本太少，无法判断漂移
        return False, None

    recent_agg = _aggregate_group(recent, ref_time)
    full_stance = agg.get("stance_dist", {})
    recent_stance = recent_agg.get("stance_dist", {})

    # 检测：任一 stance 占比变化超过 15 个百分点即标记漂移
    drift_detail = {"full": full_stance, "recent_90d": recent_stance}
    for stance in ("bullish", "neutral", "bearish"):
        full_total = sum(full_stance.values()) or 1
        recent_total = sum(recent_stance.values()) or 1
        full_pct = full_stance.get(stance, 0) / full_total
        recent_pct = recent_stance.get(stance, 0) / recent_total
        drift_detail[f"{stance}_pct_change"] = round(
            (recent_pct - full_pct) * 100, 1
        )

    drifted = any(
        abs(v) > 15 for k, v in drift_detail.items() if k.endswith("_pct_change")
    )
    return drifted, drift_detail


def build_profiles(
    prompt_version: str = "v2",
    profile_version: str = "v1",
    engine=None,
) -> list[dict]:
    """P2 画像构建主入口

    步骤：
    1. 取全部 v2 mention + 文档元信息
    2. 按 profile_key 分组聚合
    3. 每组计算超额收益（raw_bars 只读）
    4. 检测风格漂移
    5. 写入 intel.author_profiles（版本化不覆盖）

    返回写入的 profile 列表。
    """
    if engine is None:
        engine = get_engine()

    logger.info(f"P2 build_profiles: pv={prompt_version} pver={profile_version}")

    # Step 1: fetch
    rows = _fetch_mentions_with_docs(prompt_version, engine)
    logger.info(f"Fetched {len(rows)} mentions")

    if not rows:
        logger.warning("No mentions found, nothing to profile")
        return []

    # Step 2: group by profile_key
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key, ptype = _profile_key(r)
        groups.setdefault((key, ptype), []).append(r)

    logger.info(f"Grouped into {len(groups)} profiles")

    ref_time = datetime.now(timezone.utc)
    profiles = []

    for (key, ptype), group_rows in groups.items():
        # Step 3: aggregate stats
        agg = _aggregate_group(group_rows, ref_time)

        # Step 4: excess returns
        acc = _calc_excess_returns(group_rows, engine)
        agg.update(acc)

        # Step 5: drift detection
        drifted, drift_detail = _detect_drift(group_rows, agg, ref_time)
        agg["drift_detected"] = drifted
        agg["drift_detail"] = drift_detail

        # Build DB record
        record = {
            "profile_key": key,
            "profile_type": ptype,
            "profile_version": profile_version,
            "total_mentions": agg["total_mentions"],
            "total_docs": agg["total_docs"],
            "unique_symbols": agg["unique_symbols"],
            "date_first": agg["date_first"],
            "date_last": agg["date_last"],
            "stance_dist": json.dumps(
                agg["stance_dist"], ensure_ascii=False
            ),
            "style_vector": json.dumps(
                agg["style_vector"], ensure_ascii=False
            ),
            "top_symbols": json.dumps(
                agg["top_symbols"], ensure_ascii=False
            ),
            "top_sources": json.dumps(
                agg["top_sources"], ensure_ascii=False
            ),
            "horizon_dist": json.dumps(
                agg["horizon_dist"], ensure_ascii=False
            ),
            "avg_excess_20d": agg.get("avg_excess_20d"),
            "avg_excess_60d": agg.get("avg_excess_60d"),
            "accuracy_sample_size": agg.get("accuracy_sample_size", 0),
            "win_rate_20d": agg.get("win_rate_20d"),
            "win_rate_60d": agg.get("win_rate_60d"),
            "sample_insufficient": not is_sample_sufficient(
                agg["total_mentions"], agg.get("accuracy_sample_size", 0)
            ),
            "drift_detected": agg["drift_detected"],
            "drift_detail": json.dumps(
                agg["drift_detail"], ensure_ascii=False
            ) if agg.get("drift_detail") else None,
        }

        # Step 6: write (upsert on unique key)
        with engine.begin() as c:
            c.execute(
                text("""
                    INSERT INTO intel.author_profiles
                        (profile_key, profile_type, profile_version,
                         total_mentions, total_docs, unique_symbols,
                         date_first, date_last,
                         stance_dist, style_vector, top_symbols, top_sources,
                         horizon_dist,
                         avg_excess_20d, avg_excess_60d,
                         accuracy_sample_size, win_rate_20d, win_rate_60d,
                         sample_insufficient, drift_detected, drift_detail)
                    VALUES
                        (:profile_key, :profile_type, :profile_version,
                         :total_mentions, :total_docs, :unique_symbols,
                         :date_first, :date_last,
                         :stance_dist, :style_vector, :top_symbols, :top_sources,
                         :horizon_dist,
                         :avg_excess_20d, :avg_excess_60d,
                         :accuracy_sample_size, :win_rate_20d, :win_rate_60d,
                         :sample_insufficient, :drift_detected, :drift_detail)
                    ON CONFLICT (profile_key, profile_type, profile_version)
                    DO UPDATE SET
                        total_mentions   = EXCLUDED.total_mentions,
                        total_docs       = EXCLUDED.total_docs,
                        unique_symbols   = EXCLUDED.unique_symbols,
                        date_first       = EXCLUDED.date_first,
                        date_last        = EXCLUDED.date_last,
                        stance_dist      = EXCLUDED.stance_dist,
                        style_vector     = EXCLUDED.style_vector,
                        top_symbols      = EXCLUDED.top_symbols,
                        top_sources      = EXCLUDED.top_sources,
                        horizon_dist     = EXCLUDED.horizon_dist,
                        avg_excess_20d   = EXCLUDED.avg_excess_20d,
                        avg_excess_60d   = EXCLUDED.avg_excess_60d,
                        accuracy_sample_size = EXCLUDED.accuracy_sample_size,
                        win_rate_20d     = EXCLUDED.win_rate_20d,
                        win_rate_60d     = EXCLUDED.win_rate_60d,
                        sample_insufficient = EXCLUDED.sample_insufficient,
                        drift_detected   = EXCLUDED.drift_detected,
                        drift_detail     = EXCLUDED.drift_detail,
                        computed_at      = now()
                """),
                record,
            )

        profiles.append({**record, "stance_dist": agg["stance_dist"], "style_vector": agg["style_vector"]})
        logger.info(
            f"Profile [{ptype}] {key}: {agg['total_mentions']} mentions, "
            f"{agg['unique_symbols']} symbols, "
            f"acc_sample={agg.get('accuracy_sample_size', 0)}"
        )

    logger.info(f"P2 complete: {len(profiles)} profiles written")
    return profiles
