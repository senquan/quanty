"""P4-2 微信半自动：目录 watch 运行入口

监听 ~/intel-inbox/（默认，可用 --inbox 覆盖），新文件按 子目录=公众号 分组自动入库，
处理完移入 processed/ 作幂等标记。落库后理解层 run_intel_understanding.py --apply 自动接管。

用法：
  # 扫描一轮（不循环）
  .venv/Scripts/python.exe _p4_wechat_watch.py --once
  # 常驻循环（每 30s 一轮，由 tasks 经 executor 调用时常用）
  .venv/Scripts/python.exe _p4_wechat_watch.py --loop
  # 先预览哪些文件会被处理（不写库、不移文件）
  .venv/Scripts/python.exe _p4_wechat_watch.py --dry-run
  # 指定监听目录 / 默认源 / 冷却窗口
  .venv/Scripts/python.exe _p4_wechat_watch.py --inbox D:/weixin --default-source 微信剪藏 --cooldown 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 允许脚本从仓库根直接运行（data-cleaner/）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.intel.core.config import settings  # noqa: E402
from app.intel.ingest.manual import ManualSource, _SUPPORTED_EXT  # noqa: E402
from app.intel.service import run_wechat_watch  # noqa: E402


def _dry_run(inbox: Path, cooldown_sec: float, default_source: str) -> None:
    from app.intel.ingest.manual import _SUPPORTED_EXT
    from app.intel.service import _wechat_inbox_candidates

    inbox = inbox.expanduser()
    if not inbox.exists():
        print(f"[dry-run] inbox 不存在: {inbox}")
        return
    # 用 cooldown=0 预览 inbox 下全部支持文件；再逐文件标注是否仍在冷却窗口内
    all_files = [
        f for f in inbox.rglob("*")
        if f.is_file() and "processed" not in f.parts
        and f.suffix.lower() in _SUPPORTED_EXT
    ]
    now = time.time()
    ready, pending = [], []
    for f in all_files:
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        (ready if age >= cooldown_sec else pending).append(f)
    print(f"[dry-run] inbox={inbox}")
    print(f"  支持文件总数={len(all_files)}  本轮可处理={len(ready)}  冷却中(待下轮)={len(pending)}")
    by_src: dict[str, list] = {}
    for f in sorted(ready, key=lambda p: str(p)):
        rel = f.relative_to(inbox)
        src = rel.parts[0] if len(rel.parts) > 1 else default_source
        try:
            n = len(ManualSource(src).collect(f))
        except Exception as e:  # noqa: BLE001
            n = f"(解析失败: {type(e).__name__}: {str(e)[:80]})"
        by_src.setdefault(src, []).append((Path(f).name, n))
    if not by_src:
        print("  本轮无可处理文件（或全部在冷却窗口内）")
        return
    for src, files in sorted(by_src.items()):
        print(f"  源【{src}】 {len(files)} 个文件")
        for name, n in files:
            print(f"    - {name}  解析出条目={n}")


def main() -> int:
    ap = argparse.ArgumentParser(description="intel P4-2 微信目录 watch")
    ap.add_argument("--inbox", default=None, help="监听目录（默认 settings.INTEL_INBOX_DIR）")
    ap.add_argument("--once", action="store_true", help="扫描一轮即退出（默认）")
    ap.add_argument("--loop", dest="loop", action="store_true", help="常驻循环（每 N 秒一轮）")
    ap.add_argument("--default-source", default=None, help="扁平文件归入的源名（默认 wechat-inbox）")
    ap.add_argument("--cooldown", type=float, default=None, help="跳过 mtime 距现在 < 秒 的文件")
    ap.add_argument("--no-move", dest="move", action="store_false",
                    help="处理完不移入 processed/（调试用）")
    ap.add_argument("--dry-run", action="store_true", help="只预览待处理文件，不写库/不移文件")
    args = ap.parse_args()

    inbox = Path(args.inbox) if args.inbox else Path(settings.INTEL_INBOX_DIR)
    default_source = args.default_source or settings.INTEL_INBOX_DEFAULT_SOURCE
    cooldown = args.cooldown if args.cooldown is not None else settings.INTEL_INBOX_COOLDOWN_SEC

    if args.dry_run:
        _dry_run(inbox, cooldown, default_source)
        return 0

    summary = run_wechat_watch(
        inbox=inbox,
        once=not args.loop,
        default_source=default_source,
        cooldown_sec=cooldown,
        move_processed=args.move,
    )
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
