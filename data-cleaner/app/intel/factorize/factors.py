"""P3-3：INTL_* 因子实现（从简到繁，plan §5 顺序）

六个因子：
    1. INTL_MENTION_HEAT_5      5 交易日提及热度（去重文档数）
    2. INTL_SENTIMENT_10       10 交易日净情绪 (bull - bear) / total ∈ [-1, 1]
    3. INTL_RESONANCE_5         5 交易日跨源共振（不同来源数）
    4. INTL_FIRST_MENTION      60 交易日内首次提及 → 1，否则 0
    5. INTL_AUTHOR_CONVICTION   作者信念度（作者历史准确度；样本不足时降级为 LLM 置信度）
    6. INTL_STYLE_MATCH         作者/来源 top_symbols 命中该 symbol → 1，否则 0

防前视（P3-1，不变式）：
    - 每行 trade_date = to_trade_date(available_at)：A 股 15:00 收盘，
      收盘后（含非交易日）入库的提及推到下一交易日，回测当日不可见。
    - 滚动窗口只含 <= trade_date 的交易日，绝不含未来信息。

数据现实：LLM 抽取里 neutral 占绝大多数、author 大量为 null、作者 accuracy
样本几乎为 0 —— 因子会呈现稀疏/偏中性，属预期，IC 报告如实记录。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

from sqlalchemy import text

from app.intel.core.logging import get_logger
from app.intel.factorize.availability import build_factor_rows, compute_available_at
from app.intel.store import get_engine

logger = get_logger(__name__)

# 与 app/backtest/data.py、app/storage/raw_store.py 一致 —— raw_bars 的 freq 取值
FREQ_DAILY = "1d"
MARKET_CLOSE_HOUR = 15  # A 股 15:00 收盘，此后入库的提及当日不可见

FACTOR_VERSION = "v1"
PROMPT_VERSION = "v2"

HEAT_WINDOW = 5
SENTIMENT_WINDOW = 10
RESONANCE_WINDOW = 5
FIRST_MENTION_LOOKBACK = 60

# 作者准确度可用所需的最小样本（低于此值降级为 LLM 置信度）
AUTHOR_ACCURACY_MIN_SAMPLE = 10

STANCE_SCORE = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


# ---------------------------------------------------------------- 交易日历


def load_trade_calendar(engine=None) -> list[date]:
    """从 factor.raw_bars 取真实交易日历（升序）。dc 不自己猜节假日。"""
    if engine is None:
        engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT DISTINCT timestamp FROM factor.raw_bars
                WHERE freq = :fq ORDER BY timestamp ASC
                """
            ),
            {"fq": FREQ_DAILY},
        ).scalars().all()
    out: list[date] = []
    for r in rows:
        d = r.date() if isinstance(r, datetime) else r
        if not out or d != out[-1]:
            out.append(d)
    return out


def extend_calendar(
    calendar: Sequence[date], until: date, max_extra: int = 10
) -> list[date]:
    """把日历外推到 until（跳过周末），覆盖 raw_bars 的行情滞后。

    背景：raw_bars 最后一个交易日通常滞后于"今天"（当日 bar 未入库）。若不外推，
    近期 mention 会因找不到交易日被**全部丢弃**，因子表空转。

    外推部分是近似日历（忽略法定节假日），仅用于给近期 mention 定位 trade_date；
    这些日子的行情尚未入库，回测自然因缺收益数据而跳过，**不构成前视**。
    """
    if not calendar:
        return list(calendar)
    out = list(calendar)
    d = out[-1]
    added = 0
    while d < until and added < max_extra:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # 周一~周五
            out.append(d)
            added += 1
    return out


def next_trade_date(calendar: Sequence[date], d: date) -> date | None:
    """严格晚于 d 的第一个交易日；日历末尾之后返回 None。"""
    for t in calendar:
        if t > d:
            return t
    return None


def to_trade_date(available_at, calendar: Sequence[date]) -> date | None:
    """把 available_at 映射为回测可用的交易日（防前视关键一步）。

    - 交易日且 15:00 前 → 当日
    - 交易日但 15:00 后 → 下一交易日（收盘后才得到的信息，当日不可见）
    - 非交易日 → 下一交易日
    - 超出日历范围 → None（该行不可用于回测，宁缺勿前视）
    """
    if available_at is None or not calendar:
        return None
    if isinstance(available_at, datetime):
        d = available_at.date()
        hour = available_at.hour
    else:
        d = available_at
        hour = 0

    if d in calendar:
        if hour < MARKET_CLOSE_HOUR:
            return d
        return next_trade_date(calendar, d)
    return next_trade_date(calendar, d)


