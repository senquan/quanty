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

import bisect
import json
import math
from datetime import date, datetime, timezone, timedelta

from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.store import get_engine
from sqlalchemy import text

logger = get_logger(__name__)

# 常量
HALF_LIFE_DAYS = 180  # 风格向量时间加权半衰期
SAMPLE_THRESHOLD = 30  # 画像最低样本量（P2-5 红线：mentions 与 accuracy 样本均须 ≥ 此值）
EXCESS_WINDOWS = [20, 60]  # 超额收益窗口（交易日）
CN_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai（避免依赖系统 tz 库）


def _today_cn() -> date:
    """今天（Asia/Shanghai）—— as_of 默认值，也是「同一天重算幂等」的基准。"""
    return datetime.now(CN_TZ).date()


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


# ---------------------------------------------------------------------------
# 超额收益基础设施（D-1 于 2026-09-10 修复）
#
# ⚠️ 历史缺陷：旧实现个股端 ``ORDER BY timestamp ASC LIMIT 1``、基准端
# ``LIMIT 2`` + ``rows[-1]/rows[0]``，**EXCESS_WINDOWS 根本没参与计算** ——
# 取到的永远是「提及日之后第一个交易日」，于是
# ``avg_excess_20d ≡ avg_excess_60d ≡ 次日收益``（实测 14/14 画像两值完全相同；
# 而 600674.SH 真实 60 日 −9.14% 与次日 +1.08% **符号相反**）。
#
# 修复后的两条铁律：
#   1. 窗口长度必须由 EXCESS_WINDOWS 决定，靠**日历偏移**取得，不是 LIMIT 1；
#   2. 个股与基准共用**同一套交易日历**（基准序列同时当日历用），个股按日历
#      截止日取最后一根 bar —— 个股停牌不会把窗口拉长，两端区间严格等长。
# ---------------------------------------------------------------------------

_WINDOW_SLACK_DAYS = 20  # 无基准日历时，按自然日估算的兜底余量


def _window_slack_days(window: int) -> int:
    """window 个交易日 ≈ window*7/5 自然日，再留 1.5 倍余量 + 20 天（长假/停牌）"""
    return int(window * 7 / 5 * 1.5) + _WINDOW_SLACK_DAYS


def _align_tz(ts: datetime, ref_tz) -> datetime:
    """把 ts 的 tz 语义对齐到基准序列，避免 naive/aware 比较抛 TypeError。

    只替换 tzinfo 标签、不动时钟 —— 与旧实现 ``CAST(:ts AS date)`` 的按日期比较等价。
    """
    if ref_tz is None:
        # 没有参照物（基准序列为空）时保持原样：凭空改 tzinfo 只会制造新的比较错误
        return ts
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ref_tz)
    return ts


def _load_benchmark_series(engine=None) -> tuple[list, list[float]]:
    """一次性加载基准收盘序列（升序）—— 它同时充当**交易日历**。

    中证全指覆盖全日历（不停牌），用它定位「第 w 个交易日」比按个股 bar 数偏移可靠：
    个股停牌时按 bar 数偏移会把 20 日窗口悄悄拉成 23 日。
    """
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT timestamp, close
                FROM factor.raw_bars
                WHERE symbol = :sym AND freq = :frq
                  AND close IS NOT NULL AND close > 0
                ORDER BY timestamp ASC
            """),
            {"sym": BENCHMARK_SYMBOL, "frq": FREQ_DAILY},
        ).mappings().all()
    ts = [r["timestamp"] for r in rows]
    px = [float(r["close"]) for r in rows]
    return ts, px


def _fetch_symbol_bars(
    symbol: str, start_ts, end_ts, engine=None,
) -> list[dict]:
    """取个股在 [start_ts, end_ts] 的日线（升序），一次查询覆盖所有窗口"""
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT timestamp, close, hfq_close, adj_factor
                FROM factor.raw_bars
                WHERE symbol = :sym AND freq = :frq
                  AND timestamp >= :start AND timestamp <= :end
                ORDER BY timestamp ASC
            """),
            {
                "sym": symbol,
                "frq": FREQ_DAILY,
                "start": start_ts,
                "end": end_ts,
            },
        ).mappings().all()
    return [dict(r) for r in rows]


