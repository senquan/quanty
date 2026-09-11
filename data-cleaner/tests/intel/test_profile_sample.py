"""P2-5 样本量红线单测（验收清单要求：样本不足逻辑有单测）

直接测纯函数 is_sample_sufficient，覆盖边界：
- mentions 与 accuracy 样本任一 < 阈值 → 样本不足
- 两者均 ≥ 阈值 → 充足
- 恰好等于阈值 → 充足
- 自定义阈值
"""
import pytest

from app.intel.aggregate.profile import is_sample_sufficient


def test_sufficient_when_both_meet_threshold():
    assert is_sample_sufficient(30, 30) is True
    assert is_sample_sufficient(50, 31) is True


def test_insufficient_when_mentions_below():
    # mentions 不足，accuracy 充足 → 仍不足
    assert is_sample_sufficient(29, 100) is False


def test_insufficient_when_accuracy_below():
    # mentions 充足，accuracy 不足 → 仍不足
    assert is_sample_sufficient(100, 29) is False


def test_insufficient_when_both_below():
    assert is_sample_sufficient(5, 5) is False


def test_exactly_at_threshold_is_sufficient():
    # 阈值 30，恰好 30 视为达标
    assert is_sample_sufficient(30, 30) is True


def test_custom_threshold():
    assert is_sample_sufficient(10, 10, threshold=10) is True
    assert is_sample_sufficient(9, 10, threshold=10) is False


@pytest.mark.parametrize(
    "mentions,acc,expected",
    [
        (0, 0, False),   # 双零
        (30, 0, False),  # 有提及但无行情样本（初期真实状态）
        (339, 0, False), # 东方财富股票初期：提及多但 accuracy 未解锁
        (30, 30, True),
    ],
)
def test_redline_matrix(mentions, acc, expected):
    assert is_sample_sufficient(mentions, acc) is expected
