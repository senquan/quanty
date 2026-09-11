"""P3-3 因子计算单测

覆盖：交易日映射（防前视核心）、滚动窗口、六因子计算语义、
作者信念度降级、以及 build_all_factor_rows 的可见性不变式。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.intel.factorize import factors as F

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai


def dt(d: date, hour: int = 10) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=TZ)


# 周一~周五（09-07 周一 ... 09-11 周五），09-05/06 为周末
CAL = [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)]


class TestTradeDateMapping:
    """防前视核心：available_at → trade_date"""

    def test_intraday_same_day(self):
        assert F.to_trade_date(dt(date(2026, 9, 3), 10), CAL) == date(2026, 9, 3)

    def test_after_close_pushes_next_trade_day(self):
        """15:00 后入库 → 当日不可见，推到下一交易日"""
        assert F.to_trade_date(dt(date(2026, 9, 3), 16), CAL) == date(2026, 9, 4)

    def test_weekend_pushes_to_monday(self):
        assert F.to_trade_date(dt(date(2026, 9, 5), 10), CAL) == date(2026, 9, 7)

    def test_beyond_calendar_returns_none(self):
        """日历末尾之后 → None，宁缺勿前视"""
        assert F.to_trade_date(dt(date(2026, 9, 9), 10), CAL) is None

    def test_none_available_at(self):
        assert F.to_trade_date(None, CAL) is None

    def test_empty_calendar(self):
        assert F.to_trade_date(dt(date(2026, 9, 3), 10), []) is None


class TestRollingWindow:
    def test_window_includes_trade_date(self):
        got = F.rolling_dates(CAL, date(2026, 9, 8), 3)
        assert got == [date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)]

    def test_window_clamped_at_calendar_start(self):
        got = F.rolling_dates(CAL, date(2026, 9, 3), 5)
        assert got == [date(2026, 9, 3)]  # 不足 5 天就给已有部分

    def test_unknown_date_returns_empty(self):
        assert F.rolling_dates(CAL, date(2026, 9, 5), 5) == []

    def test_next_trade_date_end_of_calendar(self):
        assert F.next_trade_date(CAL, date(2026, 9, 8)) is None


class TestExtendCalendar:
    def test_extends_skipping_weekends(self):
        """外推跳过周末，只加工作日"""
        cal = [date(2026, 9, 4)]  # 周五
        got = F.extend_calendar(cal, date(2026, 9, 8), max_extra=10)
        assert got == [
            date(2026, 9, 4),
            date(2026, 9, 7),  # 周一（跳过 09-05/06 周末）
            date(2026, 9, 8),
        ]

    def test_respects_max_extra(self):
        cal = [date(2026, 9, 4)]
        got = F.extend_calendar(cal, date(2026, 12, 31), max_extra=2)
        assert len(got) == 3  # 原 1 + 最多 2

    def test_noop_when_already_covered(self):
        cal = list(CAL)
        assert F.extend_calendar(cal, date(2026, 9, 3), max_extra=10) == cal


def _m(symbol, d, stance="neutral", conf=0.8, doc=1, src="s1", author=None, hour=10):
    return {
        "symbol": symbol,
        "published_at": dt(d, hour),
        "ingested_at": dt(d, hour),
        "stance": stance,
        "confidence": conf,
        "doc_id": doc,
        "source_name": src,
        "author": author,
    }


def _index(rows, cal=CAL):
    return F._index_by_symbol_date(F.build_mention_index(rows, cal))


class TestMentionHeat:
    def test_counts_distinct_docs(self):
        """同一文档重复提及同一标的只算一次"""
        rows = [
            _m("600519.SH", date(2026, 9, 7), doc=1),
            _m("600519.SH", date(2026, 9, 7), doc=1),  # 重复
            _m("600519.SH", date(2026, 9, 8), doc=2),
        ]
        idx = _index(rows)
        d = F.rolling_dates(CAL, date(2026, 9, 8), 5)
        assert F.compute_mention_heat(idx, "600519.SH", d) == 2.0

    def test_zero_when_no_mention(self):
        idx = _index([_m("600519.SH", date(2026, 9, 7))])
        d = F.rolling_dates(CAL, date(2026, 9, 8), 5)
        assert F.compute_mention_heat(idx, "000001.SZ", d) == 0.0


class TestSentiment:
    def test_net_sentiment(self):
        rows = [
            _m("600519.SH", date(2026, 9, 7), stance="bullish"),
            _m("600519.SH", date(2026, 9, 8), stance="bearish"),
            _m("600519.SH", date(2026, 9, 8), stance="neutral", doc=2),
        ]
        idx = _index(rows)
        d = F.rolling_dates(CAL, date(2026, 9, 8), 10)
        # (1 + -1 + 0) / 3 = 0
        assert F.compute_sentiment(idx, "600519.SH", d) == pytest.approx(0.0)

    def test_all_bullish_is_one(self):
        rows = [_m("600519.SH", date(2026, 9, 7), stance="bullish", doc=i) for i in (1, 2)]
        idx = _index(rows)
        d = F.rolling_dates(CAL, date(2026, 9, 7), 10)
        assert F.compute_sentiment(idx, "600519.SH", d) == pytest.approx(1.0)

    def test_none_when_window_empty(self):
        """窗口无提及 → None（不写行，不伪造中性）"""
        idx = _index([_m("600519.SH", date(2026, 9, 7))])
        d = F.rolling_dates(CAL, date(2026, 9, 7), 10)
        assert F.compute_sentiment(idx, "000001.SZ", d) is None


class TestResonance:
    def test_counts_distinct_sources(self):
        rows = [
            _m("600519.SH", date(2026, 9, 7), src="东方财富", doc=1),
            _m("600519.SH", date(2026, 9, 7), src="华尔街见闻", doc=2),
            _m("600519.SH", date(2026, 9, 8), src="东方财富", doc=3),  # 重复源
        ]
        idx = _index(rows)
        d = F.rolling_dates(CAL, date(2026, 9, 8), 5)
        assert F.compute_resonance(idx, "600519.SH", d) == 2.0


class TestFirstMention:
    def test_first_day_is_one(self):
        rows = [
            _m("600519.SH", date(2026, 9, 7), doc=1),
            _m("600519.SH", date(2026, 9, 8), doc=2),
        ]
        idx = _index(rows)
        assert F.compute_first_mention(idx, CAL, "600519.SH", date(2026, 9, 7)) == 1.0
        assert F.compute_first_mention(idx, CAL, "600519.SH", date(2026, 9, 8)) == 0.0

    def test_unknown_date_is_zero(self):
        idx = _index([_m("600519.SH", date(2026, 9, 7))])
        assert F.compute_first_mention(idx, CAL, "600519.SH", date(2026, 9, 5)) == 0.0


class TestAuthorConviction:
    def test_uses_author_accuracy_when_sample_sufficient(self):
        profiles = {"老张": {"top_symbols": set(), "accuracy": 0.75, "sample": 30}}
        rows = [_m("600519.SH", date(2026, 9, 7), conf=0.2, author="老张")]
        assert F.compute_author_conviction(rows, profiles) == pytest.approx(0.75)

    def test_degrades_to_confidence_when_sample_insufficient(self):
        """准确度样本 <10 → 诚实降级为 LLM 自评置信度（不是伪造准确度）"""
        profiles = {"老张": {"top_symbols": set(), "accuracy": 0.75, "sample": 3}}
        rows = [_m("600519.SH", date(2026, 9, 7), conf=0.4, author="老张")]
        assert F.compute_author_conviction(rows, profiles) == pytest.approx(0.4)

    def test_falls_back_to_source_profile(self):
        """author 为 null → 用来源画像"""
        profiles = {"东方财富": {"top_symbols": set(), "accuracy": 0.6, "sample": 50}}
        rows = [_m("600519.SH", date(2026, 9, 7), conf=0.1, src="东方财富")]
        assert F.compute_author_conviction(rows, profiles) == pytest.approx(0.6)

    def test_unknown_profile_uses_confidence(self):
        rows = [_m("600519.SH", date(2026, 9, 7), conf=0.9, author="查无此人")]
        assert F.compute_author_conviction(rows, {}) == pytest.approx(0.9)

    def test_empty_rows(self):
        assert F.compute_author_conviction([], {}) == 0.0


class TestStyleMatch:
    def test_hit_when_symbol_in_top_symbols(self):
        profiles = {"老张": {"top_symbols": {"600519.SH"}, "accuracy": None, "sample": 0}}
        rows = [_m("600519.SH", date(2026, 9, 7), author="老张")]
        assert F.compute_style_match(rows, profiles) == 1.0

    def test_miss_otherwise(self):
        profiles = {"老张": {"top_symbols": {"000001.SZ"}, "accuracy": None, "sample": 0}}
        rows = [_m("600519.SH", date(2026, 9, 7), author="老张")]
        assert F.compute_style_match(rows, profiles) == 0.0

    def test_partial_hit(self):
        profiles = {"老张": {"top_symbols": {"600519.SH"}, "accuracy": None, "sample": 0}}
        rows = [
            _m("600519.SH", date(2026, 9, 7), doc=1, author="老张"),
            _m("600519.SH", date(2026, 9, 7), doc=2, author="老李"),
        ]
        assert F.compute_style_match(rows, profiles) == pytest.approx(0.5)


class TestBuildAllFactorRows:
    def test_emits_six_factors_per_combo(self):
        rows = [
            _m("600519.SH", date(2026, 9, 7), stance="bullish", doc=1),
            _m("000001.SZ", date(2026, 9, 7), stance="neutral", doc=2),
        ]
        out = F.build_all_factor_rows(rows, CAL, {})
        codes = {r["factor_code"] for r in out}
        assert codes == {
            "INTL_MENTION_HEAT_5",
            "INTL_SENTIMENT_10",
            "INTL_RESONANCE_5",
            "INTL_FIRST_MENTION",
            "INTL_AUTHOR_CONVICTION",
            "INTL_STYLE_MATCH",
        }
        # 2 个 (symbol, trade_date) 组合 × 6 因子
        assert len(out) == 12

    def test_no_lookahead_available_at_not_after_trade_date(self):
        """不变式：任何行的 available_at 日期不得晚于其 trade_date"""
        rows = [
            _m("600519.SH", date(2026, 9, 3), hour=16, doc=1),  # 收盘后 → 09-04
            _m("000001.SZ", date(2026, 9, 7), hour=10, doc=2),
        ]
        out = F.build_all_factor_rows(rows, CAL, {})
        assert out
        for r in out:
            assert r["available_at"].date() <= r["trade_date"]

    def test_after_close_mention_not_visible_same_day(self):
        """09-03 收盘后入库 → trade_date 必须是 09-04，不能是 09-03"""
        rows = [_m("600519.SH", date(2026, 9, 3), hour=16, doc=1)]
        out = F.build_all_factor_rows(rows, CAL, {})
        assert {r["trade_date"] for r in out} == {date(2026, 9, 4)}

    def test_drops_mentions_outside_calendar(self):
        """超出日历范围的 mention 被丢弃，不产生因子行"""
        rows = [_m("600519.SH", date(2026, 9, 20), doc=1)]
        assert F.build_all_factor_rows(rows, CAL, {}) == []

    def test_versioned_fields_present(self):
        rows = [_m("600519.SH", date(2026, 9, 7), doc=1)]
        out = F.build_all_factor_rows(rows, CAL, {}, factor_version="v9", prompt_version="v3")
        assert all(r["factor_version"] == "v9" for r in out)
        assert all(r["prompt_version"] == "v3" for r in out)
