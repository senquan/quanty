"""资讯抽取分页：筛选条件拼接与分页常量

分页 SQL 走真实 Postgres，不适合塞进单测；但**筛选条件的拼接**是最容易漂的
地方（列表与 count 两份 SQL 必须完全一致，否则会出现"总数 87 却只能翻 3 页"）。
这里断言 where 与 params 的对应关系，以及 20 行/页的默认值确实生效。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.api_v1.endpoints.intel import (  # noqa: E402
    MENTION_DEFAULT_PAGE_SIZE,
    MENTION_MAX_PAGE_SIZE,
    _mention_where,
)


def test_no_filter_only_pins_prompt_version():
    where, params = _mention_where("", "all", "all")
    assert params == {"pv": "v2"}
    assert "prompt_version = :pv" in where
    assert "ILIKE" not in where


def test_keyword_adds_like_param():
    where, params = _mention_where("茅台", "all", "all")
    assert params["like"] == "%茅台%"
    # 标题 / 论点 / 证据 / 标的 四个字段都要能搜到
    for col in ("d.title", "m.thesis", "m.evidence", "m.symbol"):
        assert col in where


def test_stance_and_source_filters():
    where, params = _mention_where("", "bullish", "华尔街见闻")
    assert params["stance"] == "bullish"
    assert params["source"] == "华尔街见闻"
    assert "m.stance = :stance" in where
    assert "s.name = :source" in where


def test_all_means_no_filter():
    """'all' 是前端下拉的哨兵值，不能真的拿去当过滤条件"""
    where, params = _mention_where("", "all", "all")
    assert "stance" not in params and "source" not in params
    assert ":stance" not in where and ":source" not in where


def test_page_size_defaults_and_ceiling():
    """需求：20 行一页；上限防止前端一次要 10 万行把库拖垮"""
    assert MENTION_DEFAULT_PAGE_SIZE == 20
    assert MENTION_MAX_PAGE_SIZE == 100

    # 端点里的夹紧逻辑与此同式，同步断言一次防止改漏
    def clamp(v: int) -> int:
        return max(1, min(v, MENTION_MAX_PAGE_SIZE))

    assert clamp(0) == 1
    assert clamp(20) == 20
    assert clamp(100_000) == 100