def rolling_dates(
    calendar: Sequence[date], trade_date: date, window: int
) -> list[date]:
    """含 trade_date 在内、往前 window 个交易日的日期列表（升序）。"""
    if trade_date not in calendar:
        return []
    i = calendar.index(trade_date)
    return list(calendar[max(0, i - window + 1) : i + 1])


# ---------------------------------------------------------------- 输入装载


def load_mentions(prompt_version: str = PROMPT_VERSION, engine=None) -> list[dict]:
    """装载 v2 mention + 文档元信息（含 available_at 所需的两个时间）。"""
    if engine is None:
        engine = get_engine()
    sql = text(
        """
        SELECT m.symbol,
               d.published_at,
               d.ingested_at,
               m.stance,
               m.confidence,
               m.doc_id,
               COALESCE(s.name, 'unknown') AS source_name,
               d.author
        FROM intel.doc_mentions m
        JOIN intel.documents d ON d.id = m.doc_id
        LEFT JOIN intel.sources s ON s.id = d.source_id
        WHERE m.prompt_version = :pv
        """
    )
    with engine.connect() as c:
        rows = c.execute(sql, {"pv": prompt_version}).mappings().all()
    return [dict(r) for r in rows]


def load_profile_index(engine=None) -> dict[str, dict]:
    """profile_key -> {top_symbols: set[str], accuracy: float|None, sample: int}

    按 computed_at 取每个 profile_key 的最新版本（与后端 intel 路由口径一致）。
    """
    if engine is None:
        engine = get_engine()
    sql = text(
        """
        SELECT profile_key, top_symbols, accuracy_sample_size, win_rate_20d,
               avg_excess_20d
        FROM intel.author_profiles
        WHERE (profile_key, computed_at) IN (
            SELECT profile_key, max(computed_at) FROM intel.author_profiles
            GROUP BY profile_key
        )
        """
    )
    out: dict[str, dict] = {}
    with engine.connect() as c:
        for r in c.execute(sql).mappings().all():
            tops = r["top_symbols"] or []
            syms = set()
            for t in tops:
                if isinstance(t, dict):
                    s = t.get("symbol")
                else:
                    s = t
                if s:
                    syms.add(s)
            acc = r["win_rate_20d"]
            if acc is None:
                acc = r["avg_excess_20d"]
            out[r["profile_key"]] = {
                "top_symbols": syms,
                "accuracy": acc,
                "sample": r["accuracy_sample_size"] or 0,
            }
    return out


def build_mention_index(
    mentions: Iterable[dict], calendar: Sequence[date]
) -> list[dict]:
    """给每条 mention 补 available_at 与 trade_date，并丢掉无法定位交易日的行。

    纯函数（除日志），便于单测。
    """
    out: list[dict] = []
    dropped = 0
    for m in mentions:
        av = compute_available_at(m.get("published_at"), m.get("ingested_at"))
        td = to_trade_date(av, calendar)
        if td is None or av is None:
            dropped += 1
            continue
        row = dict(m)
        row["available_at"] = av
        row["trade_date"] = td
        out.append(row)
    if dropped:
        logger.warning(f"drop {dropped} mentions: cannot resolve trade_date")
    return out


# ---------------------------------------------------------------- 因子计算


def _index_by_symbol_date(rows: Sequence[dict]) -> dict[tuple[str, date], list[dict]]:
    idx: dict[tuple[str, date], list[dict]] = {}
    for r in rows:
        idx.setdefault((r["symbol"], r["trade_date"]), []).append(r)
    return idx


def compute_mention_heat(
    idx: dict[tuple[str, date], list[dict]], symbol: str, dates: Sequence[date]
) -> float:
    """窗口内去重文档数（同一篇文档提同一标的多次只算一次）。"""
    docs: set = set()
    for d in dates:
        for r in idx.get((symbol, d), []):
            docs.add(r.get("doc_id"))
    return float(len(docs))


def compute_sentiment(
    idx: dict[tuple[str, date], list[dict]], symbol: str, dates: Sequence[date]
) -> float | None:
    """窗口内净情绪 (bull - bear) / total ∈ [-1, 1]；窗口无提及返回 None（不写行）。"""
    total = 0
    score = 0.0
    for d in dates:
        for r in idx.get((symbol, d), []):
            total += 1
            score += STANCE_SCORE.get((r.get("stance") or "").lower(), 0.0)
    if total == 0:
        return None
    return score / total


