"""intel 摄取管线真实源冒烟（不落库）

对 seed 的 5 个实测源跑一轮：fetch → parse → normalize → content_hash → simhash，
**不写 PG、不落盘**，只打印每源计数与抽样，用于发现真实 feed 的结构怪癖
（东方财富 http、各源 content:encoded 有无、日期格式等）。

用法（data-cleaner 根目录）：
    python scripts/smoke_intel_ingest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_intel_sources import SOURCES  # noqa: E402
from app.intel.ingest.registry import create_source  # noqa: E402
from app.intel.normalize import dedupe  # noqa: E402
from app.intel.normalize import redline  # noqa: E402
from app.intel.normalize import text as norm_text  # noqa: E402


def main() -> None:
    total_ok = total_items = 0
    for src in SOURCES:
        result = create_source(src["source_type"], src["name"]).fetch(
            src["url"], limit=10, timeout=15.0
        )
        if not result.ok:
            print(f"❌ {src['name']:<8} FAILED {result.latency_ms}ms  {result.error}")
            continue
        total_ok += 1
        total_items += len(result.items)
        no_content = sum(1 for it in result.items if not it.content_html)
        no_date = sum(1 for it in result.items if not it.published_at)
        redline_hits = sum(
            1 for it in result.items if redline.match_redline(it.title, it.summary)
        )
        print(
            f"✅ {src['name']:<8} {result.latency_ms:>5}ms  items={len(result.items):<3}"
            f" 无正文={no_content} 无日期={no_date} 红线={redline_hits}"
        )
        # 抽样打印第一篇的规范化结果（验证 HTML 剥离 + hash + simhash 管线）
        if result.items:
            it = result.items[0]
            body = norm_text.normalize_text(it.content_html)
            chash = dedupe.content_hash(it.content_html or it.title)[:12]
            sim = dedupe.simhash(body)
            print(
                f"   └ 样例: {it.title[:38]!r} 正文{len(body)}字 hash={chash}… "
                f"simhash={'0(短文)' if sim == 0 else '…' + format(sim, 'x')[-8:]} "
                f"pub={it.published_at}"
            )

    print(f"\n汇总: {total_ok}/{len(SOURCES)} 源可达, 共 {total_items} 篇")
    if total_ok < len(SOURCES):
        sys.exit(1)


if __name__ == "__main__":
    main()
