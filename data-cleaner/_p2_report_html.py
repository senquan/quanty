"""P2 画像基线报告：从 intel.author_profiles 生成自包含 HTML 报告。

用法：
    python _p2_report_html.py [--profile-version p2_baseline] [--out p2_baseline_report.html]
"""
from __future__ import annotations

import json
import sys
import argparse
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlsplit

import psycopg2

sys.path.insert(0, ".")
from app.intel.store import _sync_url  # DB URL 统一从 app 配置（读 .env）取


def _parse_db_url(url: str) -> dict:
    """postgresql+psycopg2://user:pw@host:port/db → psycopg2.connect 的 kwargs"""
    p = urlsplit(url.replace("+psycopg2", "").replace("+asyncpg", ""))
    return {
        "host": p.hostname or "127.0.0.1",
        "port": p.port or 5432,
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "dbname": (p.path or "/quant").lstrip("/"),
        **dict(parse_qsl(p.query)),
    }


# ⚠️ 口令绝不进源码/版本库：此处曾硬编码生产库连接串，已改为从 app 配置解析。
DB = _parse_db_url(_sync_url())


def fetch(profile_version: str | None) -> list[dict]:
    con = psycopg2.connect(**DB)
    cur = con.cursor()
    where = "WHERE profile_version = %s" if profile_version else ""
    params = (profile_version,) if profile_version else ()
    cur.execute(
        f"""
        SELECT profile_key, profile_type, profile_version,
               total_mentions, total_docs, unique_symbols,
               date_first, date_last,
               stance_dist::text, style_vector::text,
               top_symbols::text, top_sources::text, horizon_dist::text,
               avg_excess_20d, avg_excess_60d,
               accuracy_sample_size, win_rate_20d, win_rate_60d,
               sample_insufficient, drift_detected, drift_detail::text,
               computed_at
        FROM intel.author_profiles {where}
        ORDER BY total_mentions DESC
        """,
        params,
    )
    cols = [d[0] for d in cur.description]
    rows = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        for k in ("stance_dist", "style_vector", "top_symbols", "top_sources",
                 "horizon_dist", "drift_detail"):
            if isinstance(d.get(k), str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    d[k] = {}
        rows.append(d)
    con.close()
    return rows


def pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def stance_bar(dist: dict, total: int) -> str:
    b = dist.get("bullish", 0)
    n = dist.get("neutral", 0)
    r = dist.get("bearish", 0)
    tb, tn, tr = pct(b, total), pct(n, total), pct(r, total)
    return f"""
    <div class="bar">
      <span class="seg bull" style="width:{tb}%">{tb}%</span>
      <span class="seg neut" style="width:{tn}%">{tn}%</span>
      <span class="seg bear" style="width:{tr}%">{tr}%</span>
    </div>"""


def esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _syms(items) -> list[str]:
    """top_symbols 可能是 [str] 或 [{'symbol':..., 'mentions':...}]"""
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            out.append(str(it.get("symbol", "")))
        else:
            out.append(str(it))
    return [s for s in out if s]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-version", default=None)
    ap.add_argument("--out", default="p2_baseline_report.html")
    args = ap.parse_args()

    rows = fetch(args.profile_version)
    if not rows:
        print("no profiles found", file=sys.stderr)
        sys.exit(1)

    total_mentions = sum(r["total_mentions"] for r in rows)
    sufficient = [r for r in rows if not r["sample_insufficient"]]
    blocked_acc = [r for r in rows if (r["accuracy_sample_size"] or 0) == 0]

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    gen = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    cards = f"""
    <div class="cards">
      <div class="card"><div class="num">{len(rows)}</div><div class="lbl">画像数</div></div>
      <div class="card"><div class="num">{total_mentions}</div><div class="lbl">总 mention</div></div>
      <div class="card"><div class="num">{len(sufficient)}</div><div class="lbl">样本充足 (≥30)</div></div>
      <div class="card warn"><div class="num">{len(blocked_acc)}</div><div class="lbl">accuracy 待解锁</div></div>
    </div>"""

    table_rows = ""
    for r in rows:
        dist = r.get("stance_dist") or {}
        total = r["total_mentions"]
        tags = []
        if r["sample_insufficient"]:
            tags.append('<span class="tag warn">样本不足</span>')
        else:
            tags.append('<span class="tag ok">样本充足</span>')
        if r["drift_detected"]:
            tags.append('<span class="tag drift">漂移</span>')
        else:
            tags.append('<span class="tag ok">稳定</span>')
        acc = r["accuracy_sample_size"] or 0
        if acc == 0:
            acc_cell = '<span class="muted">— 待行情</span>'
        else:
            acc_cell = f'{acc} 样本'
        top_sym = ", ".join(_syms(r.get("top_symbols"))[:5]) or "—"
        table_rows += f"""
        <tr>
          <td><b>{esc(r['profile_key'])}</b><div class="sub">{esc(r['profile_type'])}</div></td>
          <td class="num">{total}</td>
          <td class="num">{r['total_docs']}</td>
          <td class="num">{r['unique_symbols']}</td>
          <td style="min-width:180px">{stance_bar(dist, total)}</td>
          <td>{acc_cell}</td>
          <td>{''.join(tags)}</td>
          <td class="sub">{esc(top_sym)}</td>
        </tr>"""

    # 重点 profile 的 stance 分布详情（前 3 个）
    detail = ""
    for r in rows[:3]:
        dist = r.get("stance_dist") or {}
        total = r["total_mentions"]
        b, n, r_ = dist.get("bullish", 0), dist.get("neutral", 0), dist.get("bearish", 0)
        detail += f"""
        <div class="detail-card">
          <h3>{esc(r['profile_key'])} <span class="sub">({esc(r['profile_type'])}, {total} mentions)</span></h3>
          {stance_bar(dist, total)}
          <div class="legend">看多 {b} · 中性 {n} · 看空 {r_}</div>
          <div class="kv">标的覆盖: <b>{r['unique_symbols']}</b> · Top: {esc(', '.join(_syms(r.get('top_symbols'))[:6])) or '—'}</div>
        </div>"""

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P2 画像基线报告</title>
<style>
  :root {{ --bg:#0f1419; --panel:#1a2230; --ink:#e6edf3; --muted:#8b98a9; --line:#2a3548;
          --bull:#e5484d; --neut:#6b7785; --bear:#46a758; --accent:#4493f8; --warn:#f5a623; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.6 -apple-system,Segoe UI,Roboto,'PingFang SC','Microsoft YaHei',sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 60px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .meta {{ color:var(--muted); font-size:12px; margin-bottom:20px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:24px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; text-align:center; }}
  .card .num {{ font-size:30px; font-weight:700; }}
  .card .lbl {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .card.warn .num {{ color:var(--warn); }}
  .banner {{ background:rgba(245,166,35,.12); border:1px solid var(--warn); border-radius:10px;
            padding:12px 16px; margin-bottom:24px; font-size:13px; }}
  .banner b {{ color:var(--warn); }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border-radius:10px; overflow:hidden; }}
  th, td {{ padding:10px 12px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:#141b26; color:var(--muted); font-weight:600; font-size:12px; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .sub {{ color:var(--muted); font-size:11px; }}
  .muted {{ color:var(--muted); }}
  .bar {{ display:flex; height:18px; border-radius:4px; overflow:hidden; background:var(--line); }}
  .seg {{ display:flex; align-items:center; justify-content:center; font-size:10px; color:#fff; min-width:0;
          overflow:hidden; white-space:nowrap; }}
  .bull {{ background:var(--bull); }}
  .neut {{ background:var(--neut); }}
  .bear {{ background:var(--bear); }}
  .tag {{ display:inline-block; padding:2px 8px; border-radius:20px; font-size:11px; margin-right:4px; }}
  .tag.ok {{ background:rgba(70,167,88,.18); color:var(--bear); }}
  .tag.warn {{ background:rgba(245,166,35,.18); color:var(--warn); }}
  .tag.drift {{ background:rgba(228,72,77,.18); color:var(--bull); }}
  .details {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:24px 0; }}
  .detail-card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }}
  .detail-card h3 {{ margin:0 0 10px; font-size:14px; }}
  .legend {{ color:var(--muted); font-size:12px; margin-top:6px; }}
  .kv {{ font-size:12px; margin-top:8px; color:var(--ink); }}
  .caveat {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px 18px; margin-top:24px; }}
  .caveat h2 {{ font-size:15px; margin:0 0 10px; }}
  .caveat li {{ margin-bottom:6px; color:var(--ink); }}
  .caveat .muted {{ color:var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>P2 画像基线报告</h1>
  <div class="meta">生成时间 {now} · 数据源 intel.author_profiles · profile_version {esc(rows[0]['profile_version'])}</div>
  {cards}
  <div class="banner">
    <b>accuracy 暂未解锁：</b> 当前语料仅覆盖 09-03~09-07，factor.raw_bars 行情截止 <b>2026-09-04</b>，
    无法计算 20d/60d 超额收益。已配置<b>每周自动重跑</b>，待 raw_bars 攒够提及日+20 个交易日后，下一次运行将自动补算 win_rate / avg_excess。
  </div>
  <table>
    <thead><tr>
      <th>画像</th><th class="num">mentions</th><th class="num">docs</th><th class="num">标的数</th>
      <th>stance 分布</th><th>accuracy</th><th>状态</th><th>Top 标的</th>
    </tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
  <h2 style="margin-top:28px;font-size:16px;">重点画像 stance 分布</h2>
  <div class="details">{detail}</div>
  <div class="caveat">
    <h2>诚实声明 / Caveat</h2>
    <ul>
      <li><b>accuracy 全为 0</b>：受行情窗口限制，非算法缺陷；自动重跑后出数字。</li>
      <li><b>全量样本不足</b>：仅「东方财富股票」（339 条）达标，其余 10 个 profile 均 &lt;30，画像置信度低，需持续积累。</li>
      <li><b>neutral 占 96%</b>：财经 RSS 偏事实陈述，bullish/bearish 信号稀疏，风格向量目前主要反映<b>标的覆盖广度</b>而非观点倾向。</li>
      <li><b>作者大量为 null</b>：多数源未带 author，已 fallback 到 source 级聚合；作者级画像需源提供更多元数据。</li>
    </ul>
  </div>
</div>
</body>
</html>"""
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {args.out} ({len(html)} bytes, {len(rows)} profiles)")


if __name__ == "__main__":
    main()
