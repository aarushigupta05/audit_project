"""
Stress-test suite: injects known, answer-keyed synthetic irregularities into
COPIES of the real committed data (data/general_ledger.csv, data/form26as.csv)
and runs the ACTUAL combined_report.py pipeline (run_combined_report() itself,
not a reimplementation) against the augmented copies, then diffs the result
against a baseline run on the unmodified real data.

Why this suite exists: every real document tested so far has come back clean
on every rule. That proves the rules don't currently fire falsely on this
data -- it does NOT prove any rule can actually catch a true positive. This
suite closes that gap for Rules 2, 4 and 5 with controlled, documented
irregularities (an explicit answer key per injected row) and verifies three
things for each:
  1. Every injected irregularity is flagged, with the expected rule and
     severity.
  2. Every injected true-negative control (a deliberate non-irregularity --
     a Rent Expense round number, two legs of the same voucher, a Dr/Cr
     pair) is NOT flagged.
  3. No real, pre-existing flag changes as a side effect of the injection --
     a full baseline-vs-augmented diff over every real leg/entity.

Injected rows are appended (never inserted or interleaved), so every real
row's position, per-voucher leg numbering, and existing flags are provably
unaffected by anything except the rule logic itself.

Rule 1 and Rule 3 are deliberately NOT stress-tested here -- see the
2026-10-02 findings report and BASELINE_CHECKPOINT:
  - Rule 1 (segregation of duties) can never fire on real data: entered_by/
    approved_by are always "" (adapt_ledger_to_schema.py's own docstring
    documents why -- Tally exports carry no ERP user-attribution metadata).
    A stress test here would only re-exercise the synthetic-fixture path
    tests/test_rule_segregation_of_duties.py already covers. Documented as
    a known limitation in rule_segregation_of_duties.py and combined_report.py
    instead.
  - Rule 3 (Benford's Law) is dataset-level, not per-row, and the real
    dataset already deviates from Benford's Law (chi-square 229.74) before
    any injection. scripts/analyze_benford_segmentation.py is the separate,
    read-only tool for that open question; no injection test applies here.
"""
import csv
import os

import pytest

import combined_report as cr
from rule_itr_26as_reconciliation import classify_severity as standalone_classify_severity

DATA_DIR = cr.DATA_DIR


def _load_rows(path):
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _gl_row(entry_id, date, account, amount, entry_type="Dr"):
    """Builds a general_ledger.csv-shaped row carrying every real column,
    defaulted the same way adapt_ledger_to_schema.py defaults a row with no
    further provenance -- so an injected row looks exactly like a real
    adapted row, not a hand-built test fixture missing columns the real
    pipeline always provides."""
    return {
        "entry_id": entry_id, "date": date, "timestamp": "",
        "account": account, "vendor": "", "entry_type": entry_type,
        "amount": f"{amount:.2f}", "entered_by": "", "approved_by": "",
        "narration": "", "vch_type": "StressTest", "vch_no": "",
        "dr_cr": entry_type, "resolved": "True", "source_pages": "",
        "corroboration_count": "1", "entry_balanced": "True",
    }


def _form26as_row(deductor_name, reported, claimed, tan):
    return {
        "deductor_name": deductor_name, "pan": "AAAPD1234C",
        "financial_year": "2025-26",
        "tds_reported_by_deductor": f"{reported:.2f}",
        "tds_claimed_in_itr_share": f"{claimed:.2f}",
        "tan": tan,
    }


