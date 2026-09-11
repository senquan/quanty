"""schema 契约 + span 硬校验单测（P1-4）"""
import json

from app.intel.understand.schema import (
    MentionExtraction,
    Stance,
    _norm_ws,
    parse_extraction,
    validate_span,
)

TEXT = "贵州茅台发布三季报，营业收入同比增长15%，机构认为高端白酒需求稳健，维持买入评级。"
# evidence 取原文中"机构认为高端白酒需求稳健，维持买入评级"片段
EV_START = TEXT.index("机构认为")
EV_END = EV_START + len("机构认为高端白酒需求稳健，维持买入评级")


def _good_mention(**over) -> dict:
    base = {
        "symbol": "600519.SH",
        "stance": "bullish",
        "confidence": 0.8,
        "horizon": "mid",
        "thesis": "高端白酒需求稳健，估值有支撑",
        "evidence": TEXT[EV_START:EV_END],
        "span_start": EV_START,
        "span_end": EV_END,
    }
    base.update(over)
    return base


class TestSpanValidation:
    def test_valid_span(self):
        m = MentionExtraction.model_validate(_good_mention())
        ok, why, s, e = validate_span(m, TEXT)
        assert ok, why
        assert TEXT[s:e] == TEXT[EV_START:EV_END]

    def test_whitespace_tolerant(self):
        """evidence 微调空白（全角空格/换行）可容忍"""
        m = MentionExtraction.model_validate(
            _good_mention(evidence=TEXT[EV_START:EV_END].replace("，", "，\n")))
        ok, why, s, e = validate_span(m, TEXT)
        assert ok, why

    def test_span_fixed_by_search(self):
        """LLM 偏移算错但 evidence 确实出自原文 → 搜索还原真实偏移"""
        m = MentionExtraction.model_validate(_good_mention(span_start=0, span_end=10))
        ok, why, s, e = validate_span(m, TEXT)
        assert ok, why
        assert _norm_ws(TEXT[s:e]) == _norm_ws(m.evidence)

    def test_fabricated_evidence_rejected(self):
        """evidence 原文不存在 → 幻觉，拒"""
        m = MentionExtraction.model_validate(
            _good_mention(evidence="目标价上调至3000元", span_start=EV_START, span_end=EV_END))
        ok, why, s, e = validate_span(m, TEXT)
        assert not ok and "不一致" in why


class TestParseExtraction:
    def test_valid_json(self):
        raw = json.dumps({"mentions": [_good_mention()],
                          "style": {"growth": 0.7, "quality": 0.5, "hype": 0.9}})
        mentions, s, err = parse_extraction(raw, TEXT)
        assert err is None and mentions and mentions[0].stance == Stance.BULLISH
        assert s is not None
        assert "hype" not in s.normalized() and s.normalized()["growth"] == 0.7

    def test_no_mention_ok(self):
        raw = json.dumps({"mentions": None, "style": {}})
        mentions, s, err = parse_extraction(raw, TEXT)
        assert err is None and mentions == []

    def test_invalid_json(self):
        mentions, s, err = parse_extraction("not json{", TEXT)
        assert mentions in (None, []) and err and "JSON" in err

    def test_extra_field_forbidden(self):
        bad = _good_mention()
        bad["hallucinated_price"] = 3000  # extra="forbid" 应拒
        raw = json.dumps({"mentions": [bad], "style": {}})
        mentions, s, err = parse_extraction(raw, TEXT)
        assert mentions in (None, []) and err and "契约校验失败" in err

    def test_bad_span_goes_error(self):
        # evidence 根本不在原文（即便 span 看似合理）→ 幻觉，拒
        raw = json.dumps({"mentions": [_good_mention(
            evidence="原文中不存在的虚构内容XYZ", span_start=EV_START, span_end=EV_END)],
            "style": {}})
        mentions, s, err = parse_extraction(raw, TEXT)
        assert mentions in (None, []) and err and "span" in err
