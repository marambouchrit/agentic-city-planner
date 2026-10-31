"""Tests of calculate_budget. No network is used."""

import pytest
from pydantic import ValidationError

from city_planner.schemas import CostItem
from city_planner.tools.calculate_budget import calculate_budget


def test_within_budget():
    result = calculate_budget([CostItem(name="A", amount=20.1), CostItem(name="B", amount=30.2)], 60)
    assert result.total == 50.3
    assert result.over_budget is False
    assert result.difference == 9.7


def test_over_budget_has_negative_difference():
    result = calculate_budget([CostItem(name="A", amount=80)], 60)
    assert result.over_budget is True and result.difference == -20


def test_exactly_on_budget_is_not_over():
    assert calculate_budget([CostItem(name="A", amount=60)], 60).over_budget is False


def test_missing_cost_is_not_counted_as_zero_and_answer_is_unknown():
    result = calculate_budget([CostItem(name="A", amount=20), CostItem(name="B")], 60)
    assert result.total == 20
    assert result.over_budget is None
    assert result.missing_costs == ["B"]
    assert "leaves out 1" in result.note


def test_missing_cost_when_already_over_is_still_over():
    assert calculate_budget([CostItem(name="A", amount=80), CostItem(name="B")], 60).over_budget is True


def test_estimates_are_labelled():
    assert calculate_budget([CostItem(name="A", amount=5)], 10).includes_estimates is True
    known = calculate_budget([CostItem(name="A", amount=5, is_estimate=False)], 10)
    assert known.includes_estimates is False and known.note == "All costs are known."


def test_no_budget_gives_total_but_no_verdict():
    result = calculate_budget([CostItem(name="A", amount=5)])
    assert result.total == 5 and result.over_budget is None and result.difference is None


def test_empty_list():
    assert calculate_budget([], 10).total == 0


@pytest.mark.parametrize("amount", [-1, float("inf"), float("nan")])
def test_invalid_amounts_are_rejected(amount):
    with pytest.raises(ValidationError):
        CostItem(name="A", amount=amount)


def test_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        calculate_budget([], -5)
