"""
Tests for combined_report.py's V3.12 refactor: compute_ledger_intrinsic_flags()
(Rules 1/2/3/5/7, extracted out of run_combined_report() so they can run
against any general_ledger.csv-shaped file) and run_ledger_only_report()
(the new entry point that uses it for a ledger OTHER than the committed
FY2024-25 one -- built for the FY2025-26 ledger, which has no matching
FY-specific Form26AS/ITR or trial balance document to run Rules 4/6
against yet).

The refactor's most important invariant -- that run_combined_report()'s
own output for the FY2024-25 ledger is completely unchanged -- is already
covered by the existing tests/test_combined_report_regression.py (every
value there is pinned against the real committed data, and all of them
still pass unmodified). This file covers the NEW function only.
"""
import json
import os

import pytest

import combined_report as cr

DATA_DIR = cr.DATA_DIR
FY2025_26_GL = os.path.join(DATA_DIR, "general_ledger_FY2025-26.csv")


@pytest.fixture(scope="module")
def fy2025_26_available():
    if not os.path.exists(FY2025_26_GL):
        pytest.skip(f"data/general_ledger_FY2025-26.csv not present at {FY2025_26_GL}")


def test_compute_ledger_intrinsic_flags_matches_run_combined_report_on_fy2024_25():
    """compute_ledger_intrinsic_flags(gl_rows) must produce exactly the
    same ledger-intrinsic numbers run_combined_report() reports for the
    committed FY2024-25 ledger -- it's the same computation, just callable
    on its own now."""
    import csv
    with open(cr.GL_FILE, "r") as f:
        gl_rows = list(csv.DictReader(f))

    (sorted_flags, multi_signal, single_signal, severity_counts,
     voucher_gap_flags, chi_square, deviates, mad, mad_conformity,
     ml_anomaly_flags) = (
        cr.compute_ledger_intrinsic_flags(gl_rows)
    )

    full_result = cr.run_combined_report(write_exports=False)

    assert len(sorted_flags) == full_result["summary"]["total_legs_flagged"]
    assert len(multi_signal) == full_result["summary"]["multi_signal"]
    assert len(single_signal) == full_result["summary"]["single_signal"]
    assert severity_counts == full_result["summary"]["by_severity"]
    assert voucher_gap_flags == full_result["voucher_sequence_gap_flags"]
    assert round(chi_square, 2) == full_result["benford"]["chi_square"]
    assert deviates == full_result["benford"]["deviates"]
    assert round(mad, 5) == full_result["benford"]["mad"]
    assert mad_conformity == full_result["benford"]["mad_conformity"]
    assert len(ml_anomaly_flags) == len(full_result["ml_anomaly_flags"])
    assert [f["entry_id"] for f in ml_anomaly_flags] == (
        [f["entry_id"] for f in full_result["ml_anomaly_flags"]]
    )


def test_run_ledger_only_report_against_fy2025_26(fy2025_26_available, tmp_path, monkeypatch):
    """Runs against the real FY2025-26 ledger (diagnosed clean in V3.11) --
    must produce a result with the same shape as run_combined_report()
    minus the two FY-specific-document rules (4 and 6), and must not
    touch the FY2024-25 combined_report.json."""
    monkeypatch.setattr(cr, "OUTPUT_DIR", str(tmp_path))
    result = cr.run_ledger_only_report(FY2025_26_GL, "FY2025-26_test", write_exports=True)

    assert result["dataset"]["report_label"] == "FY2025-26_test"
    assert result["dataset"]["total_legs"] == 13981  # pinned, see V3.11 checkpoint
    assert "tax_reconciliation_flags" not in result
    assert "trial_balance_flags" not in result
    assert "benford" in result
    assert "voucher_sequence_gap_flags" in result
    assert "ml_anomaly_flags" in result
    assert len(result["ml_anomaly_flags"]) > 0
    assert "ledger_leg_flags" in result
    assert "summary" in result

    json_path = tmp_path / "combined_report_FY2025-26_test.json"
    assert json_path.exists()
    with open(json_path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["dataset"]["total_legs"] == 13981

    assert (tmp_path / "ledger_leg_flags_FY2025-26_test.csv").exists()
    assert (tmp_path / "voucher_sequence_gap_flags_FY2025-26_test.csv").exists()
    assert (tmp_path / "ml_anomaly_flags_FY2025-26_test.csv").exists()
    # Must never touch the FY2024-25 combined_report.json or its CSVs --
    # this is a parallel, separately-named output, not a replacement.
    assert not (tmp_path / "combined_report.json").exists()


def test_run_ledger_only_report_known_series_have_gaps(fy2025_26_available):
    """Pinned from a real run against the FY2025-26 ledger: 5 series have
    gaps (Journal, Payment, Receipt, Sales Cash, Sales GST -- same shape of
    finding as the FY2024-25 ledger's Rule 7 result), Purchase's vendor
    invoice numbers are excluded by the digit-length heuristic as always."""
    result = cr.run_ledger_only_report(FY2025_26_GL, "FY2025-26_pin_check", write_exports=False)
    series_with_gaps = {f["vch_type"] for f in result["voucher_sequence_gap_flags"]}
    assert series_with_gaps == {"Journal", "Payment", "Receipt", "Sales Cash", "Sales GST"}
    assert not any(f["vch_type"] == "Purchase" for f in result["voucher_sequence_gap_flags"])


def test_entry_id_collides_across_the_two_ledgers_never_concatenate_them(fy2025_26_available):
    """Pins the exact finding that rules out ever merging/concatenating
    general_ledger.csv and general_ledger_FY2025-26.csv for a combined
    Benford's Law or Rule 7 run (see BASELINE_CHECKPOINT_V3.12.md, the
    "RESOLVED: never merge" section):
    Tally restarts bare-numbered voucher series (Journal, Payment,
    Receipt, Contra) from 1 at the start of each financial year, so the
    SAME entry_id names a completely different real voucher in each
    ledger file. entry_id is only unique WITHIN one ledger file, never
    across the two -- a naive concatenation would silently collide
    unrelated transactions under one id and corrupt every per-voucher
    rule. Confirmed directly rather than assumed: Journal_500 is a
    14-Feb-25 Hindustan Petroleum Corporation entry in the FY2024-25
    ledger, and an unrelated 9-Jan-26 "OIL SUPPLIER RENT" entry in the
    FY2025-26 ledger."""
    import csv
    with open(cr.GL_FILE, encoding="utf-8") as f:
        old_legs = [r for r in csv.DictReader(f) if r["entry_id"] == "Journal_500"]
    with open(FY2025_26_GL, encoding="utf-8") as f:
        new_legs = [r for r in csv.DictReader(f) if r["entry_id"] == "Journal_500"]

    assert old_legs and new_legs, "Journal_500 must exist in both ledgers for this to be meaningful"
    old_dates = {r["date"] for r in old_legs}
    new_dates = {r["date"] for r in new_legs}
    assert old_dates != new_dates, (
        "Journal_500 resolved to the same date in both ledgers -- the FY-reset "
        "finding this test pins may no longer hold; re-verify before merging "
        "anything across these two files."
    )
    old_amounts = {r["amount"] for r in old_legs}
    new_amounts = {r["amount"] for r in new_legs}
    assert old_amounts != new_amounts
