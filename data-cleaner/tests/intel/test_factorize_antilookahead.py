"""P3-2 防前视单测（验收硬项：先于因子实现写）

覆盖 plan §5 P3-2 两条要求：
1. 构造 published_at 早于 ingested_at 的文档，断言 ingested_at 前无因子值；
2. LLM 重跑（新 prompt_version）不得改变旧版本因子值的回测可见性。

第 1 条为纯函数单测（不依赖 DB）；第 2 条为 DB 集成测试，
使用专属 symbol 并在 finally 中清理，不污染真实因子数据。
"""
from datetime import date, datetime, timezone

from sqlalchemy import text

from app.intel.factorize.availability import (
    build_factor_rows,
    compute_available_at,
    fetch_visible_factor_values,
    filter_visible,
    is_visible_at,
    upsert_factor_values,
)
from app.intel.store import get_engine

# ---- 场景：文档 09-01 发布，09-03 才入库（历史补录 / 导入的典型形态）----
PUB = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
ING = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)


def _dt(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


# ============ 1. available_at 计算（纯函数）============


def test_available_at_equals_ingested_when_published_earlier():
    # 发布早于入库 → 以入库为准
    assert compute_available_at(PUB, ING) == ING


def test_available_at_equals_published_when_ingested_earlier():
    # 入库早于发布（时钟异常 / 预发布）→ 取较晚的发布时间
    assert compute_available_at(ING, PUB) == ING


def test_available_at_when_equal():
    assert compute_available_at(PUB, PUB) == PUB


def test_available_at_none_handling():
    assert compute_available_at(None, ING) == ING
    assert compute_available_at(PUB, None) == PUB
    assert compute_available_at(None, None) is None


def test_available_at_accepts_iso_string():
    assert compute_available_at("2026-09-01T10:00:00+00:00", ING) == ING


# ============ 2. 防前视可见性（核心验收）============


def test_no_factor_value_before_ingested_at():
    """构造 published_at 早于 ingested_at 的文档：ingested_at 前无因子值。"""
    row = build_factor_rows(
        "600519.SH", date(2026, 9, 3), "INTL_MENTION_HEAT_5", 3.0, PUB, ING
    )
    assert row["available_at"] == ING

    # 发布后、入库前：不可见
    assert is_visible_at(row["available_at"], _dt(2026, 9, 1, 11)) is False
    assert is_visible_at(row["available_at"], _dt(2026, 9, 2, 0)) is False
    # 恰好入库时刻：可见（<= 语义）
    assert is_visible_at(row["available_at"], ING) is True
    # 入库后：可见
    assert is_visible_at(row["available_at"], _dt(2026, 9, 3, 16)) is True


def test_filter_visible_cuts_lookahead_rows():
    rows = [
        build_factor_rows("600519.SH", date(2026, 9, 3), "F", 1.0, PUB, ING),
        build_factor_rows("000001.SZ", date(2026, 9, 3), "F", 2.0, PUB, _dt(2026, 9, 10, 8)),
    ]
    assert len(filter_visible(rows, _dt(2026, 9, 2))) == 0
    assert len(filter_visible(rows, _dt(2026, 9, 4))) == 1
    assert len(filter_visible(rows, _dt(2026, 9, 11))) == 2


def test_build_row_skipped_when_no_timestamps():
    # 两个时间戳都缺失 → 宁可缺值，不可引入前视
    assert build_factor_rows("600519.SH", date(2026, 9, 3), "F", 1.0, None, None) is None


def test_is_visible_at_none_is_false():
    assert is_visible_at(None, _dt(2026, 9, 3)) is False


# ============ 3. LLM 重跑不改旧版本可见性（DB 集成）============

TEST_SYMBOL = "TEST_ANTILOOKAHEAD"


def _cleanup(eng):
    with eng.begin() as c:
        c.execute(
            text("DELETE FROM intel.factor_values WHERE symbol = :s"),
            {"s": TEST_SYMBOL},
        )


def test_llm_rerun_does_not_change_old_version_visibility():
    """LLM 重跑产生新 prompt_version 后，旧版本因子值的回测可见性保持不变。"""
    eng = get_engine()
    code = "INTL_MENTION_HEAT_5"
    trade_date = date(2026, 1, 2)
    pub = _dt(2026, 1, 1, 9)
    ing_v2 = _dt(2026, 1, 2, 8)       # v2 入库
    ing_v3 = _dt(2026, 1, 5, 8)       # 重跑后 v3 入库更晚
    as_of = _dt(2026, 1, 2, 9)        # 观察时刻：v2 已可见、v3 尚未可见

    try:
        _cleanup(eng)

        # 首次产出 v2
        upsert_factor_values(
            [build_factor_rows(TEST_SYMBOL, trade_date, code, 3.0, pub, ing_v2,
                               prompt_version="v2")],
            engine=eng,
        )
        before = fetch_visible_factor_values(code, as_of, prompt_version="v2", engine=eng)
        assert len(before) == 1
        assert before[0]["available_at"] == ing_v2
        assert before[0]["value"] == 3.0

        # LLM 重跑 → 新版本 v3（入库更晚、值不同）
        upsert_factor_values(
            [build_factor_rows(TEST_SYMBOL, trade_date, code, 9.0, pub, ing_v3,
                               prompt_version="v3")],
            engine=eng,
        )

        # 旧版本可见性不受影响
        after = fetch_visible_factor_values(code, as_of, prompt_version="v2", engine=eng)
        assert after == before, "LLM 重跑不得改变旧版本因子值的可见性"

        # 新版本在其 available_at 之前不可见
        v3_at_as_of = fetch_visible_factor_values(code, as_of, prompt_version="v3", engine=eng)
        assert v3_at_as_of == [], "新版本在入库前不得可见"

        # 新版本在其 available_at 之后可见，且与旧版本并存（版本化不覆盖）
        v3_later = fetch_visible_factor_values(code, _dt(2026, 1, 5, 9),
                                               prompt_version="v3", engine=eng)
        assert len(v3_later) == 1
        assert v3_later[0]["value"] == 9.0
    finally:
        _cleanup(eng)
