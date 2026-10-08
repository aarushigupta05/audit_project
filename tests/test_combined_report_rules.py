"""
combined_report.py keeps its own inline copies of Rules 1, 2, 3, 4 and 5
rather than importing the standalone rule_*.py modules (that's an existing
design choice, not something this suite changes). That means there are two
independent implementations of the same rules in this codebase, and
nothing stops them from silently drifting apart as one gets edited and the
other doesn't. These tests catch that drift by running both implementations
against the SAME fixture and asserting they agree on which rows they flag
(and, for Rules 3/4, on the actual values/severities computed).

Rules 3 and 4 were added here 2026-10-02: Rule 4 is exactly this kind of
drift, already happened and already found (see
test_rule4_zero_claimed_severity_discrepancy_between_implementations in
test_rule_stress_injection.py for the full history of that one) -- it went
unnoticed for as long as it did precisely because this suite never covered
Rule 4. Rule 3 gained a second duplicated implementation (compute_mad/
classify_mad_conformity) in the same round of work, so it's covered from
the start rather than waiting for its own drift to be discovered later.

They also cover the severity-tier helpers that only exist in
combined_report.py (get_severity_by_amount, escalate_severity, max_severity).
"""
import pytest

import combined_report as cr
from rule_segregation_of_duties import check_segregation_of_duties
from rule_round_number import check_round_number_bias
from rule_duplicate_transaction import check_duplicate_transactions
from rule_benfords_law import check_benfords_law, compute_mad, classify_mad_conformity
from rule_itr_26as_reconciliation import check_reconciliation


def test_rule1_parity_with_standalone_module(gl_rows):
    standalone_flagged = {r["entry_id"] for r in check_segregation_of_duties(gl_rows)}
    inline_flagged = {row["entry_id"] for row in gl_rows if cr.rule_segregation_of_duties(row)}
    assert inline_flagged == standalone_flagged


def test_rule2_parity_with_standalone_module(gl_rows):
    standalone_flagged = {r["entry_id"] for r in check_round_number_bias(gl_rows)}
    inline_flagged = {row["entry_id"] for row in gl_rows if cr.rule_round_number(row)}
    assert inline_flagged == standalone_flagged


def test_rule5_parity_with_standalone_module(gl_rows):
    standalone_flagged = {r["entry_id"] for r in check_duplicate_transactions(gl_rows)}
    dup_flags = cr.rule_duplicate_transactions(gl_rows)
    inline_flagged = {gl_rows[idx]["entry_id"] for idx in dup_flags}
    assert inline_flagged == standalone_flagged


def test_rule3_benford_parity_with_standalone_module(gl_rows):
    standalone_chi_square, standalone_deviates = check_benfords_law(gl_rows)
    inline_chi_square, inline_deviates = cr.rule_benfords_law(gl_rows)
    assert inline_chi_square == pytest.approx(standalone_chi_square)
    assert inline_deviates == standalone_deviates


def test_rule3_mad_parity_with_standalone_module(gl_rows):
    standalone_mad = compute_mad(gl_rows)
    inline_mad = cr.compute_mad(gl_rows)
    assert inline_mad == pytest.approx(standalone_mad)
    assert cr.classify_mad_conformity(inline_mad) == classify_mad_conformity(standalone_mad)


def test_rule4_parity_with_standalone_module(form26as_rows):
    standalone_flagged = {f["deductor_name"]: f["severity"] for f in check_reconciliation(form26as_rows)}
    inline_flagged = {f["entity"]: f["severity"] for f in cr.rule_reconciliation(form26as_rows)}
    assert inline_flagged == standalone_flagged


# ---------- Severity helpers ----------