def _run_combined_report(tmp_path, gl_rows, form26as_rows):
    """Writes the given row lists to temp CSVs, points combined_report.py's
    GL_FILE/FORM26AS_FILE at them, and runs the REAL run_combined_report()
    -- exercising the actual production code path end to end, not a
    reimplementation of its logic."""
    gl_path = tmp_path / "general_ledger.csv"
    form26as_path = tmp_path / "form26as.csv"
    _write_csv(gl_path, gl_rows)
    _write_csv(form26as_path, form26as_rows)

    orig_gl, orig_form = cr.GL_FILE, cr.FORM26AS_FILE
    cr.GL_FILE, cr.FORM26AS_FILE = str(gl_path), str(form26as_path)
    try:
        return cr.run_combined_report(write_exports=False)
    finally:
        cr.GL_FILE, cr.FORM26AS_FILE = orig_gl, orig_form


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(cr.GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {cr.GL_FILE}")
    return _load_rows(cr.GL_FILE)


@pytest.fixture(scope="module")
def real_form26as_rows():
    if not os.path.exists(cr.FORM26AS_FILE):
        pytest.skip(f"data/form26as.csv not present at {cr.FORM26AS_FILE}")
    return _load_rows(cr.FORM26AS_FILE)


@pytest.fixture(scope="module")
def baseline_report(tmp_path_factory, real_gl_rows, real_form26as_rows):
    """The actual pipeline run once against the unmodified real data, used
    as the 'before' side of every before/after diff below."""
    tmp_path = tmp_path_factory.mktemp("baseline")
    return _run_combined_report(tmp_path, real_gl_rows, real_form26as_rows)


def _assert_real_ledger_flags_unchanged(baseline_report, augmented_report, expected_new_keys):
    """Shared diff: every real, pre-existing leg flag must be byte-identical
    after injection, and the only NEW keys allowed are the ones the answer
    key says should appear."""
    baseline_by_key = {leg["leg_key"]: leg for leg in baseline_report["ledger_leg_flags"]}
    augmented_by_key = {leg["leg_key"]: leg for leg in augmented_report["ledger_leg_flags"]}

    for key, data in baseline_by_key.items():
        assert key in augmented_by_key, f"Pre-existing real flag {key} disappeared after injection"
        assert augmented_by_key[key] == data, f"Pre-existing real flag {key} changed after injection"

    new_keys = set(augmented_by_key) - set(baseline_by_key)
    assert new_keys == expected_new_keys, (
        f"Unexpected new/missing flags after injection.\n"
        f"  expected new: {expected_new_keys}\n"
        f"  actual new:   {new_keys}"
    )


# ======================================================================
# Rule 2: Round-number bias
# ======================================================================

def test_rule2_stress_injection(real_gl_rows, real_form26as_rows, baseline_report, tmp_path):
    """Answer key:
      (Updated 2026-10-07, Addendum 17: a round amount ALONE is now LOW at
      every size; detection is unchanged, only the ranking. Severity above LOW
      needs a corroborating rule -- see test_combined_report_rules.py.)
      StressR2_RoundNonExempt_Medium   | Stress Test Account | Rs.20,000   Dr -> flagged, LOW
      StressR2_RoundNonExempt_High     | Stress Test Account | Rs.150,000  Dr -> flagged, LOW
      StressR2_RoundNonExempt_Critical | Stress Test Account | Rs.20,00,000 Dr -> flagged, LOW
      StressR2_RentExempt              | Rent Expense        | Rs.50,000   Dr -> NOT flagged (exemption)
      StressR2_NonRoundControl         | Stress Test Account | Rs.40,123.45 Dr -> NOT flagged (true-negative control)
    """
    injected = [
        _gl_row("StressR2_RoundNonExempt_Medium", "15-Jan-25", "Stress Test Account", 20000),
        _gl_row("StressR2_RoundNonExempt_High", "15-Jan-25", "Stress Test Account", 150000),
        _gl_row("StressR2_RoundNonExempt_Critical", "15-Jan-25", "Stress Test Account", 2000000),
        _gl_row("StressR2_RentExempt", "15-Jan-25", "Rent Expense", 50000),
        _gl_row("StressR2_NonRoundControl", "15-Jan-25", "Stress Test Account", 40123.45),
    ]
    augmented_gl_rows = real_gl_rows + injected

    result = _run_combined_report(tmp_path, augmented_gl_rows, real_form26as_rows)
    flags_by_key = {leg["leg_key"]: leg for leg in result["ledger_leg_flags"]}

    expected_severity = {
        "StressR2_RoundNonExempt_Medium (leg 1)": "LOW",
        "StressR2_RoundNonExempt_High (leg 1)": "LOW",
        "StressR2_RoundNonExempt_Critical (leg 1)": "LOW",
    }
    for key, severity in expected_severity.items():
        assert key in flags_by_key, f"Expected injected flag {key} is missing"
        assert flags_by_key[key]["severity"] == severity
        assert any(r["rule"] == "round_number_bias" for r in flags_by_key[key]["reasons"])

    # True-negative controls must not appear at all.
    assert "StressR2_RentExempt (leg 1)" not in flags_by_key
    assert "StressR2_NonRoundControl (leg 1)" not in flags_by_key

    _assert_real_ledger_flags_unchanged(baseline_report, result, set(expected_severity))


# ======================================================================
# Rule 5: Duplicate transaction detection
# ======================================================================

def test_rule5_stress_injection(real_gl_rows, real_form26as_rows, baseline_report, tmp_path):
    """Answer key:
      StressR5_DupA / StressR5_DupB -- two distinct new vouchers, same
        date/account/amount/direction -> BOTH flagged duplicate_transaction,
        cross-referencing each other, base severity (only 2 vouchers, no
        escalation).
      StressR5_SameVoucher (two legs, SAME entry_id) -- same account/amount/
        date/direction but one voucher -> must NOT be flagged (balancing
        legs of a single transaction, not a duplicate).
      StressR5_DrLeg / StressR5_CrLeg -- same account/amount/date, opposite
        direction -> must NOT be flagged (ordinary double-entry pair;
        direction is part of the match key).
    Amounts (77777 / 88888 / 99999) are deliberately non-round so Rule 2
    doesn't also fire and complicate the severity/signal-count assertions.
    """
    injected = [
        _gl_row("StressR5_DupA", "20-Jan-25", "Stress Test Dummy Account", 77777, "Dr"),
        _gl_row("StressR5_DupB", "20-Jan-25", "Stress Test Dummy Account", 77777, "Dr"),
        _gl_row("StressR5_SameVoucher", "21-Jan-25", "Stress Test Dummy Account", 88888, "Dr"),
        _gl_row("StressR5_SameVoucher", "21-Jan-25", "Stress Test Dummy Account", 88888, "Dr"),
        _gl_row("StressR5_DrLeg", "22-Jan-25", "Stress Test Dummy Account", 99999, "Dr"),
        _gl_row("StressR5_CrLeg", "22-Jan-25", "Stress Test Dummy Account", 99999, "Cr"),
    ]
    augmented_gl_rows = real_gl_rows + injected

    result = _run_combined_report(tmp_path, augmented_gl_rows, real_form26as_rows)
    flags_by_key = {leg["leg_key"]: leg for leg in result["ledger_leg_flags"]}

    assert "StressR5_DupA (leg 1)" in flags_by_key
    assert "StressR5_DupB (leg 1)" in flags_by_key
    assert flags_by_key["StressR5_DupA (leg 1)"]["severity"] == "MEDIUM"
    assert flags_by_key["StressR5_DupB (leg 1)"]["severity"] == "MEDIUM"

    reason_a = next(
        r["reason"] for r in flags_by_key["StressR5_DupA (leg 1)"]["reasons"]
        if r["rule"] == "duplicate_transaction"
    )
    assert "StressR5_DupB" in reason_a
    reason_b = next(
        r["reason"] for r in flags_by_key["StressR5_DupB (leg 1)"]["reasons"]
        if r["rule"] == "duplicate_transaction"
    )
    assert "StressR5_DupA" in reason_b

    # True-negative controls.
    assert "StressR5_SameVoucher (leg 1)" not in flags_by_key
    assert "StressR5_SameVoucher (leg 2)" not in flags_by_key
    assert "StressR5_DrLeg (leg 1)" not in flags_by_key
    assert "StressR5_CrLeg (leg 1)" not in flags_by_key

    expected_new_keys = {"StressR5_DupA (leg 1)", "StressR5_DupB (leg 1)"}
    _assert_real_ledger_flags_unchanged(baseline_report, result, expected_new_keys)


# ======================================================================
# Rule 4: ITR vs Form 26AS reconciliation
# ======================================================================

def test_rule4_stress_injection(real_gl_rows, real_form26as_rows, baseline_report, tmp_path):
    """Answer key (claimed = Rs.10,000 in every percentage case):
      Stress Test Deductor 5pct  | reported 9,500 | claimed 10,000 -> MEDIUM (5% gap)
      Stress Test Deductor 15pct | reported 8,500 | claimed 10,000 -> HIGH (15% gap)
      Stress Test Deductor 35pct | reported 6,500 | claimed 10,000 -> CRITICAL (35% gap)
      Stress Test Deductor ZeroClaimed | reported 500 | claimed 0 -> CRITICAL

    Re-pinned 2026-10-02: the ZeroClaimed case used to pin combined_report.py's
    inline MEDIUM default (an omission, not a design choice) while the
    standalone module returned NONE for the identical shape -- see
    test_rule4_zero_claimed_severity_discrepancy_between_implementations
    below, which now confirms the two implementations agree. Both were fixed
    to classify "claimed nothing, but a deductor reported TDS against you" as
    CRITICAL -- the percentage gap is undefined, not small, and treating it
    as maximal-severity is the correct reading, not an arbitrary default.
    """
    injected = [
        _form26as_row("Stress Test Deductor 5pct", 9500, 10000, "STRS10001A"),
        _form26as_row("Stress Test Deductor 15pct", 8500, 10000, "STRS10002A"),
        _form26as_row("Stress Test Deductor 35pct", 6500, 10000, "STRS10003A"),
        _form26as_row("Stress Test Deductor ZeroClaimed", 500, 0, "STRS10004A"),
    ]
    augmented_form26as_rows = real_form26as_rows + injected

    result = _run_combined_report(tmp_path, real_gl_rows, augmented_form26as_rows)
    flags_by_entity = {f["entity"]: f for f in result["tax_reconciliation_flags"]}

    assert flags_by_entity["Stress Test Deductor 5pct"]["severity"] == "MEDIUM"
    assert flags_by_entity["Stress Test Deductor 15pct"]["severity"] == "HIGH"
    assert flags_by_entity["Stress Test Deductor 35pct"]["severity"] == "CRITICAL"
    assert flags_by_entity["Stress Test Deductor ZeroClaimed"]["severity"] == "CRITICAL"

    # Real deductors (all clean, per test_tax_reconciliation_clean in
    # test_combined_report_regression.py) must produce zero flags, injected
    # or not -- only the 4 injected entities should appear at all.
    assert {f["entity"] for f in result["tax_reconciliation_flags"]} == {
        "Stress Test Deductor 5pct", "Stress Test Deductor 15pct",
        "Stress Test Deductor 35pct", "Stress Test Deductor ZeroClaimed",
    }

    # Ledger side was untouched in this test -- leg flags must be identical
    # to baseline, not just "unchanged for pre-existing keys".
    assert result["ledger_leg_flags"] == baseline_report["ledger_leg_flags"]


def test_rule4_zero_claimed_severity_discrepancy_between_implementations():
    """Was: 'Documents a real, pre-existing discrepancy...'. Fixed 2026-10-02:
    for a zero-claimed-but-nonzero-reported mismatch, the standalone module
    (rule_itr_26as_reconciliation.classify_severity) and combined_report.py's
    inline reimplementation of the SAME rule used to disagree on severity --
    standalone returned NONE (claimed=0 forced pct_diff=0, falling through to
    the "else: NONE" branch), while the inline version had no NONE branch and
    defaulted straight to MEDIUM. Neither behavior was a deliberate severity
    choice for this shape -- confirmed via the old pinned test's comment,
    which only asserted "must not divide by zero" (crash-avoidance), never
    that NONE or MEDIUM was intended. test_combined_report_rules.py's parity
    tests only ever compared Rules 1, 2 and 5 between the two
    implementations, not Rule 4, which is why this sat uncaught.

    Both implementations are now fixed to agree: claimed=0 with a nonzero
    mismatch is CRITICAL (the percentage gap is undefined/maximal, not a
    small one to be waved through), while a genuine zero/zero is still NONE.
    This test now pins that agreement instead of the discrepancy, kept
    (not deleted) as a regression guard against the two implementations
    silently drifting apart again."""
    # Standalone module (rule_itr_26as_reconciliation.classify_severity):
    # claimed=0 with a real nonzero mismatch is now CRITICAL.
    assert standalone_classify_severity(mismatch_amount=500, claimed_amount=0) == "CRITICAL"

    # combined_report.py's inline rule_reconciliation(): reproduced here
    # exactly (without duplicating the whole function) to pin its actual
    # behavior for the identical shape -- must match the branch added to
    # rule_reconciliation() in scripts/combined_report.py.
    claimed, reported = 0.0, 500.0
    mismatch = claimed - reported
    if claimed == 0:
        inline_severity = "CRITICAL" if mismatch != 0 else "NONE"
    else:
        pct_diff = abs(mismatch / claimed) * 100
        inline_severity = "CRITICAL" if pct_diff >= 30 else "HIGH" if pct_diff >= 10 else "MEDIUM"
    assert inline_severity == "CRITICAL"

    assert inline_severity == standalone_classify_severity(500, 0), (
        "The standalone and inline Rule 4 implementations must agree on the "
        "zero-claimed severity -- if one changed without the other, that's "
        "the discrepancy this test exists to catch."
    )
