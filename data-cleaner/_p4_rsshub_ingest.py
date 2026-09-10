"""P4-3 RSSHub 可选源：登记 + 运行入口

RSSHub 是第三方聚合（能抓微信公众号等），但稳定性/合规性不保证，故定位为**默认关闭**的备选路径：
源默认 enabled=False，仅用户显式 enable 后才会被 run_rsshub_ingest 拉取；无论拉取成功与否，
feed_health 都标 degraded（不假装稳）。

用法：
  # 登记一个源（默认关闭）
  .venv/Scripts/python.exe _p4_rsshub_ingest.py --register --name 公众号X --url "rsshub://wechat/customer/xxx"
  # 登记并立即开启
  .venv/Scripts/python.exe _p4_rsshub_ingest.py --register --name 公众号X --url "http://localhost:1200/wechat/..." --enable
  # 运行一轮（只拉已 enabled 的 rsshub 源）
  .venv/Scripts/python.exe _p4_rsshub_ingest.py --run
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from app.intel.core.config import settings  # noqa: E402
from app.intel.service import ensure_rsshub_source, run_rsshub_ingest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="intel P4-3 RSSHub 可选源")
    ap.add_argument("--register", action="store_true", help="登记一个 rsshub 源（默认关闭）")
    ap.add_argument("--name", help="源名")
    ap.add_argument("--url", help="feed URL，或 rsshub://<route>（按 INTEL_RSSHUB_BASE_URL 解析）")
    ap.add_argument("--enable", action="store_true", help="登记时一并开启（默认关闭）")
    ap.add_argument("--run", action="store_true", help="运行一轮 RSSHub 摄取")
    args = ap.parse_args()

    if args.register:
        if not args.name or not args.url:
            print("--register 需要 --name 与 --url")
            return 2
        res = ensure_rsshub_source(args.name, args.url, enabled=args.enable)
        print("已登记 rsshub 源:", json.dumps(res, ensure_ascii=False))
        if not args.enable:
            print("  注意：该源默认 enabled=False，未被拉取；用 --enable 或手动开启后 --run。")
        return 0

    if args.run:
        summary = run_rsshub_ingest()
        print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
