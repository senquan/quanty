"""intel 理解层（L2）：确定性预筛 + LLM 抽取

- ``keywords``  / ``prescreen`` : L2a 零 LLM 筛子（可单测、零成本）
- ``schema``    / ``prompts``   : L2b 输出契约与版本化 prompt
- ``llm.client``               : 云端 provider + 预算闸 + llm_runs 审计
- ``service``                  : 批跑编排（预筛命中 → 抽取 → span 校验 → 落库）
"""
