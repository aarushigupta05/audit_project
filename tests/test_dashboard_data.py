"""
Tests for dashboard/data.py -- the UI-agnostic data-loading/transform layer
behind the Streamlit dashboard (dashboard/app.py). Kept separate from
dashboard/app.py specifically so this logic is testable without Streamlit
having to render anything (see dashboard/data.py's module docstring).

Two kinds of coverage:
  1. Hand-built dicts shaped exactly like combined_report.json/
     gstr_report.json, pinning each transform function's behavior
     (including the "pipeline hasn't been run yet" None-input path).
  2. The real, committed output/combined_report.json and
     output/gstr_report.json, pinning that today's real data loads and
     transforms without error and matches the known real totals (the
     same real-data-reconciles-cleanly style already used throughout this
     project's test suite).
"""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(TESTS_DIR, "..")
DASHBOARD_DIR = os.path.join(PROJECT_ROOT, "dashboard")
sys.path.insert(0, DASHBOARD_DIR)

import pytest
import pandas as pd

import data as dd


# ---------- Hand-built fixtures shaped like the real JSON exports ----------

@pytest.fixture
def sample_report():
    return {
        "generated_at": "2026-10-02T06:15:11",
        "dataset": {"general_ledger_file": "/x/general_ledger.csv", "total_legs": 100},
        "benford": {
            "chi_square": 229.74, "threshold": 15.5, "deviates": True,
            "mad": 0.01061, "mad_conformity": "Acceptable conformity",
        },
        "tax_reconciliation_flags": [
            {"entity": "Acme Pvt Ltd", "reason": "ITR claims Rs.0 but 26AS reports Rs.500 (gap: Rs.-500.00)",
             "severity": "CRITICAL", "source": "ITR-26AS Reconciliation"},
        ],
        "trial_balance_flags": [
            {"account": "Sales", "side": "debit", "tb_amount": 1000000.0, "gl_amount": 1500000.0,
             "mismatch_amount": 500000.0, "severity": "CRITICAL", "variants_used": ["Sales"]},
        ],
        "ledger_leg_flags": [
            {
                "leg_key": "Receipt_20 (leg 2)", "entry_id": "Receipt_20", "leg_num": 2,
                "date": "5-Apr-24", "account": "J & K Bank Ltd", "amount": "100000.00",
                "reasons": [
                    {"rule": "round_number_bias", "reason": "Suspiciously round amount (100000.0)", "severity": "HIGH"},
                    {"rule": "duplicate_transaction", "reason": "Duplicate transaction candidate", "severity": "HIGH"},
                ],
                "signal_count": 2, "severity": "HIGH",
            },
            {
                "leg_key": "Payment_5 (leg 1)", "entry_id": "Payment_5", "leg_num": 1,
                "date": "10-May-24", "account": "Cash", "amount": "999999.00",
                "reasons": [
                    {"rule": "round_number_bias", "reason": "Suspiciously round amount (999999.0)", "severity": "LOW"},
                ],
                "signal_count": 1, "severity": "LOW",
            },
        ],
        "summary": {
            "total_legs_flagged": 2, "multi_signal": 1, "single_signal": 1,
            "by_severity": {"HIGH": 1, "LOW": 1},
        },
    }


