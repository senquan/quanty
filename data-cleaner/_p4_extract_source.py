# -*- coding: utf-8 -*-
"""按**源名**定向抽取该源的欠账文档（D-3 固化后的辅助工具）。

背景
----
``docs_for_understanding`` 原先按 ``ORDER BY d.id`` 升序 + limit，永远先抽 id 小的
老 RSS 欠账，后来者饿死。**D-3 已把优先级固化**（``INTEL_EXTRACT_PRIORITY``：
manual/wechat 先于 rss，同级按 available_at 倒序），所以日常轮次不再需要本脚本。

保留用途：**按源名**一次性清掉某个源的欠账（例如想单独把「分红养老之路」补齐，
或验收某个公众号的完整链路），而不想等全局队列轮到自己。

用法：
    .venv/Scripts/python.exe _p4_extract_source.py --source 分红养老之路 [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

from app.intel.store import get_engine, text
from app.intel.understand import service as usvc


def _pending_ids(src: str) -> list[int]:
    """该源下**当前 prompt_version 无 ok run** 的文档 id（按优先级同款顺序）"""
    from app.intel.understand.prompts import PROMPT_VERSION

    with get_engine().begin() as c:
        return [int(r[0]) for r in c.execute(
            text("""SELECT d.id FROM intel.documents d
                    JOIN intel.sources s ON s.id = d.source_id
                    WHERE s.name = :n
                      AND NOT EXISTS (
                          SELECT 1 FROM intel.llm_runs r
                          WHERE r.doc_id = d.id AND r.status = 'ok'
                            AND r.prompt_version = :pv
                      )
                    ORDER BY d.available_at DESC NULLS LAST, d.id"""),
            {"n": src, "pv": PROMPT_VERSION},
        )]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="源名（intel.sources.name）")
    ap.add_argument("--limit", type=int, default=0, help="本次抽取篇数上限，0=全部")
    ap.add_argument("--dry-run", action="store_true",
                    help="只数 doc_ids 与预筛命中，不调 LLM（零成本）")
    args = ap.parse_args()

    ids = _pending_ids(args.source)
    if not ids:
        print(f"[{args.source}] 无待抽取文档（该源已全部理解）")
        return 0
    print(f"[{args.source}] 待抽取文档 = {len(ids)} 条, id[{min(ids)}..{max(ids)}]")

    if args.dry_run:
        from app.intel.understand import prescreen as pre

        index = pre.get_alias_index()
        hit = 0
        with get_engine().begin() as c:
            for i in range(0, len(ids), 100):
                for r in c.execute(
                    text("""SELECT d.title, d.content FROM intel.documents d
                            WHERE d.id = ANY(:ids)"""),
                    {"ids": ids[i:i + 100]},
                ):
                    if pre.prescreen(r[0], r[1], index).hit:
                        hit += 1
        print(f"预筛命中 ≈ {hit} / {len(ids)}（这些才会送 LLM 花钱）")
        return 0

    todo = ids[: args.limit] if args.limit else ids
    print(f"送 {len(todo)} 篇进理解层抽取（doc_ids 定向）...")
    s = usvc.run_understanding_batch(limit=len(todo), doc_ids=todo)
    print(f"candidates={s.get('candidates')} prescreen_hit={s.get('prescreen_hit')} "
          f"prescreen_miss={s.get('prescreen_miss')}")
    print(f"extracted={s.get('extracted')} no_mention={s.get('no_mention')} "
          f"quarantined={s.get('quarantined')} api_fail={s.get('api_fail')}")
    print(f"cost_cny={s.get('cost_cny')} stopped_reason={s.get('stopped_reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
