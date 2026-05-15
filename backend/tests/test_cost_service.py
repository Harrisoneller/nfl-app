"""Tests for the cost ledger + budget gate."""
import pytest

from app.services.cost_service import BudgetExceeded, CostLedger, check_budget, ledger


def test_record_increments_totals():
    fresh = CostLedger()
    fresh.record("u1", input_tokens=100, output_tokens=200)
    row = fresh.get_user_today("u1")
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 200
    assert row["calls"] == 1
    g = fresh.get_global_today()
    assert g["input_tokens"] == 100


def test_budget_gate_allows_under_limit():
    # Brand-new user, should not raise
    check_budget("brand-new-user-xyz")


def test_budget_gate_rejects_over_limit():
    # Stuff the ledger with enough output tokens to blow the per-user budget,
    # then check that the next call is rejected.
    # Conservative settings: per-user $1/day, $0.01/1k output → 100k tokens = $1.
    ledger.record("rich-spender", input_tokens=0, output_tokens=200_000)
    with pytest.raises(BudgetExceeded):
        check_budget("rich-spender")


def test_summary_shape():
    summary = ledger.summary()
    assert "date" in summary
    assert "global" in summary
    assert "top_users" in summary
    assert "cost_usd" in summary["global"]
