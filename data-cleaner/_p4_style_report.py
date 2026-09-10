"""P4-4 作者风格总结报告：从 intel.author_style_summaries 生成自包含 HTML 报告。

用法：
    .venv/Scripts/python.exe _p4_style_report.py [--out p4_style_report.html]

说明：复用 app.intel.store 的 engine（不裸写 DB 密码）；报告为纯读取，不写库、不调 LLM。
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime

from app.intel import store
from app.intel.store import get_engine

PERIOD_CN = {
    "short": "短线", "mid": "中线", "long": "长线",
    "event": "事件驱动", "swing": "波段",
}
CONVICTION_CN = {"low": "低", "medium": "中", "high": "高"}


def _json(v):
    if v is None:
        return []
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return []


def fetch_rows() -> list[dict]:
    sql = store.text("""
        SELECT s.profile_key, s.profile_type, s.summary, s.style_tags, s.sectors,
               s.holding_period, s.conviction, s.caveats, s.confidence,
               s.input_stats, s.sample_size, s.model,
               s.input_tokens, s.output_tokens, s.cost_cny, s.latency_ms,
               s.status, s.error, s.computed_at,
               p.total_mentions, p.total_docs
        FROM intel.author_style_summaries s
        LEFT JOIN intel.author_profiles p
               ON p.profile_key = s.profile_key AND p.profile_type = s.profile_type
        ORDER BY COALESCE(p.total_mentions, 0) DESC, s.profile_key
    """)
    with get_engine().connect() as c:
        return [dict(r) for r in c.execute(sql).mappings().all()]


def fetch_cost() -> list[dict]:
    sql = store.text("""
        SELECT prompt_version, count(*) AS n,
               sum(input_tokens) AS tin, sum(output_tokens) AS tout,
               round(sum(cost_cny)::numeric, 4) AS cost
        FROM intel.llm_runs GROUP BY prompt_version ORDER BY prompt_version
    """)
    with get_engine().connect() as c:
        return [dict(r) for r in c.execute(sql).mappings().all()]


def _bar(conf: float | None) -> str:
    if conf is None:
        return '<span class="muted">—</span>'
    pct = max(0.0, min(1.0, float(conf))) * 100
    color = "#16a34a" if conf >= 0.6 else ("#d97706" if conf >= 0.4 else "#dc2626")
    return (f'<div class="bar"><i style="width:{pct:.0f}%;background:{color}"></i></div>'
            f'<span class="bar-val">{conf:.2f}</span>')


def _chips(items: list, cls: str = "chip") -> str:
    if not items:
        return '<span class="muted">—</span>'
    return "".join(f'<span class="{cls}">{html.escape(str(i))}</span>' for i in items)


def render(rows: list[dict], cost: list[dict]) -> str:
    ok_rows = [r for r in rows if r["status"] == "ok"]
    skip_rows = [r for r in rows if r["status"] != "ok"]
    total_cost = sum(float(r["cost_cny"] or 0) for r in ok_rows)
    avg_lat = (sum(int(r["latency_ms"] or 0) for r in ok_rows) / len(ok_rows)) if ok_rows else 0
    confs = [float(r["confidence"]) for r in ok_rows if r["confidence"] is not None]
    avg_conf = sum(confs) / len(confs) if confs else 0
    model = ok_rows[0]["model"] if ok_rows and ok_rows[0]["model"] else "—"

    cards = []
    for r in ok_rows:
        cards.append(f"""
      <section class="card">
        <header>
          <div class="who">
            <span class="name">{html.escape(r['profile_key'])}</span>
            <span class="badge">{'源' if r['profile_type'] == 'source' else '作者'}</span>
          </div>
          <div class="meta">
            观点 {r['total_mentions'] or 0} 条 · 样本 {r['sample_size']} 条 ·
            周期 <b>{PERIOD_CN.get(r['holding_period'], r['holding_period'] or '—')}</b> ·
            笃定度 <b>{CONVICTION_CN.get(r['conviction'], r['conviction'] or '—')}</b>
          </div>
        </header>
        <div class="kv">
          <div class="k">风格标签</div><div class="v">{_chips(_json(r['style_tags']))}</div>
          <div class="k">覆盖行业</div><div class="v">{_chips(_json(r['sectors']), 'chip chip-alt')}</div>
          <div class="k">结论可靠度</div><div class="v">{_bar(r['confidence'])}</div>
        </div>
        <p class="summary">{html.escape(r['summary'] or '')}</p>
        <p class="caveat"><b>局限：</b>{html.escape(r['caveats'] or '—')}</p>
        <footer>tokens {r['input_tokens']}/{r['output_tokens']} ·
          ¥{float(r['cost_cny'] or 0):.4f} · {int(r['latency_ms'] or 0)} ms ·
          {r['computed_at']:%Y-%m-%d %H:%M}</footer>
      </section>""")

    skip_html = ""
    if skip_rows:
        items = "".join(
            f"<li><b>{html.escape(r['profile_key'])}</b> — "
            f"{html.escape(r['error'] or r['status'])}</li>" for r in skip_rows)
        skip_html = f"""
      <section class="card flat">
        <header><div class="who"><span class="name">未生成（{len(skip_rows)}）</span></div></header>
        <ul class="skip">{items}</ul>
      </section>"""

    cost_rows = "".join(
        f"<tr><td>{html.escape(str(c['prompt_version']))}</td><td class='num'>{c['n']}</td>"
        f"<td class='num'>{c['tin'] or 0}</td><td class='num'>{c['tout'] or 0}</td>"
        f"<td class='num'>¥{float(c['cost'] or 0):.4f}</td></tr>" for c in cost)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P4-4 作者风格总结报告</title>
<style>
  :root {{ --bg:#f6f7f9; --card:#fff; --line:#e5e7eb; --tx:#1f2328; --mut:#6b7280; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:28px 20px 60px; background:var(--bg); color:var(--tx);
         font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--mut); font-size:13px; margin-bottom:20px; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
            gap:12px; margin-bottom:22px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:14px 16px; }}
  .stat .n {{ font-size:22px; font-weight:600; }}
  .stat .l {{ color:var(--mut); font-size:12px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:18px 20px; margin-bottom:14px; }}
  .card header {{ margin-bottom:12px; }}
  .who {{ display:flex; align-items:center; gap:8px; }}
  .name {{ font-size:16px; font-weight:600; }}
  .badge {{ font-size:11px; color:#4338ca; background:#eef2ff; border:1px solid #c7d2fe;
            border-radius:999px; padding:1px 8px; }}
  .meta {{ color:var(--mut); font-size:12px; margin-top:4px; }}
  .kv {{ display:grid; grid-template-columns:80px 1fr; gap:6px 12px; margin:10px 0 12px; }}
  .k {{ color:var(--mut); font-size:12px; }}
  .v {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; }}
  .chip {{ font-size:12px; background:#f3f4f6; border:1px solid var(--line);
           border-radius:6px; padding:1px 8px; }}
  .chip-alt {{ background:#f0fdf4; border-color:#bbf7d0; color:#166534; }}
  .bar {{ flex:0 0 120px; height:6px; background:#eef0f3; border-radius:999px;
          overflow:hidden; }}
  .bar i {{ display:block; height:100%; }}
  .bar-val {{ font-size:12px; color:var(--mut); }}
  .summary {{ margin:0 0 8px; }}
  .caveat {{ margin:0; font-size:12.5px; color:#92400e; background:#fffbeb;
             border:1px solid #fde68a; border-radius:8px; padding:8px 10px; }}
  .card footer {{ margin-top:10px; font-size:11.5px; color:var(--mut); }}
  .card.flat {{ padding:14px 20px; }}
  .skip {{ margin:0; padding-left:18px; color:var(--mut); font-size:12.5px; }}
  table {{ border-collapse:collapse; width:100%; background:var(--card);
           border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left;
           font-size:13px; }}
  th {{ background:#fafafa; font-weight:600; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .note {{ margin-top:18px; font-size:12px; color:var(--mut); background:#fff;
           border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
</style></head><body><div class="wrap">
  <h1>P4-4 作者风格总结报告</h1>
  <div class="sub">模型 {html.escape(str(model))} · prompt_version style_v1 ·
    生成于 {datetime.now():%Y-%m-%d %H:%M}</div>

  <div class="stats">
    <div class="stat"><div class="n">{len(ok_rows)}</div><div class="l">已生成</div></div>
    <div class="stat"><div class="n">{len(skip_rows)}</div><div class="l">未生成</div></div>
    <div class="stat"><div class="n">¥{total_cost:.4f}</div><div class="l">本次成本</div></div>
    <div class="stat"><div class="n">{avg_lat:.0f} ms</div><div class="l">平均延迟</div></div>
    <div class="stat"><div class="n">{avg_conf:.2f}</div><div class="l">平均可靠度</div></div>
  </div>

  {''.join(cards)}
  {skip_html}

  <h2 style="font-size:16px;margin:26px 0 10px">LLM 成本归因（llm_runs）</h2>
  <table>
    <tr><th>prompt_version</th><th class="num">调用数</th><th class="num">输入 tok</th>
        <th class="num">输出 tok</th><th class="num">成本</th></tr>
    {cost_rows}
  </table>

  <div class="note">
    <b>读法与免责</b>：本报告由 LLM 基于该作者的<b>聚合统计 + 近期观点样本</b>生成，
    结论是<b>对已有素材的归纳</b>，不等于该作者的真实长期风格。
    "结论可靠度"由模型自评，当前普遍偏低（语料时间跨度仅 1–2 天、单作者样本多为个位数），
    <b>请勿据此做投资决策</b>；样本积累到数十条以上后重跑才有统计意义。<br>
    成本与抽取（v1/v2）分开归因在 <code>prompt_version=style_v1</code>；
    失败调用同样记账（tokens=0），便于事后核对故障。
  </div>
</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="p4_style_report.html")
    args = ap.parse_args()

    rows, cost = fetch_rows(), fetch_cost()
    if not rows:
        print("intel.author_style_summaries 无数据，先跑 _p4_build_style_summaries.py --apply")
        return 1
    html_text = render(rows, cost)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"已生成 {args.out}（{len(rows)} 条记录，"
          f"{sum(1 for r in rows if r['status'] == 'ok')} 条 ok）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