def test_severity_bands_by_amount():
    assert cr.get_severity_by_amount(9999.99) == "LOW"
    assert cr.get_severity_by_amount(10000) == "MEDIUM"
    assert cr.get_severity_by_amount(99999.99) == "MEDIUM"
    assert cr.get_severity_by_amount(100000) == "HIGH"
    assert cr.get_severity_by_amount(999999.99) == "HIGH"
    assert cr.get_severity_by_amount(1000000) == "CRITICAL"
    # sign must not matter -- a Cr leg's amount is just as material as a Dr
    assert cr.get_severity_by_amount(-1000000) == "CRITICAL"


def test_escalate_severity_steps_up_the_ladder():
    assert cr.escalate_severity("LOW") == "MEDIUM"
    assert cr.escalate_severity("HIGH") == "CRITICAL"


def test_escalate_severity_caps_at_critical():
    assert cr.escalate_severity("CRITICAL") == "CRITICAL"


def test_max_severity_picks_the_highest():
    assert cr.max_severity(["LOW", "HIGH", "MEDIUM"]) == "HIGH"
    assert cr.max_severity([]) == "LOW"


def test_three_way_duplicate_group_escalates_severity(gl_rows):
    """The 3-way Sales_30/31/32 duplicate group in the fixture (amount
    50,000, three distinct vouchers) should be escalated one tier above
    the plain amount-based severity (HIGH) by the >2-voucher rule in
    rule_duplicate_transactions, landing on CRITICAL."""
    dup_flags = cr.rule_duplicate_transactions(gl_rows)
    idx_by_entry_id = {row["entry_id"]: idx for idx, row in enumerate(gl_rows)}
    sev = dup_flags[idx_by_entry_id["Sales_30"]]["severity"]
    assert sev == "CRITICAL"

    # Compare against the plain 2-way pair (Receipt_10/11, amount 100,000)
    # which should NOT be escalated -- only HIGH from amount alone.
    pair_sev = dup_flags[idx_by_entry_id["Receipt_10"]]["severity"]
    assert pair_sev == "HIGH"


# ---- Round-number severity (2026-10-07, Addendum 17) -------------------

def test_round_number_alone_is_low_whatever_the_amount():
    """Rs.10,000 stays the screening threshold, but a round amount by itself
    is informational -- a Rs.3,00,000 bank credit must not rank HIGH just for
    being round."""
    for amt in ("10000", "300000", "1500000", "3000000"):
        flag = cr.rule_round_number({"account": "J & K Bank Ltd", "amount": amt})
        assert flag["rule"] == "round_number_bias" and flag["severity"] == "LOW"


def test_round_number_threshold_unchanged():
    assert cr.rule_round_number({"account": "X", "amount": "9990"}) is None
    assert cr.rule_round_number({"account": "X", "amount": "10000.50"}) is None
    assert cr.rule_round_number({"account": "Rent Expense", "amount": "50000"}) is None
    assert cr.rule_round_number({"account": "X", "amount": "20000"}) is not None


def test_round_number_corroborated_by_duplicate_regains_amount_severity():
    """On the real FY2024-25 ledger, Receipt_20 leg 2 (Rs.1,00,000) is both
    round AND a duplicate of Receipt_19: it must still rank HIGH, because the
    duplicate rule carries the amount-based severity."""
    import csv
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "general_ledger.csv")
    if not os.path.exists(path):
        pytest.skip("data/general_ledger.csv not present")
    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    flags = dict(cr.compute_ledger_intrinsic_flags(rows)[0])
    leg = flags["Receipt_20 (leg 2)"]
    assert leg["signal_count"] == 2
    assert {r["rule"] for r in leg["reasons"]} == {"round_number_bias", "duplicate_transaction"}
    assert leg["severity"] == "HIGH"


def test_round_only_legs_are_all_low_on_real_data(gl_rows):
    result = cr.compute_ledger_intrinsic_flags(gl_rows)
    single = result[2]
    round_only = [f for _, f in single if f["reasons"][0]["rule"] == "round_number_bias"]
    assert round_only and all(f["severity"] == "LOW" for f in round_only)
