"""P1-4: LLM 抽取输出契约（pydantic strict）+ span 硬校验

纪律（2026-09-07.intel-module-design.md §4/§8）：
  - 事实 vs 推断分离：stance/thesis 是 LLM 推断，**必须带 span** 指回原文位置，
    无 span 不予入库，落 ``intel.quarantine``（附原因）；
  - 数字禁令：LLM 抽不到的字段写 null，**不许编**（目标价等任何数字也必须落在
    span 指向的原文片段内——由 span 校验兜底）。

span 语义：``span_start/span_end`` 是 **evidence 原文摘录在送审文本中的字符偏移**
（Python 切片约定，end 不含）。校验：0 <= start < end <= len(text) 且
``text[start:end]`` 与 ``evidence`` 归一化后一致（LLM 常微调空白，容忍空白差异）。
"""
from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# 送审文本超长截断（成本护栏；与 config INTEL_LLM_MAX_INPUT_CHARS 同步默认值）
DEFAULT_MAX_INPUT_CHARS = 8000


class Stance(str, Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class Horizon(str, Enum):
    SHORT = "short"      # 日-周
    MID = "mid"          # 月-季
    LONG = "long"        # 年以上
    EVENT = "event"      # 事件驱动，无固定 horizon


class MentionExtraction(BaseModel):
    """单篇文档对单标的的观点抽取（doc_mentions 行）

    注：不用 strict=True——LLM 返回 JSON 字符串，str-Enum 需要正常 coerce；
    防幻觉的关键是 extra="forbid"（多余字段=幻觉字段，直接拒）。
    """
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=8, max_length=12)     # 600519.SH 形态
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)
    horizon: Horizon
    thesis: str = Field(min_length=4, max_length=500)    # 一句话论点（LLM 推断）
    evidence: str = Field(min_length=2, max_length=300)  # 原文摘录（事实）
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)


class StyleExtraction(BaseModel):
    """单篇风格信号（doc_style 行）——六个维度强度 0-1"""
    model_config = ConfigDict(extra="forbid")

    style_dims: dict[str, float] = Field(default_factory=dict)

    _ALLOWED = {"value", "growth", "momentum", "contrarian", "event", "quality"}

    def normalized(self) -> dict[str, float]:
        """只保留白名单维度并裁剪到 [0,1]"""
        return {k: max(0.0, min(1.0, float(v)))
                for k, v in self.style_dims.items() if k in self._ALLOWED}


def _norm_ws(s: str) -> str:
    return "".join(s.split())


def validate_span(ext: MentionExtraction, text: str) -> tuple[bool, str, int | None, int | None]:
    """span 硬校验（防幻觉核心）。返回 (ok, 失败原因, 真实start, 真实end)。

    两层判定：
      ① LLM 给的 span 切片与 evidence 空白归一化后一致 → 直接用，偏移可信；
      ② 不一致时，在原文里空白归一化搜索 evidence；找到则用真实偏移还原
        （容忍 Qwen 类模型常见的字符偏移计算误差，只要 evidence 确实出自原文）。
    两层都失败（evidence 根本不在原文中）→ 幻觉，拒绝入库。
    """
    ev_norm = _norm_ws(ext.evidence)
    if not ev_norm:
        return False, "evidence 为空", None, None
    # ① 严格：LLM 给的 span 直接对上
    if 0 <= ext.span_start < ext.span_end <= len(text):
        if _norm_ws(text[ext.span_start:ext.span_end]) == ev_norm:
            return True, "", ext.span_start, ext.span_end
    # ② 宽松：在原文中搜索 evidence（空白归一化），找到则还原真实偏移
    tn = _norm_ws(text)
    idx = tn.find(ev_norm)
    if idx >= 0:
        return True, "", idx, idx + len(ev_norm)
    return False, "evidence 与原文不一致（疑似幻觉）", None, None


def parse_extraction(raw_json: str, text: str) -> tuple[list[MentionExtraction],
                                                         StyleExtraction | None,
                                                         str | None]:
    """解析 LLM 返回的 JSON。返回 (mentions, style, error)，mentions 为 MentionExtraction 列表。

    pydantic 校验失败 / JSON 非法 / span 校验失败 → (None, None, 原因)，调用方落 quarantine。
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        # 围栏/前后缀兜底：截取首个 { 到末个 }（reasoning 模型偶尔无视 json_object 约束包围栏）
        i, j = raw_json.find("{"), raw_json.rfind("}")
        if i < 0 or j <= i:
            return None, None, f"JSON 非法且无可提取对象: {raw_json[:120]}"
        try:
            data = json.loads(raw_json[i:j + 1])
        except json.JSONDecodeError as e:
            return None, None, f"JSON 非法: {e}"
    if not isinstance(data, dict):
        return None, None, "顶层必须是对象"

    mentions_data = data.get("mentions")
    if mentions_data is None:
        mentions_data = []
    if not isinstance(mentions_data, list):
        return None, None, "mentions 必须是数组"
    style_data = data.get("style") or {}
    mentions: list[MentionExtraction] = []
    try:
        for md in mentions_data:
            mentions.append(MentionExtraction.model_validate(md))
    except ValidationError as e:
        return None, None, f"mention 契约校验失败: {e.errors()[:3]}"
    try:
        style = StyleExtraction.model_validate({"style_dims": style_data})
    except ValidationError as e:
        return None, None, f"style 契约校验失败: {e.errors()[:3]}"

    for m in mentions:
        ok, why, s, e = validate_span(m, text)
        if not ok:
            return None, None, f"span 校验失败: {why}"
        m.span_start = s
        m.span_end = e
    return mentions, style, None
