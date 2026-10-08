"""
Tests for rule_ml_anomaly_detection.py (Rule 8 -- unsupervised ML layer).

Three tiers, same convention as the rest of this project:
  - synthetic ground-truth tests: measure actual recall against
    generate_data.py's planted issues and PIN the honest numbers (not
    inflate them) -- this rule's docstring already states which planted
    issue types it is and isn't designed to catch; these tests prove that
    claim rather than just asserting it in prose.
  - real-data smoke tests: run against the real, committed ledgers and
    pin that the shape/counts stay stable.
  - unit tests: feature building and severity tiering in isolation.
"""
import csv
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(TESTS_DIR, "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from rule_ml_anomaly_detection import (
    build_features,
    detect_anomalies,
    load_rows,
    run_ml_anomaly_report,
    _day_of_month,
    _is_debit,
    _severity_for_rank,
)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_is_debit_recognizes_both_schemas():
    assert _is_debit({"dr_cr": "Dr"}) == 1.0
    assert _is_debit({"dr_cr": "Cr"}) == 0.0
    assert _is_debit({"entry_type": "Debit"}) == 1.0
    assert _is_debit({"entry_type": "Credit"}) == 0.0
    assert _is_debit({}) == 0.0


def test_day_of_month_parses_both_date_formats():
    assert _day_of_month("30-Apr-24") == 30.0       # real ledger format
    assert _day_of_month("2024-04-07") == 7.0        # synthetic format
    assert _day_of_month("") == 15.0                 # blank -> neutral default


def test_severity_bands_are_ordered_and_exclusive():
    assert _severity_for_rank(0.005) == "high"
    assert _severity_for_rank(0.01) == "high"
    assert _severity_for_rank(0.02) == "medium"
    assert _severity_for_rank(0.03) == "medium"
    assert _severity_for_rank(0.04) == "low"
    assert _severity_for_rank(0.05) == "low"
    assert _severity_for_rank(0.06) is None


def test_build_features_omits_vch_type_rarity_when_absent():
    rows_no_vch = [{"account": "A", "amount": "100", "dr_cr": "Dr", "date": "01-Jan-24"}] * 40
    matrix, names, kept = build_features(rows_no_vch)
    assert "vch_type_rarity" not in names
    assert len(matrix) == len(kept) == 40


def test_build_features_includes_vch_type_rarity_when_present():
    rows_with_vch = [
        {"account": "A", "amount": "100", "dr_cr": "Dr", "date": "01-Jan-24", "vch_type": "Journal"}
    ] * 40
    matrix, names, kept = build_features(rows_with_vch)
    assert "vch_type_rarity" in names


def test_build_features_skips_rows_with_bad_amount():
    rows = [{"account": "A", "amount": "not-a-number", "dr_cr": "Dr", "date": "01-Jan-24"}] * 35
    rows += [{"account": "A", "amount": "0", "dr_cr": "Dr", "date": "01-Jan-24"}] * 5
    matrix, names, kept = build_features(rows)
    assert matrix == []
    assert kept == []


def test_detect_anomalies_returns_empty_below_30_rows():
    rows = [{"account": "A", "amount": "100", "dr_cr": "Dr", "date": "01-Jan-24"}] * 25
    assert detect_anomalies(rows) == []


def test_detect_anomalies_is_deterministic():
    """IsolationForest is seeded (RANDOM_STATE) -- two runs on the same
    input must produce the exact same flags, same order. A non-
    deterministic detector would make every downstream report a dice
    roll, which is unacceptable for an audit tool."""
    rows = load_rows(os.path.join(DATA_DIR, "general_ledger.csv"))
    first = detect_anomalies(rows)
    second = detect_anomalies(rows)
    assert [f["entry_id"] for f in first] == [f["entry_id"] for f in second]
    assert [f["anomaly_score"] for f in first] == [f["anomaly_score"] for f in second]


# ---------------------------------------------------------------------------
# Synthetic ground-truth tests -- honest recall, not inflated
# ---------------------------------------------------------------------------

def _synthetic_rows():
    path = os.path.join(DATA_DIR, "synthetic", "general_ledger.csv")
    if not os.path.exists(path):
        import subprocess
        subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "generate_data.py")],
                        check=True, cwd=SCRIPTS_DIR, capture_output=True)
    return load_rows(path)


