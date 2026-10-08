"""
Tests for rule_trial_balance_reconciliation.py.

Includes stress-test injections against the REAL trial balance + ledger
data (same approach as test_rule_stress_injection.py's Rules 2/4/5, and
test_rule_gstr_reconciliation.py's GSTR stress tests): the real data
reconciles cleanly (0 mismatches, see test_real_data_reconciles_cleanly
below), so there's no real anomaly to detect, and this rule's severity
classification has to be proven with synthetic, answer-keyed mismatches
injected into a copy of the real data instead.
"""
import pytest

from rule_trial_balance_reconciliation import (
    check_trial_balance_reconciliation,
    classify_severity,
)
from reconcile_trial_balance import parse_trial_balance, GL_FILE
import csv


@pytest.mark.parametrize("mismatch,reference,expected_severity", [
    (5000, 10000, "CRITICAL"),   # 50% gap
    (3000, 10000, "CRITICAL"),   # exactly 30% -- boundary
    (2999, 10000, "HIGH"),       # just under 30%
    (1000, 10000, "HIGH"),       # exactly 10% -- boundary
    (999, 10000, "MEDIUM"),      # just under 10%
    (1, 10000, "MEDIUM"),        # tiny but nonzero gap
    (0, 10000, "NONE"),          # no gap
    (0, 0, "NONE"),              # genuine zero/zero
    (500, 0, "CRITICAL"),        # reference = 0 but a real mismatch exists
])
def test_classify_severity_boundaries(mismatch, reference, expected_severity):
    assert classify_severity(mismatch, reference) == expected_severity


def _tb_row(account, debit=0.0, credit=0.0, opening=0.0, closing=0.0, page=1):
    return {
        "account": account, "page": page,
        "opening": opening, "opening_sign": None,
        "debit": debit, "credit": credit,
        "closing": closing, "closing_sign": None,
    }


def _gl_row(account, amount, entry_type):
    return {"account": account, "amount": str(amount), "entry_type": entry_type}


def test_clean_account_produces_no_flags():
    tb_rows = [_tb_row("Cash", debit=50000.0, credit=30000.0)]
    gl_rows = [_gl_row("Cash", 50000.0, "Dr"), _gl_row("Cash", 30000.0, "Cr")]
    assert check_trial_balance_reconciliation(tb_rows, gl_rows) == []


def test_debit_mismatch_flagged():
    tb_rows = [_tb_row("Cash", debit=50000.0, credit=30000.0)]
    gl_rows = [_gl_row("Cash", 80000.0, "Dr"), _gl_row("Cash", 30000.0, "Cr")]
    flags = check_trial_balance_reconciliation(tb_rows, gl_rows)
    debit_flags = [f for f in flags if f["side"] == "debit"]
    assert len(debit_flags) == 1
    # mismatch 30000 / reference(TB)=50000 = 60% -> CRITICAL
    assert debit_flags[0]["severity"] == "CRITICAL"
    assert debit_flags[0]["account"] == "Cash"


def test_credit_mismatch_flagged_independently_of_debit():
    tb_rows = [_tb_row("Bank", debit=100000.0, credit=100000.0)]
    gl_rows = [_gl_row("Bank", 100000.0, "Dr"), _gl_row("Bank", 111000.0, "Cr")]
    flags = check_trial_balance_reconciliation(tb_rows, gl_rows)
    assert len(flags) == 1
    assert flags[0]["side"] == "credit"
    # mismatch 11000 / reference 100000 = 11% -> HIGH
    assert flags[0]["severity"] == "HIGH"


def test_unmatched_account_not_flagged():
    """A trial-balance row with no corresponding GL account at all (Tally
    group header, zero-activity fixed asset, or a redacted partner capital
    account) is not a reconciliation flag -- reconcile_trial_balance.py's
    own investigation already established these are expected structural
    rows, not missing accounts."""
    tb_rows = [_tb_row("Fixed Assets (Group)", debit=0.0, credit=0.0)]
    gl_rows = [_gl_row("Cash", 1000.0, "Dr")]
    assert check_trial_balance_reconciliation(tb_rows, gl_rows) == []


def test_account_alias_group_reconciles_when_summed():
    """Purchase / Purchase Account is one of the established alias groups
    (continuation-page name fragmentation, not a real discrepancy) --
    summing both variants should reconcile cleanly, same as the real data."""
    tb_rows = [_tb_row("Purchase", debit=150000.0, credit=0.0)]
    gl_rows = [
        _gl_row("Purchase", 50000.0, "Dr"),
        _gl_row("Purchase Account", 100000.0, "Dr"),
    ]
    assert check_trial_balance_reconciliation(tb_rows, gl_rows) == []


# ---------- Stress-test injections against the REAL extracted data ----------

@pytest.fixture(scope="module")
def real_tb_rows():
    return parse_trial_balance()


@pytest.fixture(scope="module")
def real_gl_rows():
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_real_data_reconciles_cleanly(real_tb_rows, real_gl_rows):
    """The real baseline this rule runs against today: zero flags across
    all 82 matched trial-balance accounts. This is what makes the injected
    tests below meaningful -- see reconcile_trial_balance.py's 2026-10-02
    docstring update for how this was re-verified."""
    flags = check_trial_balance_reconciliation(real_tb_rows, real_gl_rows)
    assert flags == []


def test_stress_injection_inflated_sales_debit(real_tb_rows, real_gl_rows):
    """Answer key: inject one extra Rs.5,00,000 Dr leg under 'Sales' into a
    COPY of the real ledger, on top of its existing real debit activity
    (Rs.15,13,102.54 -- sales-return/adjustment legs, already reconciling
    exactly against the trial balance before injection). Real TB figures
    are untouched, so the injected amount alone becomes the gap -- this
    must surface as an exactly Rs.5,00,000 debit-side mismatch on Sales."""
    injected_gl = list(real_gl_rows) + [_gl_row("Sales", 500000.0, "Dr")]
    flags = check_trial_balance_reconciliation(real_tb_rows, injected_gl)
    sales_debit_flags = [f for f in flags if f["account"] == "Sales" and f["side"] == "debit"]
    assert len(sales_debit_flags) == 1
    assert sales_debit_flags[0]["mismatch_amount"] == pytest.approx(500000.0, abs=0.01)
    # Only Sales should be affected -- every other real account is untouched.
    assert {f["account"] for f in flags} == {"Sales"}


def test_stress_injection_removed_cash_credit_legs(real_tb_rows, real_gl_rows):
    """Answer key: strip every real 'Cash' Cr leg out of a copy of the
    ledger. The trial balance's own Cash credit figure is untouched, so
    the full real credit total becomes the entire gap -- guaranteed far
    above the Rs.1 tolerance and a large percentage gap -> CRITICAL."""
    injected_gl = [r for r in real_gl_rows
                   if not (r["account"] == "Cash" and r["entry_type"] == "Cr")]
    flags = check_trial_balance_reconciliation(real_tb_rows, injected_gl)
    cash_credit_flags = [f for f in flags if f["account"] == "Cash" and f["side"] == "credit"]
    assert len(cash_credit_flags) == 1
    assert cash_credit_flags[0]["severity"] == "CRITICAL"
    assert cash_credit_flags[0]["gl_amount"] == pytest.approx(0.0, abs=0.01)