def _bar_price(bar: dict) -> tuple[float | None, bool]:
    """取收盘价，复权优先。返回 ``(价格, 是否用了 hfq)``。

    ⚠️ hfq_close 缺失时降级到未复权 close，会在除权日产生**假跳空**（D-2）。
    回填完成后这条分支不应再被走到；一旦走到会打 warning 并计入返回值。
    """
    px = bar.get("hfq_close")
    if px:
        return float(px), True
    px = bar.get("close")
    return (float(px), False) if px else (None, False)


def _fetch_raw_bar(
    symbol: str, timestamp: datetime, engine=None,
) -> dict | None:
    """取个股在 timestamp（含）之后的第一根日线 —— 即「提及日建仓价」那根 bar。

    保留原签名供外部/旧调用方使用；新的窗口计算走 ``_fetch_symbol_bars``。
    """
    end = timestamp + timedelta(days=_WINDOW_SLACK_DAYS)
    bars = _fetch_symbol_bars(symbol, timestamp, end, engine)
    return bars[0] if bars else None


def _calc_excess_returns(
    rows: list[dict], engine=None,
) -> dict:
    """对一组 mention 计算超额收益统计

    返回：
      avg_excess_20d, avg_excess_60d, accuracy_sample_size, win_rate_20d, win_rate_60d
    """
    result = {f"avg_excess_{w}d": None for w in EXCESS_WINDOWS}
    result.update({f"win_rate_{w}d": None for w in EXCESS_WINDOWS})
    result["accuracy_sample_size"] = 0

    if not rows:
        return result

    bench_ts, bench_px = _load_benchmark_series(engine)
    if not bench_ts:
        logger.warning("基准序列为空，超额收益退化为绝对收益")
    max_w = max(EXCESS_WINDOWS)
    excess: dict[int, list[float]] = {w: [] for w in EXCESS_WINDOWS}
    fallback_no_hfq = 0  # 用了未复权 close 兜底的 mention 数（D-2 应为 0）

    ref_tz = bench_ts[0].tzinfo if bench_ts else None

    for r in rows:
        pub = r.get("published_at")
        if not pub:
            continue
        if isinstance(pub, str):
            pub = datetime.fromisoformat(pub)
        sym = r["symbol"]

        # 1) 在交易日历上定位提及日：第一个 >= pub 的交易日
        pub_cmp = _align_tz(pub, ref_tz)
        if bench_ts:
            idx = bisect.bisect_left(bench_ts, pub_cmp)
            if idx >= len(bench_ts):
                continue  # 提及日晚于全部行情 → 无样本
            start_ts = bench_ts[idx]
            last_idx = min(idx + max_w, len(bench_ts) - 1)
            end_ts = bench_ts[last_idx]
        else:
            # 无基准：退化为按自然日估算（仍按窗口取第 w 根，不再退回次日）
            start_ts = pub_cmp
            end_ts = pub_cmp + timedelta(days=_window_slack_days(max_w))

        # 2) 个股在区间内的全部日线（一次查询覆盖所有窗口）
        bars = _fetch_symbol_bars(sym, start_ts, end_ts, engine)
        if not bars:
            continue
        base_px, used_hfq = _bar_price(bars[0])
        if not base_px:
            continue
        if not used_hfq:
            fallback_no_hfq += 1

        bar_ts = [b["timestamp"] for b in bars]

        # 3) 逐窗口：个股与基准都取 [idx, idx+window] 这一段，严格等长
        for w in EXCESS_WINDOWS:
            ti = idx + w if bench_ts else w
            if bench_ts and ti >= len(bench_ts):
                continue  # 行情不足 w 个交易日 → 该 mention 不计入该窗口
            cut = bench_ts[ti] if bench_ts else None
            if cut is not None:
                j = bisect.bisect_right(bar_ts, cut) - 1
            else:
                j = min(w, len(bars) - 1)
            if j <= 0:
                continue  # 区间内个股无进展（停牌/退市）
            end_px, _ = _bar_price(bars[j])
            if not end_px:
                continue

            ret = (end_px / base_px - 1) * 100  # 个股窗口收益（%）
            if bench_ts:
                bench_ret = (bench_px[ti] / bench_px[idx] - 1) * 100
            else:
                bench_ret = 0.0
            excess[w].append(ret - bench_ret)

    result["accuracy_sample_size"] = len(excess[EXCESS_WINDOWS[0]])

    for w in EXCESS_WINDOWS:
        vals = excess[w]
        if not vals:
            continue
        result[f"avg_excess_{w}d"] = round(sum(vals) / len(vals), 2)
        result[f"win_rate_{w}d"] = round(
            sum(1 for x in vals if x > 0) / len(vals), 4
        )

    if fallback_no_hfq:
        logger.warning(
            f"[D-2] {fallback_no_hfq} 条 mention 的建仓价降级用了未复权 close "
            f"（hfq_close 缺失）→ 除权日会产生假跳空，请回填 hfq_close 后重算"
        )
    logger.info(
        "窗口样本量："
        + ", ".join(f"{w}d={len(excess[w])}" for w in EXCESS_WINDOWS)
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
    as_of: date | None = None,
    engine=None,
) -> list[dict]:
    """P2 画像构建主入口

    步骤：
    1. 取全部 v2 mention + 文档元信息
    2. 按 profile_key 分组聚合
    3. 每组计算超额收益（raw_bars 只读）
    4. 检测风格漂移
    5. 写入 intel.author_profiles（按 as_of 版本化，**不同 as_of 不覆盖**）

    Args:
        as_of: 画像的知识截止日。默认取「今天（Asia/Shanghai）」。
            唯一键含 as_of ⇒ 同一天重跑 = 幂等覆盖，跨天重跑 = 新增历史行。
            （D-9：此前唯一键不含日期，每次重算都原地覆盖，历史截面不可复现。）

    返回写入的 profile 列表。
    """
    if engine is None:
        engine = get_engine()

    if as_of is None:
        as_of = _today_cn()

    logger.info(
        f"P2 build_profiles: pv={prompt_version} pver={profile_version} as_of={as_of}"
    )

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
            "as_of": as_of,
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
        # ⚠️ 唯一键含 as_of ⇒ 同一天重跑幂等覆盖，跨天重跑新增历史行（D-9 版本化）。
        #    绝不要把 as_of 从 ON CONFLICT / DO UPDATE 里去掉，否则又回到"原地覆盖"。
        with engine.begin() as c:
            c.execute(
                text("""
                    INSERT INTO intel.author_profiles
                        (profile_key, profile_type, profile_version, as_of,
                         total_mentions, total_docs, unique_symbols,
                         date_first, date_last,
                         stance_dist, style_vector, top_symbols, top_sources,
                         horizon_dist,
                         avg_excess_20d, avg_excess_60d,
                         accuracy_sample_size, win_rate_20d, win_rate_60d,
                         sample_insufficient, drift_detected, drift_detail)
                    VALUES
                        (:profile_key, :profile_type, :profile_version, :as_of,
                         :total_mentions, :total_docs, :unique_symbols,
                         :date_first, :date_last,
                         :stance_dist, :style_vector, :top_symbols, :top_sources,
                         :horizon_dist,
                         :avg_excess_20d, :avg_excess_60d,
                         :accuracy_sample_size, :win_rate_20d, :win_rate_60d,
                         :sample_insufficient, :drift_detected, :drift_detail)
                    ON CONFLICT (profile_key, profile_type, profile_version, as_of)
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
