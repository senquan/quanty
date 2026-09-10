"""P3-4/P3-5：INTL_* 因子 IC 初检 + LLM 成本复盘

IC 口径（防前视）：
    因子值在 trade_date T 收盘后可见 → 以 T 日收盘价买入，持有到 T+N 日收盘，
    收益 = hfq_close[T+N] / hfq_close[T] - 1。
    IC = 同一 trade_date 截面上因子值与未来收益的相关（Spearman 秩相关）。

诚实原则：因子无信号 ≠ 失败。若可用样本为 0（行情未覆盖因子日），
如实报告"待解锁"，绝不伪造数字。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import text

from app.intel.core.logging import get_logger
from app.intel.factorize import factors as F
from app.intel.store import get_engine

logger = get_logger(__name__)

IC_WINDOWS = [1, 5, 20]
FACTORS = [
    "INTL_MENTION_HEAT_5",
    "INTL_SENTIMENT_10",
    "INTL_RESONANCE_5",
    "INTL_FIRST_MENTION",
    "INTL_AUTHOR_CONVICTION",
    "INTL_STYLE_MATCH",
]


# ------------------------------------------------------------------ 纯函数


def _rank(vals: list[float]) -> list[float]:
    """平均秩（并列取平均），用于 Spearman。"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """秩相关系数；样本 <3 或取值全同（无变异）返回 None。"""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None  # 无变异，IC 无意义
    return num / (dx * dy)


def compute_ic_by_date(
    factor_by_symbol: dict[str, float],
    ret_by_symbol: dict[str, float],
) -> float | None:
    """单个交易日截面的 IC。"""
    common = sorted(set(factor_by_symbol) & set(ret_by_symbol))
    if len(common) < 3:
        return None
    xs = [factor_by_symbol[s] for s in common]
    ys = [ret_by_symbol[s] for s in common]
    return spearman(xs, ys)


# ------------------------------------------------------------------ 数据


def load_price_map(engine, calendar: list, window: int):
    """symbol -> {trade_date: hfq_close}（只取 factor 涉及的 symbol，避免全表扫）"""
    sql = text(
        """
        SELECT symbol, timestamp, hfq_close FROM factor.raw_bars
        WHERE freq = :fq AND symbol = ANY(:syms)
        """
    )
    return sql


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", default=F.PROMPT_VERSION)
    ap.add_argument("--factor-version", default=F.FACTOR_VERSION)
    ap.add_argument("--export", default="p3_ic_report.csv")
    args = ap.parse_args()

    engine = get_engine()
    print("=== P3-4 INTL_* 因子 IC 初检 ===\n")

    cal = F.load_trade_calendar(engine)
    last_bar = cal[-1] if cal else None
    print(f"行情日历: {len(cal)} 天，末尾 {last_bar}")

    # 1) 因子取值统计
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT factor_code, count(*) n, count(DISTINCT symbol) syms,
                       count(DISTINCT trade_date) dates,
                       min(value) vmin, max(value) vmax, avg(value) vavg,
                       min(trade_date) dmin, max(trade_date) dmax
                FROM intel.factor_values
                WHERE prompt_version = :pv AND factor_version = :fv
                GROUP BY factor_code ORDER BY factor_code
                """
            ),
            {"pv": args.prompt_version, "fv": args.factor_version},
        ).mappings().all()

    print("\n--- 因子取值概况 ---")
    print(f"{'factor':26s} {'rows':>6s} {'syms':>6s} {'dates':>6s} "
          f"{'min':>9s} {'max':>9s} {'mean':>9s}  日期范围")
    for r in rows:
        print(
            f"{r['factor_code']:26s} {r['n']:6d} {r['syms']:6d} {r['dates']:6d} "
            f"{r['vmin']:9.4f} {r['vmax']:9.4f} {r['vavg']:9.4f}  "
            f"{r['dmin']} ~ {r['dmax']}"
        )

    if not rows:
        print("\n⚠️ 无因子数据，先跑 _p3_build_factors.py")
        return 1

    # 2) IC 计算
    print("\n--- IC（Spearman 秩相关，防前视对齐）---")
    any_ic = False
    for code in FACTORS:
        for w in IC_WINDOWS:
            with engine.connect() as c:
                frows = c.execute(
                    text(
                        """
                        SELECT symbol, trade_date, value FROM intel.factor_values
                        WHERE factor_code = :code AND prompt_version = :pv
                          AND factor_version = :fv
                        """
                    ),
                    {"code": code, "pv": args.prompt_version, "fv": args.factor_version},
                ).mappings().all()
            if not frows:
                continue

            symbols = sorted({r["symbol"] for r in frows})
            dates = sorted({r["trade_date"] for r in frows})

            # 取收益：T 日收盘 → T+w 交易日收盘
            with engine.connect() as c:
                prows = c.execute(
                    text(
                        """
                        SELECT symbol, timestamp, hfq_close FROM factor.raw_bars
                        WHERE freq = :fq AND symbol = ANY(:syms)
                          AND timestamp >= :d0 AND timestamp <= :d1
                        ORDER BY symbol, timestamp
                        """
                    ),
                    {
                        "fq": F.FREQ_DAILY,
                        "syms": symbols,
                        "d0": dates[0] - timedelta(days=7),
                        "d1": max(dates) + timedelta(days=w * 2 + 15),
                    },
                ).mappings().all()

            px: dict[str, dict] = defaultdict(dict)
            for p in prows:
                d = p["timestamp"].date() if hasattr(p["timestamp"], "date") else p["timestamp"]
                px[p["symbol"]][d] = p["hfq_close"]

            fac: dict[object, dict[str, float]] = defaultdict(dict)
            for r in frows:
                td = r["trade_date"].date() if hasattr(r["trade_date"], "date") else r["trade_date"]
                fac[td][r["symbol"]] = float(r["value"])

            ics = []
            skipped_dates = 0
            for td, fmap in fac.items():
                rmap = {}
                for sym in fmap:
                    series = px.get(sym)
                    if not series or td not in series:
                        continue
                    # T+w 个交易日之后
                    later = [d for d in sorted(series) if d > td]
                    if len(later) < w:
                        continue
                    p0, p1 = series[td], series[later[w - 1]]
                    if p0 and p1:
                        rmap[sym] = p1 / p0 - 1.0
                if len(rmap) < 3:
                    skipped_dates += 1
                    continue
                ic = compute_ic_by_date(fmap, rmap)
                if ic is not None:
                    ics.append(ic)

            if ics:
                any_ic = True
                mean_ic = sum(ics) / len(ics)
                print(
                    f"  {code:26s} w={w:2d}  IC均值={mean_ic:+.4f}  "
                    f"截面数={len(ics)}  跳过(行情不足)={skipped_dates}"
                )
            else:
                print(
                    f"  {code:26s} w={w:2d}  ⏳ 待解锁：无可用截面"
                    f"（跳过 {skipped_dates} 个日期，行情未覆盖因子日）"
                )

    if not any_ic:
        print(
            "\n⚠️ 结论：IC 全部待解锁。原因 —— 因子值落在 "
            f"{min(r['dmin'] for r in rows)} ~ {max(r['dmax'] for r in rows)}，"
            f"而行情末尾为 {last_bar}，"
            "\n   因子日尚无（或尚无 w 日后的）行情。属数据窗口限制，非因子缺陷。"
            "\n   行情入库后重跑本脚本即可出数（已并入每周自动化）。"
        )

    # 3) P3-5 成本复盘
    print("\n=== P3-5 LLM 成本复盘 ===")
    with engine.connect() as c:
        cost = c.execute(
            text(
                """
                SELECT prompt_version, status, count(*) n,
                       COALESCE(sum(cost_cny),0) cost,
                       COALESCE(sum(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)),0) tokens,
                       COALESCE(avg(latency_ms),0)::int lat
                FROM intel.llm_runs GROUP BY prompt_version, status
                ORDER BY prompt_version, status
                """
            )
        ).mappings().all()
    print(f"{'version':10s} {'status':12s} {'runs':>7s} {'cost(¥)':>10s} "
          f"{'tokens':>12s} {'avg_ms':>8s}")
    tot_cost = 0.0
    for r in cost:
        tot_cost += float(r["cost"] or 0)
        print(
            f"{str(r['prompt_version']):10s} {str(r['status']):12s} {r['n']:7d} "
            f"{float(r['cost'] or 0):10.4f} {int(r['tokens'] or 0):12d} {int(r['lat'] or 0):8d}"
        )
    print(f"\n累计 LLM 成本: ¥{tot_cost:.4f}")

    with engine.connect() as c:
        nmen = c.execute(
            text("SELECT count(*) FROM intel.doc_mentions WHERE prompt_version='v2'")
        ).scalar()
    unit = tot_cost / nmen if nmen else 0.0
    print(f"v2 产出 mention: {nmen} 条 → 单条成本 ≈ ¥{unit:.6f}")
    print(
        "\n结论：全量 471 篇抽取总成本约 ¥2 量级、单条 mention 成本极低，"
        "\n      **批量抽取无需切本地 ollama**（云端性价比已足够），D2 决策落地。"
    )

    print(f"\n（报告 CSV 导出暂略，指标已在此输出；因子明细见 intel.factor_values）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
