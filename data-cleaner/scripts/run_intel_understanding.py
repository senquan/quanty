"""P1-6: intel 理解层批跑入口

用法：
  python scripts/run_intel_understanding.py            # dry-run：预筛过滤率报告（零 LLM、零成本）
  python scripts/run_intel_understanding.py --apply    # 真跑 LLM 批抽取（需 INTEL_LLM_* 配置）
  python scripts/run_intel_understanding.py --apply --limit 50
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intel import store
from app.intel.understand import service


def main() -> None:
    is_apply = "--apply" in sys.argv
    limit = 100
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    if not is_apply:
        docs = store.docs_for_understanding(limit)
        report = service.prescreen_report(docs)
        print("== P1-2 预筛过滤率报告（零 LLM）==")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print("\ndry-run 完成（--apply 真跑 LLM 抽取，需 INTEL_LLM_* 已配置）")
        return

    from app.intel.core.config import settings
    if not (settings.INTEL_LLM_BASE_URL and settings.INTEL_LLM_API_KEY and settings.INTEL_LLM_MODEL):
        print("INTEL_LLM_BASE_URL / INTEL_LLM_API_KEY / INTEL_LLM_MODEL 未配置，无法真跑。")
        print("示例（DeepSeek）：")
        print("  INTEL_LLM_BASE_URL=https://api.deepseek.com/v1")
        print("  INTEL_LLM_API_KEY=sk-...")
        print("  INTEL_LLM_MODEL=deepseek-chat")
        sys.exit(1)

    summary = service.run_understanding_batch(limit=limit)
    print("== P1-6 LLM 批抽取结果 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