def compute_resonance(
    idx: dict[tuple[str, date], list[dict]], symbol: str, dates: Sequence[date]
) -> float:
    """窗口内提及该标的的不同来源数（跨源共振）。"""
    srcs: set = set()
    for d in dates:
        for r in idx.get((symbol, d), []):
            srcs.add(r.get("source_name") or "unknown")
    return float(len(srcs))


def compute_first_mention(
    idx: dict[tuple[str, date], list[dict]],
    calendar: Sequence[date],
    symbol: str,
    trade_date: date,
    lookback: int = FIRST_MENTION_LOOKBACK,
) -> float:
    """回看窗口内该 symbol 的最早提及日是否就是 trade_date → 1.0 / 0.0。"""
    window = rolling_dates(calendar, trade_date, lookback)
    if not window:
        return 0.0
    first = None
    for d in window:
        if (symbol, d) in idx:
            first = d
            break
    return 1.0 if first == trade_date else 0.0


def compute_author_conviction(
    rows: Sequence[dict], profiles: dict[str, dict]
) -> float:
    """作者信念度：作者历史准确度；样本不足（<10）时降级为 LLM 自评置信度。

    降级是诚实的数据现实处理，不是伪装成有准确度。
    """
    if not rows:
        return 0.0
    vals: list[float] = []
    for r in rows:
        key = r.get("author") or r.get("source_name")
        p = profiles.get(key) if key else None
        if (
            p
            and p.get("accuracy") is not None
            and (p.get("sample") or 0) >= AUTHOR_ACCURACY_MIN_SAMPLE
        ):
            vals.append(float(p["accuracy"]))
        else:
            conf = r.get("confidence")
            vals.append(float(conf) if conf is not None else 0.0)
    return sum(vals) / len(vals)


def compute_style_match(rows: Sequence[dict], profiles: dict[str, dict]) -> float:
    """风格匹配：提及者（作者优先，回退来源）的 top_symbols 命中该标的 → 1。"""
    if not rows:
        return 0.0
    hits = 0
    for r in rows:
        key = r.get("author") or r.get("source_name")
        p = profiles.get(key) if key else None
        if p and r["symbol"] in p.get("top_symbols", set()):
            hits += 1
    return hits / len(rows)


def build_all_factor_rows(
    mentions: Sequence[dict],
    calendar: Sequence[date],
    profiles: dict[str, dict] | None = None,
    factor_version: str = FACTOR_VERSION,
    prompt_version: str = PROMPT_VERSION,
) -> list[dict]:
    """把 mention 列表展开为六因子的全部因子行（含 available_at，防前视）。

    只为每个 (symbol, trade_date) 实际有提及的组合生成行；回测侧缺失即视为
    "无提及"（heat/resonance=0、sentiment=中性），不伪造零值行。
    """
    if profiles is None:
        profiles = {}
    rows = build_mention_index(mentions, calendar)
    idx = _index_by_symbol_date(rows)

    out: list[dict] = []
    for (symbol, td), group in idx.items():
        # 该组合的可见时刻：组内最大 available_at（全部可见后才算该日完整值）
        av = max(g["available_at"] for g in group)
        pub = max((g.get("published_at") for g in group), default=None)
        ing = max((g.get("ingested_at") for g in group), default=None)

        heat_dates = rolling_dates(calendar, td, HEAT_WINDOW)
        sent_dates = rolling_dates(calendar, td, SENTIMENT_WINDOW)
        reso_dates = rolling_dates(calendar, td, RESONANCE_WINDOW)

        heat = compute_mention_heat(idx, symbol, heat_dates)
        sent = compute_sentiment(idx, symbol, sent_dates)
        reso = compute_resonance(idx, symbol, reso_dates)
        first = compute_first_mention(idx, calendar, symbol, td)
        conv = compute_author_conviction(group, profiles)
        style = compute_style_match(group, profiles)

        specs = [
            ("INTL_MENTION_HEAT_5", heat),
            ("INTL_RESONANCE_5", reso),
            ("INTL_FIRST_MENTION", first),
            ("INTL_AUTHOR_CONVICTION", conv),
            ("INTL_STYLE_MATCH", style),
        ]
        if sent is not None:
            specs.append(("INTL_SENTIMENT_10", sent))

        for code, val in specs:
            row = build_factor_rows(
                symbol=symbol,
                trade_date=td,
                factor_code=code,
                value=float(val),
                published_at=pub,
                ingested_at=ing,
                factor_version=factor_version,
                prompt_version=prompt_version,
            )
            if row:
                out.append(row)
    return out
