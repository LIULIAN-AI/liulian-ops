from __future__ import annotations

from decimal import Decimal

import pytest

from crawler.harness.budget import Budget, BudgetExceededError, BudgetLimits


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


def test_budget_records_usage_across_dimensions() -> None:
    clock = FakeClock()
    budget = Budget(
        BudgetLimits(wall_seconds=10, tokens=100, usd=Decimal("1.50"), turns=3),
        clock=clock,
    )

    budget.record(tokens=25, usd=Decimal("0.25"), turns=1)
    clock.now = 12.5

    usage = budget.check()
    assert usage.wall_seconds == 2.5
    assert usage.tokens == 25
    assert usage.usd == Decimal("0.25")
    assert usage.turns == 1


def test_budget_raises_clear_exception_when_tokens_exceed_limit() -> None:
    budget = Budget(BudgetLimits(tokens=10))

    with pytest.raises(BudgetExceededError) as exc:
        budget.record(tokens=11)

    assert exc.value.dimension == "tokens"
    assert "Budget exceeded for tokens" in str(exc.value)


def test_budget_raises_clear_exception_when_usd_exceeds_limit() -> None:
    budget = Budget(BudgetLimits(usd=Decimal("0.10")))

    with pytest.raises(BudgetExceededError) as exc:
        budget.record(usd=Decimal("0.11"))

    assert exc.value.dimension == "usd"
    assert "Budget exceeded for usd" in str(exc.value)


def test_budget_raises_clear_exception_when_turns_exceed_limit() -> None:
    budget = Budget(BudgetLimits(turns=1))

    with pytest.raises(BudgetExceededError) as exc:
        budget.record(turns=2)

    assert exc.value.dimension == "turns"
    assert "Budget exceeded for turns" in str(exc.value)


def test_budget_checks_wall_time_limit() -> None:
    clock = FakeClock()
    budget = Budget(BudgetLimits(wall_seconds=1), clock=clock)

    clock.now = 11.01

    with pytest.raises(BudgetExceededError) as exc:
        budget.check()

    assert exc.value.dimension == "wall_seconds"


def test_budget_rejects_negative_accounting() -> None:
    budget = Budget(BudgetLimits())

    with pytest.raises(ValueError, match="tokens"):
        budget.record_tokens(-1)
