# -*- coding: utf-8 -*-
"""临时脚本：对「分红养老之路」源的文档做定向理解层抽取。

背景：全局 docs_for_understanding 按 ORDER BY d.id 升序 + limit，永远先抽
id 小的老 RSS 欠账（东方财富 6777 篇未抽），今天一次灌入的 227 篇（id 8686+）
排在几千条之后根本轮不到。本脚本用 run_understanding_batch(doc_ids=...) 精确
锁定这批文档，绕开全局欠账顺序。

用法： .venv/Scripts/python.exe _p4_extract_source.py [--limit N]
"""
from __future__ import annotations

import argparse
import sys

from app.intel.understand import service as usvc
from app.intel.store import get_engine, text

SRC = "分红养老之路"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="本次抽取篇数上限，0=全部227")
    ap.add_argument("--dry-run", action="store_true", help="只数 doc_ids 与预筛命中，不调 LLM")
    args = ap.parse_args()

    with get_engine().begin() as c:
        ids = [r[0] for r in c.execute(
            text("""select d.id from intel.documents d join intel.sources s on s.id=d.source_id
                    where s.name=:n and not exists
                    (select 1 from intel.llm_runs r where r.doc_id=d.id and r.status='ok')
                    order by d.id"""),
            {"n": SRC},
        )]
    print(f"[{SRC}] 待抽取文档 = {len(ids)} 条, id[{min(ids)}..{max(ids)}]" if ids else "无")

    if args.dry_run:
        # 预筛命中数（零 LLM）
        from app.intel.understand import prescreen as pre
        index = pre.get_alias_index()
        hit = 0
        with get_engine().begin() as c:
            for i in range(0, len(ids), 100):
                chunk = ids[i:i+100]
                for r in c.execute(
                    text("""select d.id, d.title, d.content from intel.documents d where d.id = ANY(:ids)"""),
                    {"ids": chunk},
                ):
                    p = pre.prescreen(r[1], r[2], index)  # id, title, content
                    if p.hit:
                        hit += 1
        print(f"预筛命中 ≈ {hit} / {len(ids)}（这些才会送 LLM 花钱）")
        return 0

    todo = ids[: args.limit] if args.limit else ids
    if not todo:
        print("没有待抽取文档")
        return 0

    print(f"送 {len(todo)} 篇进理解层抽取（doc_ids 定向）...")
    s = usvc.run_understanding_batch(limit=len(todo), doc_ids=todo)
    print(
        f"candidates={s.get('candidates')} prescreen_hit={s.get('prescreen_hit')} "
        f"prescreen_miss={s.get('prescreen_miss')}"
    )
    print(
        f"extracted={s.get('extracted')} no_mention={s.get('no_mention')} "
        f"quarantined={s.get('quarantined')} api_fail={s.get('api_fail')}"
    )
    print(f"cost_cny={s.get('cost_cny')} stopped_reason={s.get('stopped_reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
