"""P4-4 作者风格总结报告运行入口

为每位作者（或源）调一次云端 LLM，基于其聚合统计 + 近期观点样本生成风格综述，
落 intel.author_style_summaries，后端 /api/v1/intel/profiles 会带进画像卡片。

用法：
  # 先预览：看会为哪些作者送审、prompt 多长（不调 LLM、不落库）
  .venv/Scripts/python.exe _p4_build_style_summaries.py
  # 真跑（需 INTEL_LLM_* 已配置；受日预算闸保护）
  .venv/Scripts/python.exe _p4_build_style_summaries.py --apply
  # 只跑指定作者 / 限制数量 / 调高样本红线
  .venv/Scripts/python.exe _p4_build_style_summaries.py --apply --authors 张三,李四
  .venv/Scripts/python.exe _p4_build_style_summaries.py --apply --limit 5 --min-mentions 10
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    argv = sys.argv[1:]
    is_apply = "--apply" in argv

    def opt(name: str, default=None, cast=str):
        if name in argv:
            try:
                return cast(argv[argv.index(name) + 1])
            except (IndexError, ValueError):
                print(f"{name} 参数非法")
                sys.exit(2)
        return default

    limit = opt("--limit", None, int)
    min_mentions = opt("--min-mentions", 3, int)
    profile_version = opt("--profile-version", "v1")
    summary_version = opt("--summary-version", "v1")
    authors_raw = opt("--authors", None)
    authors = [a.strip() for a in authors_raw.split(",")] if authors_raw else None

    from app.intel.aggregate import style_summary as SS

    if not is_apply:
        out = SS.build_style_summaries(
            profile_version=profile_version, summary_version=summary_version,
            authors=authors, limit=limit, min_mentions=min_mentions, dry_run=True,
        )
        print("== P4-4 风格总结 dry-run（不调 LLM）==")
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        print("\ndry-run 完成（--apply 真跑 LLM，需 INTEL_LLM_* 已配置）")
        return 0

    from app.intel.core.config import settings
    if not (settings.INTEL_LLM_BASE_URL and settings.INTEL_LLM_API_KEY
            and settings.INTEL_LLM_MODEL):
        print("INTEL_LLM_BASE_URL / INTEL_LLM_API_KEY / INTEL_LLM_MODEL 未配置，无法真跑。")
        print("示例（DeepSeek）：")
        print("  INTEL_LLM_BASE_URL=https://api.deepseek.com/v1")
        print("  INTEL_LLM_API_KEY=sk-...")
        print("  INTEL_LLM_MODEL=deepseek-chat")
        return 1

    out = SS.build_style_summaries(
        profile_version=profile_version, summary_version=summary_version,
        authors=authors, limit=limit, min_mentions=min_mentions,
    )
    print("== P4-4 风格总结结果 ==")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