@pytest.fixture
def multi_leg_voucher_report():
    """A report shaped to exercise voucher-level (not leg-level) severity
    aggregation: 'Receipt_182' has two flagged legs (both CRITICAL, the
    real-world pattern this was built for -- round_number_bias flagging
    both sides of one journal entry independently), and 'Payment_9' has
    one MEDIUM leg and one HIGH leg, so its voucher-level severity must
    come out HIGH (the max), not MEDIUM."""
    return {
        "ledger_leg_flags": [
            {"leg_key": "Receipt_182 (leg 1)", "entry_id": "Receipt_182", "leg_num": 1,
             "date": "1-Jun-24", "account": "A R CONSTRUCTION COMPANY", "amount": "1000000.00",
             "reasons": [{"rule": "round_number_bias", "reason": "round", "severity": "CRITICAL"}],
             "signal_count": 1, "severity": "CRITICAL"},
            {"leg_key": "Receipt_182 (leg 2)", "entry_id": "Receipt_182", "leg_num": 2,
             "date": "1-Jun-24", "account": "J & K Bank Ltd", "amount": "1000000.00",
             "reasons": [{"rule": "round_number_bias", "reason": "round", "severity": "CRITICAL"}],
             "signal_count": 1, "severity": "CRITICAL"},
            {"leg_key": "Payment_9 (leg 1)", "entry_id": "Payment_9", "leg_num": 1,
             "date": "2-Jun-24", "account": "Cash", "amount": "50000.00",
             "reasons": [{"rule": "round_number_bias", "reason": "round", "severity": "MEDIUM"}],
             "signal_count": 1, "severity": "MEDIUM"},
            {"leg_key": "Payment_9 (leg 2)", "entry_id": "Payment_9", "leg_num": 2,
             "date": "2-Jun-24", "account": "Vendor X", "amount": "50000.00",
             "reasons": [{"rule": "duplicate_transaction", "reason": "dup", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
        ],
    }


@pytest.fixture
def sample_gstr_report():
    return {
        "generated_at": "2026-10-02T06:22:38",
        "dataset": {
            "gstr1_file": "/x/gstr1_summary.csv", "gstr3b_file": "/x/gstr3b_summary.csv",
            "gstr1_periods": 1, "gstr3b_periods": 1, "periods": ["June 2026-27"],
        },
        "flags": [
            {"period": "June 2026-27", "check": "gstr1_vs_gstr3b_taxable_supplies",
             "reason": "Taxable value: GSTR-1 reports Rs.100000.0 but GSTR-3B reports Rs.92970.96 (gap: Rs.7029.04)",
             "severity": "MEDIUM"},
        ],
        "summary": {"total_flagged": 1, "by_severity": {"MEDIUM": 1}},
    }


@pytest.fixture
def sample_ledger_gstr_report():
    """Shaped like rule_ledger_gstr_reconciliation.run_ledger_gstr_report()'s
    output -- note the two separate period lists (taxable vs non-GST
    activity), unlike sample_gstr_report's single "periods" list above;
    see rule_ledger_gstr_reconciliation.py's module docstring for why."""
    return {
        "generated_at": "2026-10-04T10:00:00",
        "dataset": {
            "general_ledger_file": "/x/general_ledger_FY2025-26.csv",
            "gstr1_file": "/x/gstr1_summary.csv",
            "ledger_periods_with_taxable_activity": ["April 2025-26", "May 2025-26"],
            "ledger_periods_with_non_gst_activity": ["April 2025-26", "May 2025-26"],
        },
        "flags": [
            {"period": "May 2025-26", "check": "ledger_vs_gstr1_taxable_supply",
             "reason": "Taxable value: ledger shows Rs.100000.00 but GSTR-1 reports Rs.90000.00 (gap: Rs.10000.00)",
             "severity": "HIGH"},
        ],
        "summary": {"total_flagged": 1, "by_severity": {"HIGH": 1}},
    }


@pytest.fixture
def sample_ledger_only_report():
    """Shaped like combined_report.run_ledger_only_report()'s output
    (e.g. output/combined_report_FY2025-26.json) -- same ledger_leg_flags/
    benford shape as sample_report above, but WITHOUT
    tax_reconciliation_flags/trial_balance_flags (deliberately absent,
    not a different shape -- see that function's own docstring), plus
    voucher_sequence_gap_flags and ml_anomaly_flags, which sample_report
    above doesn't carry (added so this fixture also exercises
    voucher_gap_dataframe()/ml_anomaly_dataframe() against the lowercase-
    severity, Rule-8-shaped records those functions normalize)."""
    return {
        "generated_at": "2026-10-04T10:05:00",
        "dataset": {
            "general_ledger_file": "/x/general_ledger_FY2025-26.csv",
            "report_label": "FY2025-26", "total_legs": 50,
        },
        "benford": {
            "chi_square": 176.06, "threshold": 15.5, "deviates": True,
            "mad": 0.01026, "mad_conformity": "Acceptable conformity",
        },
        "voucher_sequence_gap_flags": [
            {"vch_type": "Journal", "prefix": "", "missing_count": 2, "group_min": 1,
             "group_max": 100, "group_size": 98, "severity": "HIGH",
             "missing_numbers": [10, 20]},
        ],
        "ml_anomaly_flags": [
            {"entry_id": "Debit Note_1", "account": "Vendor Z", "dr_cr": "Dr",
             "amount": "73.00", "date": "1-May-25", "vch_type": "Debit Note",
             "severity": "high", "anomaly_score": -0.11, "anomaly_rank_pct": 0.01},
            {"entry_id": "Contra_5", "account": "Bank A", "dr_cr": "Cr",
             "amount": "1000000.00", "date": "2-May-25", "vch_type": "Contra",
             "severity": "medium", "anomaly_score": -0.05, "anomaly_rank_pct": 0.02},
        ],
        "ledger_leg_flags": [],
        "summary": {
            "total_legs_flagged": 0, "multi_signal": 0, "single_signal": 0,
            "by_severity": {}, "ml_anomaly_flags": 2,
        },
    }


# ---------- kpi_summary ----------

def test_kpi_summary_with_report(sample_report):
    kpis = dd.kpi_summary(sample_report)
    assert kpis == {
        "total_legs": 100,
        "total_legs_flagged": 2,
        "multi_signal": 1,
        "single_signal": 1,
        "critical_count": 0,
        "high_count": 1,
        "generated_at": "2026-10-02T06:15:11",
    }


def test_kpi_summary_none_report():
    kpis = dd.kpi_summary(None)
    assert kpis["total_legs"] == 0
    assert kpis["total_legs_flagged"] == 0
    assert kpis["generated_at"] is None


# ---------- severity_breakdown_df ----------

def test_severity_breakdown_df_orders_critical_first(sample_report):
    df = dd.severity_breakdown_df(sample_report)
    assert list(df["severity"]) == ["HIGH", "LOW"]
    assert list(df["count"]) == [1, 1]


def test_severity_breakdown_df_none_report_is_empty_with_columns():
    df = dd.severity_breakdown_df(None)
    assert df.empty
    assert list(df.columns) == ["severity", "count"]


# ---------- ledger_flags_dataframe ----------

def test_ledger_flags_dataframe_expands_one_row_per_reason(sample_report):
    df = dd.ledger_flags_dataframe(sample_report)
    # 2 reasons on the first flag + 1 reason on the second = 3 rows total
    assert len(df) == 3
    assert set(df["rule"]) == {"round_number_bias", "duplicate_transaction"}
    # the multi-signal leg's two rows both carry its overall severity (HIGH),
    # not each reason's own severity, which lives in reason_severity instead
    multi_rows = df[df["leg_key"] == "Receipt_20 (leg 2)"]
    assert len(multi_rows) == 2
    assert set(multi_rows["severity"]) == {"HIGH"}
    assert set(multi_rows["reason_severity"]) == {"HIGH"}


def test_ledger_flags_dataframe_none_report_is_empty_with_columns():
    df = dd.ledger_flags_dataframe(None)
    assert df.empty
    assert "rule" in df.columns and "severity" in df.columns


# ---------- ledger_flag_row_count / distinct_voucher_count / voucher_severity_breakdown_df ----------
# (flaws #2 and #3 from the 2026-10-02 UI review: unreconciled leg-count vs
# row-count totals, and leg-granularity vs voucher-granularity severity counts)

def test_ledger_flag_row_count_counts_every_reason_not_every_leg(sample_report):
    # sample_report has one leg with 2 reasons + one leg with 1 reason = 3 rows
    assert dd.ledger_flag_row_count(sample_report) == 3


def test_ledger_flag_row_count_none_report():
    assert dd.ledger_flag_row_count(None) == 0


def test_distinct_voucher_count(sample_report):
    # Receipt_20 and Payment_5 are two distinct entry_ids
    assert dd.distinct_voucher_count(sample_report) == 2


def test_distinct_voucher_count_collapses_multi_leg_vouchers(multi_leg_voucher_report):
    # 4 flagged legs, but only 2 distinct vouchers (Receipt_182, Payment_9)
    assert dd.distinct_voucher_count(multi_leg_voucher_report) == 2


def test_voucher_severity_breakdown_uses_max_severity_per_voucher(multi_leg_voucher_report):
    """The exact bug this was built to fix: Receipt_182's two CRITICAL legs
    must collapse into ONE CRITICAL voucher, not two. Payment_9 has a
    MEDIUM leg and a HIGH leg -- its voucher-level severity must be HIGH
    (the max), not MEDIUM and not double-counted as both."""
    df = dd.voucher_severity_breakdown_df(multi_leg_voucher_report)
    as_dict = dict(zip(df["severity"], df["voucher_count"]))
    assert as_dict == {"CRITICAL": 1, "HIGH": 1}


def test_voucher_severity_breakdown_df_none_report():
    df = dd.voucher_severity_breakdown_df(None)
    assert df.empty
    assert list(df.columns) == ["severity", "voucher_count"]


# ---------- tax_reconciliation_dataframe / trial_balance_dataframe ----------

def test_tax_reconciliation_dataframe(sample_report):
    df = dd.tax_reconciliation_dataframe(sample_report)
    assert len(df) == 1
    assert df.iloc[0]["entity"] == "Acme Pvt Ltd"
    assert df.iloc[0]["severity"] == "CRITICAL"


def test_tax_reconciliation_dataframe_empty_when_no_flags():
    report = {"tax_reconciliation_flags": []}
    df = dd.tax_reconciliation_dataframe(report)
    assert df.empty
    assert list(df.columns) == ["entity", "reason", "severity", "source"]


def test_trial_balance_dataframe(sample_report):
    df = dd.trial_balance_dataframe(sample_report)
    assert len(df) == 1
    assert df.iloc[0]["account"] == "Sales"
    assert df.iloc[0]["mismatch_amount"] == 500000.0


# ---------- benford_summary ----------

def test_benford_summary(sample_report):
    b = dd.benford_summary(sample_report)
    assert b["chi_square"] == 229.74
    assert b["mad"] == 0.01061
    assert b["mad_conformity"] == "Acceptable conformity"


def test_benford_summary_none_report():
    assert dd.benford_summary(None) is None


def test_benford_summary_missing_key():
    assert dd.benford_summary({}) is None


def test_benford_summary_stale_report_missing_mad_fields():
    """Regression test for the exact bug that broke the dashboard in the
    field (2026-10-02): a real output/combined_report.json on disk had
    never been regenerated since before MAD was added to
    scripts/combined_report.py, so its "benford" section had chi_square/
    threshold/deviates but no "mad"/"mad_conformity" at all. The old
    implementation did `dict(report["benford"])` and app.py then indexed
    benford['mad'] directly, which raised KeyError and crashed the whole
    app. benford_summary() must come back with mad/mad_conformity as None
    instead of raising, so the UI can show a "re-run the pipeline" prompt."""
    stale_report = {
        "benford": {"chi_square": 229.74, "threshold": 15.5, "deviates": True},
    }
    b = dd.benford_summary(stale_report)
    assert b["chi_square"] == 229.74
    assert b["threshold"] == 15.5
    assert b["deviates"] is True
    assert b["mad"] is None
    assert b["mad_conformity"] is None


# ---------- gstr_summary ----------

def test_gstr_summary(sample_gstr_report):
    g = dd.gstr_summary(sample_gstr_report)
    assert g["periods"] == ["June 2026-27"]
    assert g["total_flagged"] == 1
    assert g["by_severity"] == {"MEDIUM": 1}
    assert len(g["flags_df"]) == 1
    assert g["flags_df"].iloc[0]["check"] == "gstr1_vs_gstr3b_taxable_supplies"


def test_gstr_summary_none_report():
    g = dd.gstr_summary(None)
    assert g["generated_at"] is None
    assert g["periods"] == []
    assert g["total_flagged"] == 0
    assert g["flags_df"].empty


# ---------- rule_options ----------

def test_rule_options_sorted_and_deduplicated(sample_report):
    assert dd.rule_options(sample_report) == ["duplicate_transaction", "round_number_bias"]


def test_rule_options_none_report():
    assert dd.rule_options(None) == []


# ---------- Against the real, committed output files ----------

def test_real_combined_report_loads_and_transforms_cleanly():
    """The real output/combined_report.json this project currently ships.
    Not a fixed-value pin (those numbers will shift as the pipeline is
    re-run), just proof the real file's actual shape flows through every
    transform function without raising and without silently returning
    empty on non-empty input."""
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")

    kpis = dd.kpi_summary(report)
    assert kpis["total_legs"] > 0
    assert kpis["total_legs_flagged"] > 0

    ledger_df = dd.ledger_flags_dataframe(report)
    assert len(ledger_df) >= kpis["total_legs_flagged"]  # >= : multi-signal legs expand to >1 row

    sev_df = dd.severity_breakdown_df(report)
    assert sev_df["count"].sum() == kpis["total_legs_flagged"]

    benford = dd.benford_summary(report)
    assert benford is not None
    assert benford["mad_conformity"] == "Acceptable conformity"

    # Both currently reconcile clean (see test_combined_report_regression.py) --
    # pinned here too so a dashboard regression (e.g. KeyError on real data)
    # would be caught even if that other suite weren't run.
    assert dd.tax_reconciliation_dataframe(report).empty
    assert dd.trial_balance_dataframe(report).empty


def test_real_gstr_report_loads_and_transforms_cleanly():
    """UPDATED 2026-10-03: real GSTR data now covers 16 months (April 2025
    - July 2026) via the GSTR All_Months PDFs, not just the one-off June
    2026-27 filing -- see BASELINE_CHECKPOINT_V3.12.md."""
    gstr_report = dd.load_gstr_report()
    if gstr_report is None:
        pytest.skip("output/gstr_report.json has not been generated in this environment")

    g = dd.gstr_summary(gstr_report)
    assert len(g["periods"]) == 16
    assert "June 2026-27" in g["periods"]
    assert "April 2025-26" in g["periods"]
    assert g["flags_df"].empty
    assert g["total_flagged"] == 0


def test_load_combined_report_missing_file_returns_none(tmp_path):
    assert dd.load_combined_report(path=str(tmp_path / "nope.json")) is None


def test_load_gstr_report_missing_file_returns_none(tmp_path):
    assert dd.load_gstr_report(path=str(tmp_path / "nope.json")) is None


def test_load_segmentation_report_missing_file_returns_none(tmp_path):
    assert dd.load_segmentation_report(path=str(tmp_path / "nope.json")) is None


# ---------- tax_reconciliation_summary / trial_balance_summary (flaw #5) ----------
# These read the real underlying files directly (not combined_report.json),
# so they're tested against the real committed data, same as the
# test_real_* tests above.

def test_tax_reconciliation_summary_real_data():
    summary = dd.tax_reconciliation_summary(dd.load_combined_report())
    assert summary["deductor_count"] == 1
    assert summary["total_claimed"] == pytest.approx(32018.0)
    assert summary["total_reported"] == pytest.approx(32018.0)
    assert summary["flags_df"].empty


def test_trial_balance_summary_real_data():
    summary = dd.trial_balance_summary(dd.load_combined_report())
    assert summary["clean_count"] == 82
    assert summary["mismatched_count"] == 0
    assert summary["total_checked"] == 82
    assert summary["flags_df"].empty


# ---------- benford_segmentation_summary (flaw #4) ----------

@pytest.fixture
def sample_segmentation_report():
    return {
        "generated_at": "2026-10-02T16:00:00",
        "overall": {"n": 100, "chi_square": 50.0, "deviates": True,
                     "mad": 0.01, "mad_conformity": "Acceptable"},
        "by_vch_type": [
            {"segment": "Purchase", "n": 50, "pct_of_total": 50.0,
             "chi_square": 500.0, "deviates": True, "mad": 0.13, "mad_conformity": "Nonconformity"},
            {"segment": "Contra", "n": 30, "pct_of_total": 30.0,
             "chi_square": 200.0, "deviates": True, "mad": 0.09, "mad_conformity": "Nonconformity"},
            {"segment": "Journal", "n": 20, "pct_of_total": 20.0,
             "chi_square": 20.0, "deviates": True, "mad": 0.01, "mad_conformity": "Acceptable"},
        ],
        "by_account": [
            {"segment": "Cash", "n": 60, "pct_of_total": 60.0,
             "chi_square": 100.0, "deviates": True, "mad": 0.03, "mad_conformity": "Nonconformity"},
        ],
        "exclusion_checks": [
            {"label": "Excluding Cash, Sales (top accounts)", "n": 40, "pct_of_total": 40.0,
             "chi_square": 60.0, "deviates": True, "mad": 0.02, "mad_conformity": "Nonconformity"},
            {"label": "Excluding fuel-sale types", "n": 35, "pct_of_total": 35.0,
             "chi_square": 55.0, "deviates": True, "mad": 0.015, "mad_conformity": "Marginal"},
        ],
    }


def test_benford_segmentation_summary_none_report():
    assert dd.benford_segmentation_summary(None) is None


def test_benford_segmentation_summary_identifies_outliers(sample_segmentation_report):
    summary = dd.benford_segmentation_summary(sample_segmentation_report)
    assert summary["outlier_segments"] == ["Purchase", "Contra"]
    assert "Purchase" in summary["verdict"]
    assert "Contra" in summary["verdict"]
    assert "3 of 3" in summary["verdict"]  # all 3 sample segments have n>=10 and deviate


def test_benford_segmentation_summary_dataframes_have_expected_shape(sample_segmentation_report):
    summary = dd.benford_segmentation_summary(sample_segmentation_report)
    assert len(summary["by_vch_type_df"]) == 3
    assert len(summary["by_account_df"]) == 1
    assert "Purchase" in set(summary["by_vch_type_df"]["segment"])


def test_benford_segmentation_summary_flags_when_exclusion_check_conforms():
    """If an exclusion check does NOT deviate, the verdict should say so
    rather than claiming the deviation is broad-based when it isn't."""
    report = {
        "overall": {"n": 100, "chi_square": 50.0, "deviates": True, "mad": 0.01, "mad_conformity": "Acceptable"},
        "by_vch_type": [
            {"segment": "A", "n": 20, "pct_of_total": 50.0, "chi_square": 30.0,
             "deviates": True, "mad": 0.02, "mad_conformity": "Nonconformity"},
            {"segment": "B", "n": 20, "pct_of_total": 50.0, "chi_square": 20.0,
             "deviates": True, "mad": 0.01, "mad_conformity": "Acceptable"},
        ],
        "by_account": [],
        "exclusion_checks": [
            {"label": "x", "n": 10, "pct_of_total": 10.0, "chi_square": 5.0,
             "deviates": False, "mad": 0.005, "mad_conformity": "Close"},
        ],
    }
    summary = dd.benford_segmentation_summary(report)
    assert "worth a closer look" in summary["verdict"]
    assert summary["both_exclusions_deviate"] is False


# ---------- benford_segmentation_summary: breadth counts (2026-10-02 checklist UI) ----------

def test_benford_segmentation_summary_exposes_breadth_counts(sample_segmentation_report):
    """These are the same numbers the `verdict` paragraph is already built
    from (see that field's docstring) -- pinned here as their own fields
    so the Benford tab's checklist can use them directly instead of
    parsing the paragraph."""
    summary = dd.benford_segmentation_summary(sample_segmentation_report)
    assert summary["substantial_count"] == 3  # all 3 sample vch_types have n>=10
    assert summary["deviating_count"] == 3    # and all 3 deviate
    assert summary["both_exclusions_deviate"] is True  # both fixture exclusion checks deviate
    assert summary["account_substantial_count"] == 1  # the one by_account row, n=60
    assert summary["account_deviating_count"] == 1


def test_benford_segmentation_summary_outlier_chi_squares_align_with_outlier_segments(sample_segmentation_report):
    summary = dd.benford_segmentation_summary(sample_segmentation_report)
    assert summary["outlier_segments"] == ["Purchase", "Contra"]
    assert summary["outlier_chi_squares"] == [500.0, 200.0]


def test_benford_segmentation_summary_breadth_counts_real_data():
    report = dd.load_combined_report()
    seg_report = dd.load_segmentation_report()
    if report is None or seg_report is None:
        pytest.skip("pipeline output not generated in this environment")
    summary = dd.benford_segmentation_summary(seg_report)
    assert summary["substantial_count"] == summary["deviating_count"] == 8
    assert summary["both_exclusions_deviate"] is True


# ---------- priority_findings_concentration_note ----------

def test_priority_findings_concentration_note_none_report():
    assert dd.priority_findings_concentration_note(None) is None


def test_priority_findings_concentration_note_empty_df():
    assert dd.priority_findings_concentration_note(dd.priority_findings(None)) is None


def test_priority_findings_concentration_note_all_distinct_accounts():
    report = {
        "ledger_leg_flags": [
            {"leg_key": "A (leg 1)", "entry_id": "A", "date": "1-Jan-24", "account": "Account A",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
            {"leg_key": "B (leg 1)", "entry_id": "B", "date": "1-Jan-24", "account": "Account B",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
        ],
    }
    df = dd.priority_findings(report, limit=10)
    assert dd.priority_findings_concentration_note(df) is None


def test_priority_findings_concentration_note_identifies_repeated_account():
    report = {
        "ledger_leg_flags": [
            {"leg_key": "A (leg 1)", "entry_id": "A", "date": "1-Jan-24", "account": "Shared Account",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
            {"leg_key": "B (leg 1)", "entry_id": "B", "date": "1-Jan-24", "account": "Shared Account",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
            {"leg_key": "C (leg 1)", "entry_id": "C", "date": "1-Jan-24", "account": "Lone Account",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
        ],
    }
    df = dd.priority_findings(report, limit=10)
    note = dd.priority_findings_concentration_note(df)
    assert note is not None
    assert "Shared Account" in note
    assert "2" in note


def test_priority_findings_concentration_note_does_not_change_ranking():
    """The note is purely informational -- it must never reorder or drop
    rows from priority_findings()'s own output."""
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    before = dd.priority_findings(report, limit=10)
    dd.priority_findings_concentration_note(before)
    after = dd.priority_findings(report, limit=10)
    pd.testing.assert_frame_equal(before, after)


def test_real_segmentation_report_transforms_cleanly():
    """Against the real analyze_benford_segmentation.py output -- pins that
    Purchase/Contra come out as the real outliers (matches
    test_analyze_benford_segmentation.py's own pin of this)."""
    import analyze_benford_segmentation as abs_mod
    seg_report = abs_mod.run_segmentation_report(write_export=False)
    summary = dd.benford_segmentation_summary(seg_report)
    assert set(summary["outlier_segments"]) == {"Purchase", "Contra"}


# ---------- save_uploaded_file / list_raw_pdfs ----------

def test_save_uploaded_file_rejects_non_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Only PDF files"):
        dd.save_uploaded_file("summary.csv", b"not a pdf")


def test_save_uploaded_file_writes_to_raw_pdfs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(tmp_path))
    dest = dd.save_uploaded_file("gstr1_july_2026_redacted.pdf", b"%PDF-1.4 fake bytes")
    assert dest == str(tmp_path / "gstr1_july_2026_redacted.pdf")
    with open(dest, "rb") as f:
        assert f.read() == b"%PDF-1.4 fake bytes"


def test_save_uploaded_file_strips_directory_components(tmp_path, monkeypatch):
    """A filename shouldn't be able to escape RAW_PDFS_DIR via path
    components (e.g. '../../etc/passwd.pdf') -- os.path.basename() must
    strip them before joining."""
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(tmp_path))
    dest = dd.save_uploaded_file("../../evil.pdf", b"x")
    assert os.path.dirname(dest) == str(tmp_path)


def test_list_raw_pdfs_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(tmp_path))
    assert dd.list_raw_pdfs() == []


def test_list_raw_pdfs_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(tmp_path))
    (tmp_path / "b.pdf").write_bytes(b"x")
    (tmp_path / "a.pdf").write_bytes(b"x")
    assert dd.list_raw_pdfs() == ["a.pdf", "b.pdf"]


def test_list_raw_pdfs_missing_dir_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist"
    import data as dd_module
    old = dd_module.RAW_PDFS_DIR
    dd_module.RAW_PDFS_DIR = str(missing)
    try:
        assert dd_module.list_raw_pdfs() == []
    finally:
        dd_module.RAW_PDFS_DIR = old


# ---------- RULE_DISPLAY_NAMES (2026-10-02: dropped "Rule N" from the UI) ----------

def test_rule_display_names_cover_every_real_rule_code():
    """Every rule code that actually appears in the real report must have a
    human-readable name -- otherwise RULE_DISPLAY_NAMES.get(code, code)
    silently falls back to showing the raw code (e.g. "round_number_bias")
    in the UI, which is exactly the ambiguity this was built to remove.
    Catches a new rule being added to combined_report.py without its name
    being added here too."""
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    for code in dd.rule_options(report):
        assert code in dd.RULE_DISPLAY_NAMES, f"no display name for rule code {code!r}"


# ---------- ledger_flags_dataframe: amount/date fixes (2026-10-02) ----------

def test_ledger_flags_dataframe_amount_is_numeric(sample_report):
    df = dd.ledger_flags_dataframe(sample_report)
    assert df["amount"].dtype.kind == "f"
    assert list(df["amount"]) == [100000.0, 100000.0, 999999.0]


def test_ledger_flags_dataframe_amount_sorts_numerically_not_lexically():
    """Regression: amount used to be kept as the JSON's raw string, so
    sorting by it compared lexically -- "999.00" sorted ahead of
    "150000.00" because '9' > '1' as characters, even though
    999 < 150000 as numbers. Picked amounts here specifically differ in
    digit count so a lexical sort and a numeric sort disagree, unlike
    sample_report's amounts (which happen to have the same digit count)."""
    report = {
        "ledger_leg_flags": [
            {"leg_key": "A (leg 1)", "entry_id": "A", "date": "1-Jan-24",
             "account": "Cash", "amount": "999.00",
             "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "LOW"}],
             "signal_count": 1, "severity": "LOW"},
            {"leg_key": "B (leg 1)", "entry_id": "B", "date": "1-Jan-24",
             "account": "Cash", "amount": "150000.00",
             "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
        ],
    }
    df = dd.ledger_flags_dataframe(report)
    sorted_df = df.sort_values("amount", ascending=False, kind="stable")
    assert list(sorted_df["entry_id"]) == ["B", "A"]


def test_ledger_flags_dataframe_rule_display_names(sample_report):
    df = dd.ledger_flags_dataframe(sample_report)
    assert set(df["rule_display"]) == {"Round-Number Bias", "Duplicate Transaction"}
    assert "Rule" not in "".join(df["rule_display"])  # no leftover "Rule N" wording


def test_ledger_flags_dataframe_date_parsed(sample_report):
    df = dd.ledger_flags_dataframe(sample_report)
    assert str(df["date_parsed"].dtype).startswith("datetime64")
    assert list(df["date_parsed"].dt.strftime("%Y-%m-%d")) == [
        "2024-04-05", "2024-04-05", "2024-05-10",
    ]


def test_ledger_flags_dataframe_none_report_has_new_columns():
    df = dd.ledger_flags_dataframe(None)
    assert df.empty
    assert "rule_display" in df.columns
    assert "date_parsed" in df.columns


# ---------- priority_findings (voucher-level, 2026-10-02) ----------

def test_priority_findings_none_report():
    df = dd.priority_findings(None)
    assert df.empty
    assert list(df.columns) == [
        "entry_id", "severity", "accounts", "amount", "date", "checks", "why", "leg_count",
    ]


def test_priority_findings_groups_by_voucher_not_leg(multi_leg_voucher_report):
    """Receipt_182's two CRITICAL legs must collapse into ONE row (not
    two) -- this is the exact granularity fix the Overview KPIs already
    apply (voucher_severity_breakdown_df), carried over to the new
    findings list so it doesn't reintroduce the double-counting
    confusion."""
    df = dd.priority_findings(multi_leg_voucher_report, limit=10)
    assert len(df) == 2  # Receipt_182, Payment_9 -- not 4
    receipt_row = df[df["entry_id"] == "Receipt_182"].iloc[0]
    assert receipt_row["leg_count"] == 2
    assert receipt_row["severity"] == "CRITICAL"
    assert "A R CONSTRUCTION COMPANY" in receipt_row["accounts"]
    assert "J & K Bank Ltd" in receipt_row["accounts"]


def test_priority_findings_voucher_severity_is_max_of_its_legs(multi_leg_voucher_report):
    """Payment_9 has one MEDIUM leg and one HIGH leg -- the voucher-level
    row must come out HIGH (the max), not MEDIUM, same convention as
    voucher_severity_breakdown_df()."""
    df = dd.priority_findings(multi_leg_voucher_report, limit=10)
    payment_row = df[df["entry_id"] == "Payment_9"].iloc[0]
    assert payment_row["severity"] == "HIGH"
    assert payment_row["amount"] == 50000.0


def test_priority_findings_sorted_severity_then_amount():
    report = {
        "ledger_leg_flags": [
            {"leg_key": "A (leg 1)", "entry_id": "A", "date": "1-Jan-24", "account": "X",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "HIGH"}],
             "signal_count": 1, "severity": "HIGH"},
            {"leg_key": "B (leg 1)", "entry_id": "B", "date": "1-Jan-24", "account": "X",
             "amount": "9000000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "CRITICAL"}],
             "signal_count": 1, "severity": "CRITICAL"},
            {"leg_key": "C (leg 1)", "entry_id": "C", "date": "1-Jan-24", "account": "X",
             "amount": "20000000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "CRITICAL"}],
             "signal_count": 1, "severity": "CRITICAL"},
        ],
    }
    df = dd.priority_findings(report, limit=10)
    # Both CRITICAL legs rank above the HIGH one; within CRITICAL, the
    # larger amount (C, 2 crore) ranks above the smaller (B, 90 lakh).
    assert list(df["entry_id"]) == ["C", "B", "A"]


def test_priority_findings_respects_limit():
    report = {
        "ledger_leg_flags": [
            {"leg_key": f"V{i} (leg 1)", "entry_id": f"V{i}", "date": "1-Jan-24", "account": "X",
             "amount": "50000.00", "reasons": [{"rule": "round_number_bias", "reason": "r", "severity": "LOW"}],
             "signal_count": 1, "severity": "LOW"}
            for i in range(20)
        ],
    }
    assert len(dd.priority_findings(report, limit=5)) == 5


def test_priority_findings_real_data_row_count_matches_distinct_vouchers():
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    df = dd.priority_findings(report, limit=100000)
    assert len(df) == dd.distinct_voucher_count(report)


# ---------- voucher_legs / _leg_num_from_key / _related_entry_ids_from_reason ----------

def test_leg_num_from_key_parses_trailing_number():
    assert dd._leg_num_from_key("Receipt_20 (leg 2)") == 2
    assert dd._leg_num_from_key("Journal_699 (leg 14)") == 14


def test_leg_num_from_key_malformed_returns_none():
    assert dd._leg_num_from_key("not a leg key") is None
    assert dd._leg_num_from_key(None) is None


def test_related_entry_ids_from_reason_single_voucher():
    reason = "Duplicate transaction candidate: matches voucher(s) Receipt_19 on 5-Apr-24 (Dr J & K Bank Ltd Rs.100000.00)"
    assert dd._related_entry_ids_from_reason(reason) == ["Receipt_19"]


def test_related_entry_ids_from_reason_multiple_vouchers():
    reason = ("Duplicate transaction candidate: matches voucher(s) Journal_598, Journal_636 "
              "on 31-Mar-25 (Cr Hindustan Petroleum Corporation Limited Rs.100360.88)")
    assert dd._related_entry_ids_from_reason(reason) == ["Journal_598", "Journal_636"]


def test_related_entry_ids_from_reason_non_duplicate_reason_returns_empty():
    assert dd._related_entry_ids_from_reason("Suspiciously round amount (500000.0)") == []
    assert dd._related_entry_ids_from_reason("") == []


def test_related_entry_ids_from_reason_matches_every_real_duplicate_reason():
    """Validated against all real duplicate_transaction reasons in
    output/combined_report.json before this regex was relied on -- pinned
    here so a future wording change in rule_duplicate_transactions()
    (scripts/combined_report.py) that breaks the match is caught, instead
    of related-transaction lookups silently going quiet."""
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    dup_reasons = [
        r["reason"] for flag in report["ledger_leg_flags"] for r in flag["reasons"]
        if r["rule"] == "duplicate_transaction"
    ]
    assert dup_reasons, "expected at least one real duplicate_transaction reason"
    for reason in dup_reasons:
        assert dd._related_entry_ids_from_reason(reason), f"regex didn't match: {reason!r}"


def test_voucher_legs_missing_entry_id_returns_empty():
    assert dd.voucher_legs("NoSuchVoucher_999", rows=[]) == []


def test_voucher_legs_numbers_in_file_order():
    rows = [
        {"entry_id": "A", "account": "Cash"},
        {"entry_id": "B", "account": "Sales"},
        {"entry_id": "A", "account": "Bank"},
    ]
    legs = dd.voucher_legs("A", rows=rows)
    assert [leg["leg_num"] for leg in legs] == [1, 2]
    assert [leg["account"] for leg in legs] == ["Cash", "Bank"]


def test_voucher_legs_real_data_matches_known_voucher():
    """Receipt_20's two legs, and their order/accounts, are already pinned
    elsewhere in this project (the leg_key "Receipt_20 (leg 2)" ==
    J & K Bank Ltd) -- confirms voucher_legs()'s leg numbering lines up
    with combined_report.py's own leg_key convention on real data."""
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    legs = dd.voucher_legs("Receipt_20")
    if not legs:
        pytest.skip("data/general_ledger.csv has not been generated in this environment")
    assert [leg["leg_num"] for leg in legs] == [1, 2]
    assert legs[1]["account"] == "J & K Bank Ltd"


# ---------- voucher_detail ----------

def test_voucher_detail_none_report():
    assert dd.voucher_detail(None, "anything") is None


def test_voucher_detail_unknown_entry_id_returns_none():
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    assert dd.voucher_detail(report, "DefinitelyNotARealVoucher_123456") is None


def test_voucher_detail_real_data_round_number_voucher():
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    df = dd.priority_findings(report, limit=1)
    top_entry_id = df.iloc[0]["entry_id"]
    detail = dd.voucher_detail(report, top_entry_id)
    assert detail is not None
    assert detail["entry_id"] == top_entry_id
    assert detail["severity"] == df.iloc[0]["severity"]
    assert len(detail["flagged_legs"]) >= 1
    for fl in detail["flagged_legs"]:
        assert fl["raw_row"] is not None  # every flagged leg must join back to a real row
        assert fl["evidence"]


def test_voucher_detail_real_data_duplicate_voucher_has_related_transactions():
    report = dd.load_combined_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    dup_entry_id = next(
        (f["entry_id"] for f in report["ledger_leg_flags"]
         if any(r["rule"] == "duplicate_transaction" for r in f["reasons"])),
        None,
    )
    if dup_entry_id is None:
        pytest.skip("no duplicate_transaction flags in the real report")
    detail = dd.voucher_detail(report, dup_entry_id)
    assert detail["related_entry_ids"]
    assert detail["related_legs"]
    assert dup_entry_id not in detail["related_entry_ids"]  # never names itself as "related"


# ---------- pipeline_status ----------

def test_pipeline_status_none_report_nothing_marked_done():
    status = dd.pipeline_status(None, None)
    assert status["last_run"] is None
    assert status["gstr_last_run"] is None
    assert all(stage["done"] is False for stage in status["stages"] if stage["name"] in ("Detection", "Reconciliation", "Report"))


def test_pipeline_status_real_data_shape():
    report = dd.load_combined_report()
    gstr_report = dd.load_gstr_report()
    if report is None:
        pytest.skip("output/combined_report.json has not been generated in this environment")
    status = dd.pipeline_status(report, gstr_report)
    assert status["documents_processed"] == len(dd.list_raw_pdfs())
    assert [s["name"] for s in status["stages"]] == [
        "Extraction", "Reconstruction", "Normalization",
        "Detection", "Reconciliation", "Report",
    ]
    assert all(s["done"] for s in status["stages"])  # real pipeline has fully run
    assert status["last_run"] == report.get("generated_at")


# ---------- run_full_analysis ----------
# Added 2026-10-04: the generic "Re-run Analysis" orchestrator that
# replaced the sidebar's four dataset-specific buttons (see
# run_full_analysis()'s own docstring in dashboard/data.py). These pin
# its three-way classification of outcomes (ran / skipped / failed) --
# the behavior the sidebar actually depends on to tell "no GSTR documents
# this time" apart from "a real pipeline error" apart from "a missing
# optional dependency" -- without re-testing the four underlying
# run_*_pipeline() functions themselves (already exercised elsewhere/by
# the real-data integration this project runs against).

def test_run_full_analysis_real_data_all_four_run():
    """Against this project's own real, already-on-file documents, all
    four specialized pipelines have what they need -- end-to-end proof
    that the generic orchestrator reaches and correctly runs every one of
    them, not just a mocked subset."""
    if not os.path.exists(dd.RAW_PDFS_DIR):
        pytest.skip("data/raw_pdfs not present in this environment")
    result = dd.run_full_analysis()
    ran_names = {r["name"] for r in result["ran"]}
    assert ran_names == {
        "Tax & ledger reconciliation", "GST reconciliation",
        "Ledger analysis refresh", "Ledger vs GST reconciliation",
    }
    assert result["skipped"] == []
    assert result["failed"] == []


def test_run_full_analysis_missing_documents_is_skipped_not_failed(monkeypatch):
    """A pipeline whose required source documents simply aren't present
    (the ordinary case for a future user who, say, has no GSTR filings at
    all) must be reported as "skipped", never "failed" -- failed implies
    something is broken; skipped means this particular check doesn't
    apply yet. Monkeypatches ALL FOUR of _ANALYSIS_STEPS' underlying
    functions to isolate this from whatever real documents happen to be
    on file in this environment."""
    def _raise_not_found():
        raise FileNotFoundError("no source document found (simulated)")

    for name in (
        "run_combined_pipeline", "run_gstr_pipeline",
        "run_fy2025_26_pipeline", "run_ledger_gstr_pipeline",
    ):
        monkeypatch.setattr(dd, name, _raise_not_found)
    # _ANALYSIS_STEPS captured the original function objects at module
    # import time, so it must be rebuilt against the patched names too --
    # exactly what run_full_analysis() reads from.
    monkeypatch.setattr(dd, "_ANALYSIS_STEPS", [
        (name, getattr(dd, fn))
        for name, fn in zip(
            ["Tax & ledger reconciliation", "GST reconciliation",
             "Ledger analysis refresh", "Ledger vs GST reconciliation"],
            ["run_combined_pipeline", "run_gstr_pipeline",
             "run_fy2025_26_pipeline", "run_ledger_gstr_pipeline"],
        )
    ])

    result = dd.run_full_analysis()
    assert result["ran"] == []
    assert result["failed"] == []
    assert {s["name"] for s in result["skipped"]} == {
        "Tax & ledger reconciliation", "GST reconciliation",
        "Ledger analysis refresh", "Ledger vs GST reconciliation",
    }
    assert all("simulated" in s["reason"] for s in result["skipped"])


def test_run_full_analysis_missing_dependency_flagged_distinctly(monkeypatch):
    """The exact 2026-10-04 "No module named 'sklearn'" scenario: an
    ImportError must come back as a "failed" entry with
    is_dependency_error=True, distinct from an ordinary data/logic
    failure -- this is what lets the sidebar show it as a setup problem,
    never as an audit finding (see dashboard/app.py's sidebar, the
    "Setup issue, not an audit finding" message)."""
    def _raise_import_error():
        raise ModuleNotFoundError("No module named 'sklearn'")

    def _raise_value_error():
        raise ValueError("some genuine data problem (simulated)")

    monkeypatch.setattr(dd, "run_combined_pipeline", _raise_import_error)
    monkeypatch.setattr(dd, "run_gstr_pipeline", _raise_value_error)
    monkeypatch.setattr(dd, "_ANALYSIS_STEPS", [
        ("Tax & ledger reconciliation", dd.run_combined_pipeline),
        ("GST reconciliation", dd.run_gstr_pipeline),
    ])

    result = dd.run_full_analysis()
    assert len(result["failed"]) == 2
    by_name = {f["name"]: f for f in result["failed"]}
    assert by_name["Tax & ledger reconciliation"]["is_dependency_error"] is True
    assert "sklearn" in by_name["Tax & ledger reconciliation"]["error"]
    assert by_name["GST reconciliation"]["is_dependency_error"] is False


# ---------- load_fy2025_26_report / load_ledger_gstr_report ----------
# Added 2026-10-04 (dashboard finalization): these two loaders, and every
# transform below them, existed in output/ fully computed and tested, but
# had zero dashboard presence until this pass -- see
# BASELINE_CHECKPOINT_V3.14.md's finalization addendum.

def test_load_fy2025_26_report_missing_file_returns_none(tmp_path):
    assert dd.load_fy2025_26_report(path=str(tmp_path / "nope.json")) is None


def test_load_ledger_gstr_report_missing_file_returns_none(tmp_path):
    assert dd.load_ledger_gstr_report(path=str(tmp_path / "nope.json")) is None


def test_real_fy2025_26_report_loads_and_transforms_cleanly():
    """Same 'proof it flows through without raising' convention as
    test_real_combined_report_loads_and_transforms_cleanly() above, for
    the second real financial year's ledger-only report."""
    report = dd.load_fy2025_26_report()
    if report is None:
        pytest.skip("output/combined_report_FY2025-26.json has not been generated in this environment")

    kpis = dd.kpi_summary(report)
    assert kpis["total_legs"] > 0
    assert kpis["total_legs_flagged"] > 0

    ledger_df = dd.ledger_flags_dataframe(report)
    assert len(ledger_df) >= kpis["total_legs_flagged"]

    sev_df = dd.severity_breakdown_df(report)
    assert sev_df["count"].sum() == kpis["total_legs_flagged"]

    benford = dd.benford_summary(report)
    assert benford is not None
    assert benford["chi_square"] is not None

    gap_df = dd.voucher_gap_dataframe(report)
    assert isinstance(gap_df, pd.DataFrame)

    ml_df = dd.ml_anomaly_dataframe(report)
    assert isinstance(ml_df, pd.DataFrame)
    assert len(ml_df) > 0  # real FY2025-26 data has ML anomaly flags


def test_real_ledger_gstr_report_loads_and_transforms_cleanly():
    report = dd.load_ledger_gstr_report()
    if report is None:
        pytest.skip("output/ledger_gstr_reconciliation_report.json has not been generated in this environment")

    summary = dd.ledger_gstr_summary(report)
    assert summary["generated_at"] is not None
    assert len(summary["taxable_periods"]) == 12
    assert len(summary["non_gst_periods"]) == 12
    # The project's own pinned real-data result (see
    # rule_ledger_gstr_reconciliation.py's module docstring and
    # tests/test_rule_ledger_gstr_reconciliation.py): all 12 overlapping
    # months reconcile cleanly, to the rupee, on both taxable and
    # non-GST supply.
    assert summary["total_flagged"] == 0
    assert summary["flags_df"].empty


# ---------- ledger_gstr_summary ----------

def test_ledger_gstr_summary_none_report():
    s = dd.ledger_gstr_summary(None)
    assert s["generated_at"] is None
    assert s["taxable_periods"] == []
    assert s["non_gst_periods"] == []
    assert s["total_flagged"] == 0
    assert s["flags_df"].empty


def test_ledger_gstr_summary_shape(sample_ledger_gstr_report):
    s = dd.ledger_gstr_summary(sample_ledger_gstr_report)
    assert s["taxable_periods"] == ["April 2025-26", "May 2025-26"]
    assert s["non_gst_periods"] == ["April 2025-26", "May 2025-26"]
    assert s["total_flagged"] == 1
    assert s["by_severity"] == {"HIGH": 1}
    assert len(s["flags_df"]) == 1
    assert s["flags_df"].iloc[0]["check"] == "ledger_vs_gstr1_taxable_supply"


def test_ledger_gstr_summary_tolerates_stale_report_missing_non_gst_key():
    """Regression guard for the exact stale-JSON shape this project's own
    on-disk output/ledger_gstr_reconciliation_report.json had before this
    pass (written before the non-GST supply check was added to
    rule_ledger_gstr_reconciliation.py): dataset had
    ledger_periods_with_taxable_activity but no
    ledger_periods_with_non_gst_activity key at all. Must come back with
    an empty list there, not raise KeyError -- same convention
    benford_summary() already follows for an older-shaped stale report."""
    stale = {
        "generated_at": "2026-10-03T15:28:35",
        "dataset": {
            "general_ledger_file": "/x/general_ledger_FY2025-26.csv",
            "gstr1_file": "/x/gstr1_summary.csv",
            "ledger_periods_with_taxable_activity": ["April 2025-26"],
        },
        "flags": [],
        "summary": {"total_flagged": 0, "by_severity": {}},
    }
    s = dd.ledger_gstr_summary(stale)
    assert s["taxable_periods"] == ["April 2025-26"]
    assert s["non_gst_periods"] == []


# ---------- voucher_gap_dataframe (Rule 7) ----------

def test_voucher_gap_dataframe_none_report():
    df = dd.voucher_gap_dataframe(None)
    assert df.empty
    assert list(df.columns) == [
        "vch_type", "prefix", "missing_count", "group_min",
        "group_max", "group_size", "severity", "missing_numbers",
    ]


def test_voucher_gap_dataframe_joins_missing_numbers(sample_ledger_only_report):
    df = dd.voucher_gap_dataframe(sample_ledger_only_report)
    assert len(df) == 1
    assert df.iloc[0]["missing_numbers"] == "10, 20"
    assert df.iloc[0]["severity"] == "HIGH"


def test_voucher_gap_dataframe_real_data_both_ledgers():
    """Rule 7 has been computed and exported since before this pass, but
    had zero dashboard presence (not even for the existing FY2024-25
    tabs) until voucher_gap_dataframe() was added -- see
    BASELINE_CHECKPOINT_V3.14.md's finalization addendum."""
    report_2425 = dd.load_combined_report()
    report_2526 = dd.load_fy2025_26_report()
    if report_2425 is None or report_2526 is None:
        pytest.skip("combined reports not generated in this environment")
    for report in (report_2425, report_2526):
        df = dd.voucher_gap_dataframe(report)
        assert len(df) == len(report["voucher_sequence_gap_flags"])
        if not df.empty:
            assert set(df["severity"]) <= set(dd.SEVERITY_ORDER)


# ---------- ml_anomaly_dataframe / ml_anomaly_summary (Rule 8) ----------

def test_ml_anomaly_dataframe_none_report():
    df = dd.ml_anomaly_dataframe(None)
    assert df.empty


def test_ml_anomaly_dataframe_uppercases_severity(sample_ledger_only_report):
    """rule_ml_anomaly_detection.py emits lowercase severities ("high"/
    "medium"/"low"), unlike every other rule in this project -- this
    function must normalize to uppercase so SEVERITY_COLORS/SEVERITY_ORDER
    (both keyed uppercase) and every other severity column on this
    dashboard apply to it without special-casing."""
    df = dd.ml_anomaly_dataframe(sample_ledger_only_report)
    assert len(df) == 2
    assert set(df["severity"]) == {"HIGH", "MEDIUM"}
    assert df["amount"].dtype.kind == "f"  # cast from string, like ledger_flags_dataframe


def test_ml_anomaly_summary_counts_match_dataframe(sample_ledger_only_report):
    s = dd.ml_anomaly_summary(sample_ledger_only_report)
    assert s["total_flagged"] == 2
    assert s["by_severity"] == {"HIGH": 1, "MEDIUM": 1}
    assert len(s["flags_df"]) == 2


def test_ml_anomaly_summary_none_report():
    s = dd.ml_anomaly_summary(None)
    assert s["total_flagged"] == 0
    assert s["by_severity"] == {}
    assert s["flags_df"].empty


def test_ml_anomaly_dataframe_real_data_both_ledgers():
    """Pinned against this project's own documented real-data results (see
    BASELINE_CHECKPOINT_V3.14.md section 1.1): 731 flags for FY2024-25,
    699 for FY2025-26. Not re-pinning the exact counts here (those will
    legitimately shift if the pipeline is re-run with different/updated
    data) -- just confirming the real shape transforms cleanly and the
    severity values are the normalized uppercase set."""
    report_2425 = dd.load_combined_report()
    report_2526 = dd.load_fy2025_26_report()
    if report_2425 is None or report_2526 is None:
        pytest.skip("combined reports not generated in this environment")
    for report in (report_2425, report_2526):
        df = dd.ml_anomaly_dataframe(report)
        assert len(df) == len(report["ml_anomaly_flags"])
        assert len(df) > 0
        assert set(df["severity"]) <= {"HIGH", "MEDIUM", "LOW"}


# ---------- _load_ledger_rows / voucher_detail with an explicit ledger_path ----------
# Added 2026-10-04: a replacement voucher_detail(report, entry_id) with no
# ledger_path argument always reads GENERAL_LEDGER_PATH (FY2024-25) --
# correct for every existing call site, but wrong for the new FY2025-26
# tab, which must pass FY2025_26_GL_PATH explicitly. entry_ids are NOT
# unique across the two ledgers (confirmed below), so this isn't just a
# missing feature -- calling it without the right path silently joins
# against the WRONG financial year's row.

def test_load_ledger_rows_respects_explicit_path():
    if not os.path.exists(dd.FY2025_26_GL_PATH):
        pytest.skip("data/general_ledger_FY2025-26.csv not present in this environment")
    rows_2425 = dd._load_ledger_rows()  # default path
    rows_2526 = dd._load_ledger_rows(dd.FY2025_26_GL_PATH)
    assert rows_2425 != rows_2526
    assert len(rows_2526) > 0


def test_voucher_detail_with_explicit_ledger_path_finds_fy2025_26_voucher():
    report = dd.load_fy2025_26_report()
    if report is None or not os.path.exists(dd.FY2025_26_GL_PATH):
        pytest.skip("FY2025-26 report/ledger not present in this environment")
    df = dd.priority_findings(report, limit=1)
    if df.empty:
        pytest.skip("no FY2025-26 priority findings in this environment")
    entry_id = df.iloc[0]["entry_id"]

    detail = dd.voucher_detail(report, entry_id, ledger_path=dd.FY2025_26_GL_PATH)
    assert detail is not None
    assert detail["entry_id"] == entry_id
    assert len(detail["legs"]) > 0


def test_voucher_detail_entry_ids_are_not_unique_across_ledgers():
    """Documents the exact hazard the ledger_path parameter exists to
    prevent: the same entry_id can legitimately exist in BOTH real
    ledgers (they're independent documents, each with their own
    Payment_N/Journal_N/... numbering starting fresh), so calling
    voucher_detail() without the right ledger_path doesn't fail loudly --
    it silently returns a different, wrong voucher. If this test ever
    starts failing because the ledgers stop sharing any entry_id, that's
    good news for safety, not a bug -- it would just mean this particular
    regression guard no longer has a real example to demonstrate with."""
    report_2425 = dd.load_combined_report()
    report_2526 = dd.load_fy2025_26_report()
    if report_2425 is None or report_2526 is None or not os.path.exists(dd.FY2025_26_GL_PATH):
        pytest.skip("both combined reports/ledgers must be present for this test")

    fy2526_entry_ids = {f["entry_id"] for f in report_2526.get("ledger_leg_flags", [])}
    shared = [
        eid for eid in fy2526_entry_ids
        if dd.voucher_detail(report_2425, eid) is not None
    ]
    if not shared:
        pytest.skip("no shared entry_id between the two real ledgers right now")

    eid = shared[0]
    right = dd.voucher_detail(report_2526, eid, ledger_path=dd.FY2025_26_GL_PATH)
    wrong = dd.voucher_detail(report_2425, eid)  # default path -- the FY2024-25 ledger
    assert right is not None and wrong is not None
    # Same entry_id, two different real vouchers -- their raw dates/amounts
    # must not be assumed identical; the only thing this test pins is that
    # BOTH independently resolve (proving the path parameter actually
    # changes which file is read, not that the vouchers are related).
    assert right["legs"][0]["entry_id"] == eid
    assert wrong["legs"][0]["entry_id"] == eid


# ---------- run_full_ledger_reconstruction / reconstruct_full_document ----------
# Added 2026-10-04: reconstruct_full_document() had zero test coverage
# before this pass (see scripts/reconstruct_ledger_entries.py's own
# docstring for the full reasoning on why auto-detecting the page range
# is safe). These are deliberately real, slow (~45-50s each) end-to-end
# tests against the actual raw PDFs, not mocked -- the exact safety
# property being guarded (byte-identical output to the currently
# committed CSV) can only be checked against the real files, and this is
# also the project's only automated guard that the frozen FY2024-25
# baseline stays byte-for-byte untouched going forward.

import csv as _csv_module


def _read_csv_rows(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(_csv_module.DictReader(f))


@pytest.mark.slow
def test_run_full_ledger_reconstruction_frozen_baseline_byte_identical(tmp_path):
    info = dd.LEDGER_PDF_INFO["ledgers_redacted"]
    if not os.path.exists(info["pdf_path"]):
        pytest.skip("data/raw_pdfs/ledgers_redacted.pdf not present in this environment")
    committed_rows = _read_csv_rows(info["output_csv"])

    # Redirect the output_csv to a scratch path via a monkeypatched copy of
    # LEDGER_PDF_INFO so this test can NEVER write over the real, frozen
    # data/general_ledger.csv even if something above it is wrong --
    # belt-and-suspenders on top of the manual byte-identical verification
    # already done for this change (see BASELINE_CHECKPOINT_V3.14.md).
    import reconstruct_ledger_entries
    import adapt_ledger_to_schema
    recon_result = reconstruct_ledger_entries.reconstruct_full_document(info["pdf_path"])
    assert recon_result["total_pages"] == 926
    assert recon_result["total_vouchers"] == 6216
    assert recon_result["unbalanced_count"] == info["known_unbalanced"] == 2

    scratch_csv = str(tmp_path / "general_ledger_scratch.csv")
    adapt_ledger_to_schema.adapt_ledger(
        input_path=recon_result["entries_path"], output_path=scratch_csv,
    )
    assert _read_csv_rows(scratch_csv) == committed_rows


@pytest.mark.slow
def test_run_full_ledger_reconstruction_fy2025_26_byte_identical(tmp_path):
    info = dd.LEDGER_PDF_INFO["Ledgers_Anonymised"]
    if not os.path.exists(info["pdf_path"]):
        pytest.skip("data/raw_pdfs/Ledgers_Anonymised.pdf not present in this environment")
    committed_rows = _read_csv_rows(info["output_csv"])

    import reconstruct_ledger_entries
    import adapt_ledger_to_schema
    recon_result = reconstruct_ledger_entries.reconstruct_full_document(info["pdf_path"])
    assert recon_result["total_pages"] == 888
    assert recon_result["total_vouchers"] == 5947
    assert recon_result["unbalanced_count"] == info["known_unbalanced"] == 3

    scratch_csv = str(tmp_path / "general_ledger_FY2025-26_scratch.csv")
    adapt_ledger_to_schema.adapt_ledger(
        input_path=recon_result["entries_path"], output_path=scratch_csv,
    )
    assert _read_csv_rows(scratch_csv) == committed_rows


@pytest.mark.slow
def test_run_full_ledger_reconstruction_rejects_unknown_basename():
    with pytest.raises(KeyError):
        dd.run_full_ledger_reconstruction("not_a_real_pdf")


# ---------- Generic ledger-document genericization (2026-10-04) ----------
# Added for the dashboard's "upload a document, it gets tested" requirement:
# dashboard.data no longer finds ledger documents by a hardcoded filename
# dict -- discover_documents()/discover_ledger_documents() classify
# data/raw_pdfs/ by CONTENT (scripts/classify_document.py), and
# process_ledger_document() is the generic, never-raising entry point that
# reconstructs, confidence-scores, adapts, and reports on any ledger-shaped
# PDF. These tests cover the new functions directly; test_classify_document.py
# covers the classifier itself.

import reconstruct_ledger_entries as _rle


# ---- derive_period_label ----

def test_derive_period_label_exact_indian_fy():
    assert dd.derive_period_label("2025-04-01", "2026-03-31") == "FY2025-26"
    assert dd.derive_period_label("2024-04-01", "2025-03-31") == "FY2024-25"


def test_derive_period_label_non_fy_range():
    label = dd.derive_period_label("2025-06-15", "2025-09-20")
    assert label == "15 Jun 2025 to 20 Sep 2025"
    assert "/" not in label  # must always be filename-safe


def test_derive_period_label_missing_dates():
    assert dd.derive_period_label(None, None) == "Unknown period"
    assert dd.derive_period_label("2025-04-01", None) == "Unknown period"
    assert dd.derive_period_label(None, "2026-03-31") == "Unknown period"


def test_derive_period_label_malformed_dates_never_raises():
    assert dd.derive_period_label("not-a-date", "also-not-a-date") == "Unknown period"


# ---- ledger_slug / ledger_meta_path / ledger_output_csv_path ----

def test_ledger_slug_strips_extension_and_dir():
    assert dd.ledger_slug("/a/b/c/Ledgers_Anonymised.pdf") == "Ledgers_Anonymised"
    assert dd.ledger_slug("ledgers_redacted.pdf") == "ledgers_redacted"


def test_known_ledger_output_paths_matches_ledger_pdf_info():
    """KNOWN_LEDGER_OUTPUT_PATHS is derived FROM LEDGER_PDF_INFO (not a
    hand-duplicated copy) -- this pins that the two never drift apart."""
    for slug, info in dd.LEDGER_PDF_INFO.items():
        assert dd.KNOWN_LEDGER_OUTPUT_PATHS[slug] == info["output_csv"]


def test_ledger_output_csv_path_known_vs_generic():
    assert dd.ledger_output_csv_path(dd.LEDGER_PDF_INFO["Ledgers_Anonymised"]["pdf_path"]) \
        == dd.FY2025_26_GL_PATH
    generic = dd.ledger_output_csv_path("/x/y/some_brand_new_ledger.pdf")
    assert generic == os.path.join(dd.DATA_DIR, "general_ledger__some_brand_new_ledger.csv")


# ---- discover_documents / discover_ledger_documents ----

def test_discover_documents_classifies_real_raw_pdfs():
    docs = dd.discover_documents()
    if not docs:
        pytest.skip("data/raw_pdfs/ not present in this environment")
    names = {d["filename"] for d in docs}
    assert "ledgers_redacted.pdf" in names
    assert "Ledgers_Anonymised.pdf" in names
    for d in docs:
        assert d["doctype"] in dd.DOCTYPE_DISPLAY_NAMES  # every result is a real DOCTYPES member


def test_discover_ledger_documents_finds_both_known_ledgers():
    ledgers = dd.discover_ledger_documents()
    if not ledgers:
        pytest.skip("data/raw_pdfs/ not present in this environment")
    slugs = {dd.ledger_slug(d["path"]) for d in ledgers}
    assert {"ledgers_redacted", "Ledgers_Anonymised"} <= slugs


def test_discover_documents_empty_dir_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(tmp_path))
    assert dd.discover_documents() == []
    assert dd.discover_ledger_documents() == []


# ---- ledger_tab_info: graceful degradation for the two known documents ----

def test_ledger_tab_info_known_documents_graceful_degradation():
    """Neither real document has ever been run through
    process_ledger_document() (they predate it -- processed instead via
    the dashboard's existing run_full_ledger_reconstruction()/
    run_fy2025_26_pipeline() buttons), so no sidecar ledger_meta__*.json
    exists for them. ledger_tab_info() must still return a usable result
    by falling back to LEDGER_PDF_INFO's own established label/paths."""
    for slug, info in dd.LEDGER_PDF_INFO.items():
        if not os.path.exists(info["pdf_path"]) or not os.path.exists(info.get("report_path", "")):
            pytest.skip(f"{slug}: PDF or report not present in this environment")
        tab_info = dd.ledger_tab_info(info["pdf_path"])
        assert tab_info is not None
        assert tab_info["ok"] is True
        assert tab_info["label"] == info["label"]
        assert tab_info["report_path"] == info["report_path"]
        assert tab_info["output_csv"] == info["output_csv"]
        assert tab_info["confidence"] == "high"


def test_ledger_tab_info_unprocessed_document_returns_none(tmp_path):
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    path = tmp_path / "never_processed_ledger.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(50, 750, "Ledger Account")
    c.drawString(50, 735, "Date Particulars Vch Type Vch No. Debit Credit")
    c.save()
    assert dd.ledger_tab_info(str(path)) is None


# ---- document_library ----

def test_document_library_flags_known_and_ledger_docs_correctly():
    lib = dd.document_library()
    if not lib:
        pytest.skip("data/raw_pdfs/ not present in this environment")
    by_name = {row["filename"]: row for row in lib}
    if "ledgers_redacted.pdf" in by_name:
        row = by_name["ledgers_redacted.pdf"]
        assert row["is_ledger"] is True
        assert row["is_known"] is True
    if "trial_balance_redacted.pdf" in by_name:
        row = by_name["trial_balance_redacted.pdf"]
        assert row["is_ledger"] is False
        assert row["is_known"] is False
        assert row["is_processed"] is False


# ---- assess_reconstruction_confidence() (pure function, no I/O) ----

def _recon(total_vouchers, total_legs, unresolved_count, unbalanced_count):
    return {
        "total_vouchers": total_vouchers, "total_legs": total_legs,
        "unresolved_count": unresolved_count, "unbalanced_count": unbalanced_count,
    }


def test_assess_confidence_high_matches_real_baseline_shape():
    """Calibrated against the real FY2024-25 numbers (6,216 vouchers,
    2 unbalanced, 3.74% unresolved) -- comfortably under both thresholds."""
    recon = _recon(total_vouchers=6216, total_legs=14625, unresolved_count=547, unbalanced_count=2)
    result = _rle.assess_reconstruction_confidence(recon)
    assert result == {"confidence": "high", "reasons": []}


def test_assess_confidence_low_too_few_vouchers():
    recon = _recon(total_vouchers=5, total_legs=10, unresolved_count=0, unbalanced_count=0)
    result = _rle.assess_reconstruction_confidence(recon)
    assert result["confidence"] == "low"
    assert any("too few" in r for r in result["reasons"])


def test_assess_confidence_low_high_unresolved_rate():
    recon = _recon(total_vouchers=100, total_legs=1000, unresolved_count=200, unbalanced_count=0)
    result = _rle.assess_reconstruction_confidence(recon)
    assert result["confidence"] == "low"
    assert any("could not be attributed" in r for r in result["reasons"])


def test_assess_confidence_low_high_unbalanced_rate():
    recon = _recon(total_vouchers=100, total_legs=1000, unresolved_count=5, unbalanced_count=5)
    result = _rle.assess_reconstruction_confidence(recon)
    assert result["confidence"] == "low"
    assert any("don't balance" in r for r in result["reasons"])


def test_assess_confidence_can_fire_multiple_reasons_at_once():
    recon = _recon(total_vouchers=10, total_legs=20, unresolved_count=15, unbalanced_count=5)
    result = _rle.assess_reconstruction_confidence(recon)
    assert result["confidence"] == "low"
    assert len(result["reasons"]) >= 2  # too-few-vouchers AND high-unresolved both apply


# ---- process_ledger_document(): never raises, correct guard/rejection paths ----

def test_process_ledger_document_guards_frozen_baseline():
    frozen_path = dd.LEDGER_PDF_INFO["ledgers_redacted"]["pdf_path"]
    if not os.path.exists(frozen_path):
        pytest.skip("ledgers_redacted.pdf not present in this environment")
    result = dd.process_ledger_document(frozen_path)
    assert result["ok"] is False
    assert result["stage"] == "guard"
    # Must never have attempted (or completed) a reconstruction against the
    # frozen baseline through this generic path -- the whole point of the guard.
    assert "frozen" in result["error"].lower()


def test_process_ledger_document_rejects_non_ledger_document():
    tb_path = os.path.join(dd.RAW_PDFS_DIR, "trial_balance_redacted.pdf")
    if not os.path.exists(tb_path):
        pytest.skip("trial_balance_redacted.pdf not present in this environment")
    result = dd.process_ledger_document(tb_path)
    assert result["ok"] is False
    assert result["stage"] == "classify"
    assert result["doctype"] == "trial_balance"


def test_process_ledger_document_never_raises_on_garbage(tmp_path):
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"not a real pdf, just bytes")
    result = dd.process_ledger_document(str(path))
    assert result["ok"] is False
    assert result["stage"] == "classify"


def test_process_ledger_document_never_raises_on_missing_file():
    result = dd.process_ledger_document("/tmp/does_not_exist_at_all_98765.pdf")
    assert result["ok"] is False
    assert result["stage"] == "classify"


@pytest.mark.slow
def test_process_ledger_document_full_success_path_on_novel_synthetic_ledger(tmp_path):
    """End-to-end: a genuinely novel, never-before-seen ledger-shaped PDF
    (not one of the two established documents) goes all the way through
    classify -> reconstruct -> confidence -> adapt -> report -> sidecar
    metadata, entirely generically -- this is the exact scenario the user
    asked for ('upload a document and it gets tested'). Deliberately
    sparse (a single voucher) so this also exercises the LOW-confidence
    path (too few vouchers to trust the percentage checks), not just the
    happy path.

    Writes real files under this project's data/ and output/ dirs (same
    as a real upload would, since process_ledger_document() isn't
    parameterized by output directory) -- cleaned up in `finally` so this
    test never leaves stray committed-looking files behind."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    slug = "pytest_synthetic_sparse_ledger"
    pdf_path = tmp_path / f"{slug}.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.setFont("Helvetica", 9)
    c.drawString(50, 780, "Some Company Pvt Ltd")
    c.drawString(50, 765, "Ledger Account")
    c.drawString(50, 750, "Date Particulars Vch Type Vch No. Debit Credit")
    c.drawString(50, 730, "01-Apr-25 To Cash Receipt 1 5,000.00")
    c.save()

    produced_paths = [
        os.path.join(dd.DATA_DIR, f"{slug}_reconstructed_entries.csv"),
        os.path.join(dd.DATA_DIR, f"{slug}_reconstructed_unresolved.csv"),
        os.path.join(dd.DATA_DIR, f"{slug}_reconstructed_state_events.csv"),
        os.path.join(dd.DATA_DIR, f"general_ledger__{slug}.csv"),
        dd.ledger_meta_path(str(pdf_path)),
    ]
    try:
        result = dd.process_ledger_document(str(pdf_path))
        assert result["ok"] is True
        assert result["confidence"] == "low"  # 1 voucher is far below MIN_VOUCHERS_FOR_CONFIDENCE
        assert result["confidence_reasons"]
        assert os.path.exists(result["output_csv"])
        assert os.path.exists(result["report_path"])
        produced_paths.append(result["report_path"])
        # combined_report's own write_exports side-files, derived the same
        # way run_ledger_only_report() names them (see that function):
        suffix = result["report_label"].replace(" ", "_")
        for extra in ("ledger_leg_flags", "voucher_sequence_gap_flags", "ml_anomaly_flags"):
            produced_paths.append(os.path.join(dd.OUTPUT_DIR, f"{extra}_{suffix}.csv"))

        # The sidecar metadata is exactly what ledger_tab_info() will read back.
        tab_info = dd.ledger_tab_info(str(pdf_path))
        assert tab_info is not None
        assert tab_info["ok"] is True
        assert tab_info["confidence"] == "low"
    finally:
        for p in produced_paths:
            if os.path.exists(p):
                os.remove(p)


@pytest.mark.slow
def test_process_ledger_document_zero_vouchers_fails_cleanly_not_a_crash(tmp_path):
    """Regression test for a real bug found 2026-10-04 via a manual
    Playwright stress test of the upload flow (not a hypothetical): a PDF
    whose column headers are ledger-shaped enough to pass
    classify_document()/check_is_ledger_document() (so it gets this far),
    but whose body doesn't actually contain anything
    reconstruct_full_document() can pair into a complete voucher --
    reconstructing to 0 vouchers / 0 legs. Before the fix, this produced
    a 0-row adapted CSV, and combined_report.rule_benfords_law() /
    compute_mad() both divide by the row count -- so 'processing' this
    document raised a bare ZeroDivisionError from deep inside the rule
    engine, not the clean {"ok": False, "stage", "error"} this function
    promises for every other failure mode. Confirmed via
    reconstruct_full_document() directly against this same PDF: it
    returns total_vouchers=0, and assess_reconstruction_confidence()
    already classifies that as "low" with an explicit reason -- this test
    pins that the 0-voucher case is caught using that existing signal
    BEFORE adapt/report are attempted, not that the arithmetic was
    patched to tolerate it."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    slug = "pytest_zero_voucher_ledger"
    pdf_path = tmp_path / f"{slug}.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.setFont("Helvetica", 8)
    c.drawString(50, 780, "Date Particulars Vch Type Vch No Debit Credit")
    # Lines that match the ledger-header check but never resolve into a
    # complete, paired voucher -- no "To "/"By " particulars, no amounts
    # reconstruct_full_document() can tie together into a leg.
    y = 760
    for i in range(20):
        c.drawString(50, y, f"garbled unpaired line {i}")
        y -= 12
    c.save()

    produced_paths = [
        os.path.join(dd.DATA_DIR, f"{slug}_reconstructed_entries.csv"),
        os.path.join(dd.DATA_DIR, f"{slug}_reconstructed_unresolved.csv"),
        os.path.join(dd.DATA_DIR, f"{slug}_reconstructed_state_events.csv"),
        os.path.join(dd.DATA_DIR, f"general_ledger__{slug}.csv"),
        dd.ledger_meta_path(str(pdf_path)),
    ]
    try:
        # Sanity-check the premise directly: this PDF really does
        # reconstruct to 0 vouchers (not guessed -- if a future change to
        # reconstruct_ledger_entries.py makes this PDF resolve into any
        # vouchers at all, this test's premise no longer holds and it
        # should fail loudly here, not pass for the wrong reason).
        import reconstruct_ledger_entries as _rle
        recon = _rle.reconstruct_full_document(str(pdf_path))
        assert recon["total_vouchers"] == 0

        result = dd.process_ledger_document(str(pdf_path))
        assert result["ok"] is False
        assert result["stage"] == "reconstruct"
        assert "0 vouchers" in result["error"]
        assert result["confidence"] == "low"
        # The whole point: no adapted CSV or report ever gets written for
        # a 0-voucher document -- there's nothing in either to show.
        assert not os.path.exists(dd.ledger_output_csv_path(str(pdf_path)))
    finally:
        for p in produced_paths:
            if os.path.exists(p):
                os.remove(p)
