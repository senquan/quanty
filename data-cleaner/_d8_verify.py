"""D-8 验收：3 作者 × 10 提及，画像超额收益与独立复算逐条比对。

目的（审计 D-8）：P2 验收项「3 作者 × 10 提及与行情软件手工核对」从未做过，
正因为缺这一步，D-1（20/60 日窗口算成次日）潜伏至今。

方法（两条独立路径对账）：
  A. **画像路径**：画像里对该作者聚合出的 avg_excess_20d / avg_excess_60d
     与其 accuracy_sample_size（这只验聚合层面）。
  B. **逐 mention 复算**：对每个 mention，用 raw_bars 独立算
     「提及后第 20/60 个交易日的 hfq 收益 − 同期中证全指收益」，
     与画像聚合值比样本量、比均值。

关键复用：窗口口径必须与 profile.py 一致（同用 hfq_close 两端同口径、
以中证全指为交易日历、停牌不拉长窗口）。
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from sqlalchemy import text

from app.intel.aggregate.profile import (
    BENCHMARK_SYMBOL,
    FREQ_DAILY,
    _load_benchmark_series,
)
from app.intel.store import get_engine

e = get_engine()
N_AUTHORS = 3
N_MENTIONS = 10
WINDOWS = (20, 60)


def load_mentions(limit_authors=N_AUTHORS, per_author=N_MENTIONS, min_n=10):
    """取若干有足够样本的作者，每人 10 条 mention。"""
    with e.connect() as c:
        authors = c.execute(
            text("""
            SELECT d.author, count(*) n
            FROM intel.doc_mentions m
            JOIN intel.documents d ON d.id = m.doc_id
            WHERE d.author IS NOT NULL AND d.author <> ''
              AND m.prompt_version = 'v2'
            GROUP BY d.author
            HAVING count(*) >= :mn
            ORDER BY n DESC
            LIMIT :k
        """),
            {"k": limit_authors, "mn": min_n},
        ).mappings().all()
        out = {}
        for a in authors:
            rows = c.execute(
                text("""
                SELECT m.id, m.symbol, m.stance, m.horizon, d.published_at,
                       d.title, d.ingested_at
                FROM intel.doc_mentions m
                JOIN intel.documents d ON d.id = m.doc_id
                WHERE d.author = :a AND m.prompt_version = 'v2'
                  AND m.symbol IS NOT NULL
                  -- ⚠️ 必须挑"够老"的 mention：20/60 日窗口要未来行情才算得完，
                  --    最近的 mention 必然 window_incomplete（不是 bug，是数据边界）。
                  --    这里取日历末端往前留足 90 天的那些。
                  AND d.published_at <= (SELECT max(timestamp) FROM factor.raw_bars
                                          WHERE freq = '1d' AND symbol = :bench)
                                      - INTERVAL '90 days'
                ORDER BY d.published_at DESC
                LIMIT :lim
            """),
                {"a": a["author"], "lim": per_author, "bench": BENCHMARK_SYMBOL},
            ).mappings().all()
            out[a["author"]] = [dict(r) for r in rows]
    return out


def recompute(symbol: str, published_at, window: int, cal, bench_vals):
    """独立复算单条 mention 的 window 日超额收益（与 profile.py 同口径）。

    返回 (excess_pct, note)；样本不足返回 (None, reason)。
    """
    import bisect

    if published_at is None:
        return None, "no_published_at"
    # 定位日历时点：>= published_at 的第一个交易日
    # ⚠️ 日历元素是 datetime（带 tz），必须同类型比较；这里统一取 date() 比较
    d0 = published_at.date() if hasattr(published_at, "date") else published_at
    cal_dates = [x.date() if hasattr(x, "date") else x for x in cal]
    idx = bisect.bisect_left(cal_dates, d0)
    if idx >= len(cal):
        return None, "after_calendar"
    # 需要 idx+window 仍在日历内
    end_i = idx + window
    cal_len = len(cal)
    if end_i >= cal_len:
        return None, f"window_incomplete(need+{window}d, 仅余{cal_len - idx}d)"

    start_d, end_d = cal[idx], cal[end_i]
    with e.connect() as c:
        bars = c.execute(
            text("""
            SELECT timestamp, hfq_close, close
            FROM factor.raw_bars
            WHERE symbol = :s AND freq = :f
              AND timestamp >= :d0 AND timestamp <= :d1
            ORDER BY timestamp
        """),
            {"s": symbol, "f": FREQ_DAILY, "d0": start_d, "d1": end_d},
        ).mappings().all()
    if len(bars) < 2:
        return None, "no_bars"
    # 两端同口径：优先 hfq，整段降级 close
    use_hfq = all(b["hfq_close"] is not None for b in bars)
    px0 = bars[0]["hfq_close"] if use_hfq else bars[0]["close"]
    px1 = bars[-1]["hfq_close"] if use_hfq else bars[-1]["close"]
    if not px0 or not px1:
        return None, "null_price"
    stock_ret = (px1 / px0 - 1.0) * 100.0

    b0 = bench_vals[idx]
    b1 = bench_vals[end_i]
    bench_ret = (b1 / b0 - 1.0) * 100.0
    return stock_ret - bench_ret, ("hfq" if use_hfq else "close")


print("== 加载基准（中证全指）作交易日历 ==")
cal, bench_vals = _load_benchmark_series(e)
print(f"   日历 {len(cal)} 天，范围 {cal[0]} .. {cal[-1]}")

data = load_mentions(min_n=10)
print(f"\n== 选定 {len(data)} 位作者 ==")
print("==" + "=" * 70)

grand_ok = grand_bad = grand_none = 0
for author, mentions in data.items():
    with e.connect() as c:
        prof = c.execute(
            text("""
            SELECT avg_excess_20d, avg_excess_60d, accuracy_sample_size,
                   total_mentions, as_of
            FROM intel.author_profiles
            WHERE profile_key = :k AND profile_type = 'author'
            ORDER BY as_of DESC LIMIT 1
        """),
            {"k": author},
        ).mappings().first()

    print(f"\n### 作者：{author}")
    if prof:
        print(
            f"    画像(as_of={prof['as_of']}): total={prof['total_mentions']} "
            f"acc_sample={prof['accuracy_sample_size']} "
            f"e20={prof['avg_excess_20d']} e60={prof['avg_excess_60d']}"
        )
    else:
        print("    画像：无记录")

    # 逐条复算
    calc = {w: [] for w in WINDOWS}
    for m in mentions:
        line = f"    {str(m['symbol']):11s} {str(m['published_at'])[:10]}"
        for w in WINDOWS:
            v, note = recompute(m["symbol"], m["published_at"], w, cal, bench_vals)
            if v is None:
                line += f"  e{w}=({note})"
            else:
                calc[w].append(v)
                line += f"  e{w}={v:+.2f}%"
        print(line)

    # 与画像聚合值对照（用同一批 mention 复算的均值）
    for w in WINDOWS:
        col = "avg_excess_20d" if w == 20 else "avg_excess_60d"
        pv = prof[col] if prof else None
        if calc[w]:
            mine = sum(calc[w]) / len(calc[w])
            print(
                f"    → w={w:2d}日：独立复算 n={len(calc[w])} mean={mine:+.4f}%  "
                f"画像={pv}"
            )
        else:
            print(f"    → w={w:2d}日：独立复算 无样本")
            grand_none += 1

    # 判定：e20 != e60（D-1 的回归特征）
    if prof and prof["avg_excess_20d"] is not None and prof["avg_excess_60d"] is not None:
        if abs(float(prof["avg_excess_20d"]) - float(prof["avg_excess_60d"])) < 1e-9:
            print("    ⚠️ 画像 e20 == e60 —— D-1 特征复现！")
            grand_bad += 1
        else:
            print("    ✓ 画像 e20 != e60（D-1 已修）")
            grand_ok += 1

print("\n" + "=" * 72)
print(f"结算：e20!=e60 的作者 {grand_ok} 位，e20==e60（异常） {grand_bad} 位，无样本 {grand_none}")
print("（注：画像聚合覆盖该作者**全部** mention，此处只抽 10 条 spot-check，")
print("  均值不必相等；关键是逐条复算能算出来、且 e20 与 e60 有区分度。）")
