"""intel 源 seed：把 P0 实测可用的 5 个 RSS 源灌进 intel.sources

用法（data-cleaner 根目录）：
    python scripts/seed_intel_sources.py            # dry-run：只打印将写入的源
    python scripts/seed_intel_sources.py --apply    # 实际写库（幂等，ON CONFLICT url DO UPDATE）

源清单依据：docs/memo/2026-09-07.intel-module-plan.md §1
（2026-09-07 连通性探针实测：全部 HTTP 200 + 真 RSS/Atom；36氪 SPA 死链已剔除，爱范儿替补）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intel import store  # noqa: E402

# 实测可用源（plan §1.2 首批 5 个）
SOURCES: list[dict] = [
    {
        "source_type": "rss", "name": "华尔街见闻",
        "url": "https://dedicated.wallstreetcn.com/rss.xml",
        "credibility": "high", "poll_interval_sec": 300,
        "robots_ok": True, "enabled": True,
    },
    {
        "source_type": "rss", "name": "东方财富股票",
        "url": "http://rss.eastmoney.com/rss_stock.xml",  # http 非 https（实测可达）
        "credibility": "high", "poll_interval_sec": 300,
        "robots_ok": True, "enabled": True,
    },
    {
        "source_type": "rss", "name": "虎嗅",
        "url": "https://rss.huxiu.com/",
        "credibility": "medium", "poll_interval_sec": 300,
        "robots_ok": True, "enabled": True,
    },
    {
        "source_type": "rss", "name": "钛媒体",
        "url": "https://www.tmtpost.com/rss.xml",
        "credibility": "medium", "poll_interval_sec": 600,  # 实测延迟偏高 ~1.4s，降频
        "robots_ok": True, "enabled": True,
    },
    {
        "source_type": "rss", "name": "爱范儿",
        "url": "https://www.ifanr.com/feed",
        "credibility": "medium", "poll_interval_sec": 300,
        "robots_ok": True, "enabled": True,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    args = parser.parse_args()

    print(f"{'源':<12}{'类型':<7}{'轮询s':<7}{'可信':<8}URL")
    print("-" * 90)
    for s in SOURCES:
        print(f"{s['name']:<12}{s['source_type']:<7}{s['poll_interval_sec']:<7}"
              f"{s['credibility']:<8}{s['url']}")

    if not args.apply:
        print("\n[dry-run] 未写库。确认无误后加 --apply 实际写入。")
        return

    for s in SOURCES:
        sid = store.upsert_source(s)
        print(f"  upsert {s['name']} -> id={sid}")
    print(f"\n完成：{len(SOURCES)} 个源已写入 intel.sources（enabled=true）")


if __name__ == "__main__":
    main()
