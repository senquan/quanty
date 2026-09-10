"""P1-Gate 评估（v2 跑完后运行）

用法：.venv/Scripts/python.exe _p1_gate_eval.py

自动指标（机器可测部分）：
  - 总 mention / 去重 symbol / stance 分布
  - symbol 格式合规率（A 股 .SH/.SZ/.BJ，长度 8-12）
  - span 有效率：evidence 归一化后是否真在送审原文（title+\n\n+content[:8000]）中
  - 疑似幻觉率：evidence 不在原文的比例（P1-Gate 阈值 ≤2%）

并导出 50 篇含 mention 的文档标注样本（p1_gate_sample.csv），留人工标注列供老朱核对。
"""
import sys, csv, psycopg2
sys.path.insert(0, ".")
from app.intel.understand.schema import _norm_ws

URL = "postgresql://postgres:abdxJMPj7SWf@127.0.0.1:5432/quant"
conn = psycopg2.connect(URL)
cur = conn.cursor()

# 全量 mention + 关联文档
cur.execute("""
    SELECT m.id, m.doc_id, m.symbol, m.stance, m.confidence, m.horizon,
           m.thesis, m.evidence, m.span_start, m.span_end, d.title, d.content
    FROM intel.doc_mentions m JOIN intel.documents d ON d.id = m.doc_id
    WHERE m.prompt_version = 'v2'
    ORDER BY m.doc_id
""")
rows = cur.fetchall()

total = len(rows)
symbols = set()
stance_dist = {}
sym_ok = span_ok = halluc = 0
for r in rows:
    _, _, symbol, stance, _, _, _, evidence, sp_s, sp_e, title, content = r
    symbols.add(symbol)
    stance_dist[stance] = stance_dist.get(stance, 0) + 1
    if symbol.endswith((".SH", ".SZ", ".BJ")) and 8 <= len(symbol) <= 12:
        sym_ok += 1
    text = (title or "") + "\n\n" + (content or "")[:8000]
    if _norm_ws(evidence) and _norm_ws(evidence) in _norm_ws(text):
        span_ok += 1
    else:
        halluc += 1

print("=== P1-Gate 自动指标 ===")
print(f"总 mention:            {total}")
print(f"去重 symbol:          {len(symbols)}")
print(f"stance 分布:          {stance_dist}")
print(f"symbol 合规率:        {sym_ok}/{total} = {sym_ok/total:.1%}")
print(f"span 有效率:          {span_ok}/{total} = {span_ok/total:.1%}")
print(f"疑似幻觉率:           {halluc}/{total} = {halluc/total:.1%}")

# P1-Gate 阈值对照
print("\n=== P1-Gate 阈值对照（自动可测部分）===")
print(f"symbol≥90%?   {'PASS' if sym_ok/total >= 0.90 else 'FAIL'}  ({sym_ok/total:.1%})")
print(f"span≥95%?     {'PASS' if span_ok/total >= 0.95 else 'FAIL'}  ({span_ok/total:.1%})")
print(f"幻觉≤2%?      {'PASS' if halluc/total <= 0.02 else 'FAIL'}  ({halluc/total:.1%})")
print("stance≥80% 需人工标注判定（自动不可测）")

# 抽 50 篇含 mention 的文档导出标注样本
cur.execute("SELECT DISTINCT doc_id FROM intel.doc_mentions WHERE prompt_version='v2' ORDER BY doc_id LIMIT 50")
doc_ids = [r[0] for r in cur.fetchall()]
with open("p1_gate_sample.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["doc_id", "title", "原文关键句_人工填", "llm_symbol", "llm_stance",
                "llm_thesis", "llm_evidence", "人工_标的", "人工_倾向", "人工_论断", "是否幻觉"])
    for did in doc_ids:
        cur.execute("SELECT title, content FROM intel.documents WHERE id=%s", (did,))
        title, content = cur.fetchone()
        cur.execute("SELECT symbol, stance, thesis, evidence FROM intel.doc_mentions WHERE doc_id=%s", (did,))
        for sym, st, thesis, ev in cur.fetchall():
            w.writerow([did, (title or "")[:60], "", sym, st, thesis, ev, "", "", "", ""])
print(f"\n已导出 {len(doc_ids)} 篇标注样本 -> p1_gate_sample.csv")
conn.close()
