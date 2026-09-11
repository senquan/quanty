"""预算闸单测（P1-3）：超限必须抛 BudgetExceeded（停批告警，不静默降级）"""
import pytest

from app.intel.understand.llm.client import BudgetExceeded, BudgetGate


class FakeStore:
    def __init__(self, spent: float):
        self.spent = spent
        self.calls = 0

    def llm_spent_today_cny(self) -> float:
        self.calls += 1
        return self.spent


class TestBudgetGate:
    def test_under_budget_passes(self):
        gate = BudgetGate(store_mod=FakeStore(3.3))
        gate.check()  # 不抛即过

    def test_over_budget_raises(self):
        gate = BudgetGate(store_mod=FakeStore(10.01))
        with pytest.raises(BudgetExceeded):
            gate.check()

    def test_exact_budget_raises(self):
        gate = BudgetGate(store_mod=FakeStore(10.0))
        with pytest.raises(BudgetExceeded):
            gate.check()

    def test_zero_spent_passes(self):
        gate = BudgetGate(store_mod=FakeStore(0.0))
        gate.check()