def test_recall_on_round_number_planted_issues_is_substantial():
    """is_round_10k is a direct feature, so this is the one planted-issue
    type this rule is actually designed to catch well. Pinned at >=50%
    recall (measured: ~74% on the current seeded synthetic set) -- a
    floor, not the exact number, so unrelated changes to generate_data.py's
    random seed don't make this test flaky."""
    rows = _synthetic_rows()
    flags = detect_anomalies(rows)
    flagged_ids = {f["entry_id"] for f in flags}
    planted = [r for r in rows if r.get("is_planted_issue") == "round_number"]
    caught = sum(1 for r in planted if r["entry_id"] in flagged_ids)
    assert len(planted) > 0
    assert caught / len(planted) >= 0.5


def test_recall_on_personnel_based_issues_is_near_zero_by_design():
    """same_approver and off_hours need entered_by/timestamp, which this
    rule deliberately excludes (blank on every real row -- see the
    module docstring). This test PINS that exclusion actually holds in
    practice, not just in the docstring's claim: if a future change
    accidentally let personnel fields leak into the feature set, this
    test's recall figures would jump and fail the upper bound below,
    flagging that the documented scope boundary silently moved."""
    rows = _synthetic_rows()
    flags = detect_anomalies(rows)
    flagged_ids = {f["entry_id"] for f in flags}
    for issue_type in ("same_approver", "off_hours"):
        planted = [r for r in rows if r.get("is_planted_issue") == issue_type]
        caught = sum(1 for r in planted if r["entry_id"] in flagged_ids)
        assert len(planted) > 0
        # near-zero: allow a little incidental overlap, never "mostly caught"
        assert caught / len(planted) <= 0.3


def test_recall_on_exact_duplicates_is_near_zero_by_design():
    """rule_duplicate_transaction.py (Rule 5) is the dedicated rule for
    this pattern (exact account+amount+date+direction match on a
    different voucher). An Isolation Forest has no notion of "this exact
    combination appeared twice" -- a duplicate looks like one more
    ordinary-looking row to it, not an outlier. Pinning near-zero recall
    here documents that this rule does NOT substitute for Rule 5."""
    rows = _synthetic_rows()
    flags = detect_anomalies(rows)
    flagged_ids = {f["entry_id"] for f in flags}
    for issue_type in ("duplicate_source", "duplicate_copy"):
        planted = [r for r in rows if r.get("is_planted_issue") == issue_type]
        caught = sum(1 for r in planted if r["entry_id"] in flagged_ids)
        assert len(planted) > 0
        assert caught / len(planted) <= 0.3


def test_false_positive_rate_on_clean_synthetic_rows_is_bounded():
    """The severity bands flag ~5% of ALL rows by construction, so some
    clean rows will always be flagged -- that's the known, accepted
    cost of an unsupervised method with no ground truth on real data.
    This test just pins that it stays near that ~5% design target and
    doesn't blow up to flagging a much larger chunk of ordinary rows."""
    rows = _synthetic_rows()
    flags = detect_anomalies(rows)
    flagged_ids = {f["entry_id"] for f in flags}
    clean = [r for r in rows if not r.get("is_planted_issue")]
    clean_flagged = sum(1 for r in clean if r["entry_id"] in flagged_ids)
    assert clean_flagged / len(clean) <= 0.08


# ---------------------------------------------------------------------------
# Real-data smoke tests
# ---------------------------------------------------------------------------

def test_real_fy2024_25_ledger_report_shape():
    result = run_ml_anomaly_report(os.path.join(DATA_DIR, "general_ledger.csv"))
    assert result["total_legs_considered"] > 10000
    assert len(result["flags"]) > 0
    assert set(result["severity_counts"]) <= {"high", "medium", "low"}
    for f in result["flags"]:
        assert f["severity"] in ("high", "medium", "low")
        assert f["entry_id"]


def test_real_fy2025_26_ledger_report_shape():
    result = run_ml_anomaly_report(os.path.join(DATA_DIR, "general_ledger_FY2025-26.csv"))
    assert result["total_legs_considered"] > 10000
    assert len(result["flags"]) > 0
    assert set(result["severity_counts"]) <= {"high", "medium", "low"}


def test_flags_are_sorted_most_anomalous_first():
    result = run_ml_anomaly_report(os.path.join(DATA_DIR, "general_ledger.csv"))
    scores = [f["anomaly_score"] for f in result["flags"]]
    assert scores == sorted(scores)
    ranks = [f["anomaly_rank_pct"] for f in result["flags"]]
    assert ranks == sorted(ranks)
