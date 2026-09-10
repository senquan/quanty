"""P4-1 运行入口：人工投喂（URL 清单 / 导出文件 / 目录）→ intel.documents。

用法：
  .venv/Scripts/python.exe _p4_manual_ingest.py --target articles.csv
  .venv/Scripts/python.exe _p4_manual_ingest.py --target ./weixin_export/ --source-name "XXX公众号"
  .venv/Scripts/python.exe _p4_manual_ingest.py --target page.html --dry-run

输入自动判别：
  .txt/.csv  → 每行/每列一个 URL，逐条抓取
  .html/.htm/.mhtml → 浏览器导出/剪藏网页，解析标题/作者/时间/正文
  .txt/.md   → 整篇纯文本文章
  目录       → 递归按扩展名分发

落库后理解层对来源无差别，跑 scripts/run_intel_understanding.py --apply 即接管
抽取 → 画像 → INTL_STYLE_MATCH 全链路。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intel.ingest.manual import ManualSource
from app.intel.service import run_manual_ingest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="URL清单文件 / 导出文件 / 目录")
    ap.add_argument("--source-name", default=None, help="manual 源名（默认目录名或文件名主干）")
    ap.add_argument("--limit", type=int, default=None, help="最多入库篇数")
    ap.add_argument("--dry-run", action="store_true", help="只解析不写库，打印抽样")
    args = ap.parse_args()

    if args.dry_run:
        src_name = args.source_name or Path(args.target).stem or "manual"
        try:
            items = ManualSource(src_name).collect(args.target)
        except Exception as e:  # noqa: BLE001
            print(f"解析失败: {type(e).__name__}: {e}")
            return 1
        if args.limit:
            items = items[:args.limit]
        print(f"解析出 {len(items)} 篇：")
        for it in items[:10]:
            print(
                f"  - 标题={it.title[:36]!r}  url={it.url[:48]}  "
                f"时间={it.published_at}  作者={it.author!r}  正文{len(it.content_html)}字"
            )
        if len(items) > 10:
            print(f"  ... 其余 {len(items) - 10} 篇")
        print("\n[dry-run] 未写库")
        return 0

    stats = run_manual_ingest(
        args.target, source_name=args.source_name, limit=args.limit
    )
    print("=== P4-1 人工投喂入库 ===")
    print(f"源: {stats.get('source')} (id={stats.get('source_id')})")
    print(
        f"抓取={stats['fetched']}  新增={stats['new']}  "
        f"重复={stats['dup']}  转载={stats['reposts']}"
    )
    print("下一步：python scripts/run_intel_understanding.py --apply  抽取→画像→因子")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
