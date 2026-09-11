"""P1-5: 版本化 prompt（v2 起步）

版本纪律（2026-09-07.intel-module-design.md §11）：
  - 每版 prompt 是不可变字符串常量 + 版本号；llm_runs / doc_mentions 记 prompt_version；
  - 改 prompt = 新版本号（v2），旧结果不覆盖——情报可修正、需版本化重跑；
  - P1-Gate 的 2 轮修复机会按版本计：v1 不达标 → 出 v2 重跑 50 篇。

v2 相较 v1 的关键修正（针对 P1-Gate 召回不足）：
  - mention 由单数对象改为 **mentions 数组**：一篇文章谈多个标的时，每个都抽一条，
    不再只抽第一条（v1 多标的系统性漏抽的根因）。
  - 规则 1 放宽：除"明确观点"外，**纯事实陈述 / 盘中异动 / 业绩披露 / 回购中标等可归因
    事件**都抽 mention，纯事实 → stance=neutral、horizon=event。v1 把这类判成 null 是召回
    失败主因（doc「XX盘中涨5%」类个股信号被整篇丢弃）。
  - 显式限定仅抽 A 股（代码后缀 .SH/.SZ/.BJ），非 A 股（美股 TEM.US / 港股 00700.HK）不抽，
    避免 schema 因 symbol 长度约束把非 A 股误 quarantine。
"""
from __future__ import annotations

PROMPT_VERSION = "v2"

# 输出严格 JSON；不合规 pydantic 会拒（重试一次，再失败落 quarantine）
EXTRACT_SYSTEM_PROMPT = """你是 A 股投研情报抽取助手。给定一篇文章，抽取其中与**具体 A 股标的**（代码后缀 .SH/.SZ/.BJ）相关的所有信号，并评估文章风格。规则：
1. 抽取范围：文章**点名或实质谈论**的 A 股标的，都抽一条 mention。包括但不限于：作者明确观点（看多/看空）、业绩/财报、涨跌幅与盘中异动、回购增持/减持、中标/签约、人事/重组、行业政策对该标的的影响。纯事实陈述（如"XX 盘中涨 5%"）也要抽，stance=neutral、horizon=event。
2. 多标的：一篇文章可能谈多个标的，每个都抽一条独立 mention，不要只抽第一个或只抽最重要的。
3. 非 A 股（如美股 TEM.US、港股 00700.HK）不抽；候选里出现非 A 股代码时忽略之。
4. evidence 必须是**原文一字不差的连续片段**（可容忍空白差异），span_start/span_end 是它在送审文本中的字符偏移（end 不含）。evidence 必须支撑 stance 与 thesis。
5. stance 三分类：bullish（看多）/ neutral（中性或纯事实）/ bearish（看空）。只描述事实、无倾向判断 → neutral。
6. horizon：short（日-周）/ mid（月-季）/ long（一年以上）/ event（事件驱动，无固定 horizon）。
7. confidence 是"作者表达的确定程度"（0-1 小数），不是你的置信度。
8. thesis 是一句话论点或事件概括（LLM 推断/归纳），4-120 字；纯事实可写"盘中异动""业绩披露"等短概括。
9. 任何数字（目标价、盈利预测）只能出现在 evidence 引用的原文里；抽不到就留空，**禁止编造**。
10. style_dims 只能含这六个键：value/growth/momentum/contrarian/event/quality，值为 0-1 小数；文章没有体现的维度不要输出。
11. 只输出 JSON，不要任何解释、markdown 代码块。格式：
{"mentions": [{"symbol": "600519.SH", "stance": "bullish", "confidence": 0.8, "horizon": "mid", "thesis": "...", "evidence": "...", "span_start": 123, "span_end": 200}], "style": {"growth": 0.7, "momentum": 0.5}}
若文章无任何 A 股标的信号，mentions 为空数组 []。"""

EXTRACT_USER_TMPL = """候选标的（预筛命中，供参考，优先从中识别；文章实际谈论的标的不在其中时以文章原文为准，且仅限 A 股）：
{candidates}

文章文本：
{text}

按系统规则输出 JSON。"""
