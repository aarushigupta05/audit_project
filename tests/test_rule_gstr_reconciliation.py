"""
Tests for rule_gstr_reconciliation.py.

Includes stress-test injections against the REAL extracted GSTR-1/GSTR-3B
data (same approach as test_rule_stress_injection.py's Rules 2/4/5): the
one real month on file reconciles cleanly, so there's no real anomaly to
detect, and this rule's correctness has to be proven with synthetic,
answer-keyed discrepancies instead -- see rule_gstr_reconciliation.py's
module docstring for why.
"""
import csv
import os

import pytest

from rule_gstr_reconciliation import check_gstr_reconciliation, classify_severity, run_gstr_report
import adapt_gstr_to_schema as adapt


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


def _gstr1_row(period="June", financial_year="2026-27", taxable_value=92970.96,
                igst=0.0, cgst=8367.35, sgst=8367.35, cess=0.0,
                non_gst_value=40990353.95):
    return {
        "financial_year": financial_year, "period": period,
        "taxable_value": str(taxable_value), "igst": str(igst),
        "cgst": str(cgst), "sgst": str(sgst), "cess": str(cess),
        "non_gst_value": str(non_gst_value),
    }


def _gstr3b_row(period="June", financial_year="2026-27", taxable_value=92970.96,
                igst=0.0, cgst=8367.35, sgst=8367.35, cess=0.0,
                non_gst_value=40990353.95, cgst_payable=8367.00,
                cgst_paid_total=8367.00, sgst_payable=8367.00,
                sgst_paid_total=8367.00, igst_payable=0.0, igst_paid_total=0.0):
    return {
        "financial_year": financial_year, "period": period,
        "taxable_value": str(taxable_value), "igst": str(igst),
        "cgst": str(cgst), "sgst": str(sgst), "cess": str(cess),
        "non_gst_value": str(non_gst_value),
        "cgst_payable": str(cgst_payable), "cgst_paid_total": str(cgst_paid_total),
        "sgst_payable": str(sgst_payable), "sgst_paid_total": str(sgst_paid_total),
        "igst_payable": str(igst_payable), "igst_paid_total": str(igst_paid_total),
    }


def test_clean_matching_period_produces_no_flags():
    gstr1 = [_gstr1_row()]
    gstr3b = [_gstr3b_row()]
    assert check_gstr_reconciliation(gstr1, gstr3b) == []


def test_taxable_value_mismatch_flagged():
    gstr1 = [_gstr1_row(taxable_value=100000.00)]
    gstr3b = [_gstr3b_row(taxable_value=92970.96)]
    flags = check_gstr_reconciliation(gstr1, gstr3b)
    taxable_flags = [f for f in flags if "Taxable value" in f["reason"]]
    assert len(taxable_flags) == 1
    assert taxable_flags[0]["check"] == "gstr1_vs_gstr3b_taxable_supplies"
    # mismatch = 100000 - 92970.96 = 7029.04; pct of reference (92970.96) ~7.56% -> MEDIUM
    assert taxable_flags[0]["severity"] == "MEDIUM"


def test_cgst_mismatch_flagged_critical_at_large_gap():
    gstr1 = [_gstr1_row(cgst=20000.00)]
    gstr3b = [_gstr3b_row(cgst=8367.35)]
    flags = check_gstr_reconciliation(gstr1, gstr3b)
    cgst_flags = [f for f in flags if "CGST" in f["reason"]]
    assert len(cgst_flags) == 1
    assert cgst_flags[0]["severity"] == "CRITICAL"


def test_non_gst_value_mismatch_flagged():
    gstr1 = [_gstr1_row(non_gst_value=40000000.00)]
    gstr3b = [_gstr3b_row(non_gst_value=40990353.95)]
    flags = check_gstr_reconciliation(gstr1, gstr3b)
    non_gst_flags = [f for f in flags if f["check"] == "gstr1_vs_gstr3b_non_gst_supplies"]
    assert len(non_gst_flags) == 1


def test_missing_gstr3b_for_filed_gstr1_flagged_critical():
    gstr1 = [_gstr1_row(period="July")]
    gstr3b = [_gstr3b_row(period="June")]
    flags = check_gstr_reconciliation(gstr1, gstr3b)
    missing_flags = [f for f in flags if f["check"] == "missing_filing"]
    assert len(missing_flags) == 1
    assert missing_flags[0]["severity"] == "CRITICAL"


def test_payment_shortfall_flagged():
    """CGST payable Rs.8367 but only Rs.5000 actually paid -- a real,
    unpaid liability on an already-filed return."""
    gstr1 = [_gstr1_row()]
    gstr3b = [_gstr3b_row(cgst_payable=8367.00, cgst_paid_total=5000.00)]
    flags = check_gstr_reconciliation(gstr1, gstr3b)
    shortfall_flags = [f for f in flags if f["check"] == "gstr3b_payment_completeness"]
    assert len(shortfall_flags) == 1
    assert "CGST" in shortfall_flags[0]["reason"]
    # shortfall 3367 / payable 8367 ~ 40% -> CRITICAL
    assert shortfall_flags[0]["severity"] == "CRITICAL"


def test_payment_fully_settled_not_flagged():
    gstr1 = [_gstr1_row()]
    gstr3b = [_gstr3b_row()]  # payable == paid_total for every head
    flags = check_gstr_reconciliation(gstr1, gstr3b)
    assert [f for f in flags if f["check"] == "gstr3b_payment_completeness"] == []


# ---------- Stress-test injections against the REAL extracted data ----------
# (see module docstring: the one real month reconciles cleanly, so these
# synthetic injections are what actually prove the rule detects a
# discrepancy, the same validation approach used for Rules 2/4/5.)

@pytest.fixture(scope="module")
def real_gstr1_row():
    import glob
    paths = sorted(glob.glob(adapt.GSTR1_GLOB))
    if not paths:
        pytest.skip(f"No GSTR-1 PDFs present matching {adapt.GSTR1_GLOB}")
    return adapt.extract_gstr1(paths[0])


@pytest.fixture(scope="module")
def real_gstr3b_row():
    import glob
    paths = sorted(glob.glob(adapt.GSTR3B_GLOB))
    if not paths:
        pytest.skip(f"No GSTR-3B PDFs present matching {adapt.GSTR3B_GLOB}")
    return adapt.extract_gstr3b(paths[0])


def test_real_data_reconciles_cleanly(real_gstr1_row, real_gstr3b_row):
    """The real baseline this rule runs against today: zero flags. Not
    much of a stress test by itself, but it's the fact that makes every
    injected-discrepancy test below meaningful -- a rule that flagged
    everything would also "pass" a test full of only clean data."""
    assert check_gstr_reconciliation([real_gstr1_row], [real_gstr3b_row]) == []


def test_stress_injection_taxable_value_inflated_in_gstr1(real_gstr1_row, real_gstr3b_row):
    """Answer key: GSTR-1 taxable value inflated by Rs.50,000 over the real
    GSTR-3B figure (92,970.96 -> 142,970.96) -- a 53.8% gap on the
    reference (GSTR-3B's 92,970.96) -> CRITICAL."""
    injected = dict(real_gstr1_row)
    injected["taxable_value"] = str(float(real_gstr1_row["taxable_value"]) + 50000.00)
    flags = check_gstr_reconciliation([injected], [real_gstr3b_row])
    taxable_flags = [f for f in flags if "Taxable value" in f["reason"]]
    assert len(taxable_flags) == 1
    assert taxable_flags[0]["severity"] == "CRITICAL"
    # Real ledger-side data (there is none fed in here) is untouched --
    # only the one injected period should appear at all.
    assert {f["period"] for f in flags} == {"June 2026-27"}


def test_stress_injection_sgst_understated_in_gstr3b(real_gstr1_row, real_gstr3b_row):
    """Answer key: GSTR-3B SGST understated by Rs.1,000 against the real
    GSTR-1 figure (8,367.35 -> 7,367.35) -- mismatch 1000 / reference
    7367.35 ~ 13.6% -> HIGH."""
    injected = dict(real_gstr3b_row)
    injected["sgst"] = str(float(real_gstr3b_row["sgst"]) - 1000.00)
    flags = check_gstr_reconciliation([real_gstr1_row], [injected])
    sgst_flags = [f for f in flags if "SGST" in f["reason"] and f["check"] == "gstr1_vs_gstr3b_taxable_supplies"]
    assert len(sgst_flags) == 1
    assert sgst_flags[0]["severity"] == "HIGH"


def test_stress_injection_cgst_underpaid(real_gstr1_row, real_gstr3b_row):
    """Answer key: real CGST payable (Rs.8,367.00) with only Rs.4,000
    actually paid -- shortfall 4367 / payable 8367 ~ 52% -> CRITICAL."""
    injected = dict(real_gstr3b_row)
    injected["cgst_paid_total"] = "4000.00"
    flags = check_gstr_reconciliation([real_gstr1_row], [injected])
    shortfall_flags = [f for f in flags if f["check"] == "gstr3b_payment_completeness"
                        and "CGST" in f["reason"]]
    assert len(shortfall_flags) == 1
    assert shortfall_flags[0]["severity"] == "CRITICAL"


def test_run_gstr_report_shape_and_real_data(tmp_path, monkeypatch):
    """ADDED 2026-10-02 for the dashboard's JSON data source: pins
    run_gstr_report()'s result shape and that it reproduces the real,
    clean baseline (0 flags) -- same dataset test_real_data_reconciles_cleanly
    above already covers, just via the export wrapper this time.

    UPDATED 2026-10-03: real data now covers 16 months (April 2025 - July
    2026, via the GSTR All_Months PDFs merged with the one-off June
    2026-27 PDF, deduped) -- was 1 month when this test was first written."""
    import rule_gstr_reconciliation as rgr
    monkeypatch.setattr(rgr, "OUTPUT_DIR", str(tmp_path))
    result = rgr.run_gstr_report(write_export=True)

    assert result["dataset"]["gstr1_periods"] == 16
    assert result["dataset"]["gstr3b_periods"] == 16
    assert len(result["dataset"]["periods"]) == 16
    assert "June 2026-27" in result["dataset"]["periods"]
    assert "April 2025-26" in result["dataset"]["periods"]
    assert result["flags"] == []
    assert result["summary"] == {"total_flagged": 0, "by_severity": {}}

    exported_path = tmp_path / "gstr_report.json"
    assert exported_path.exists()
    import json
    with open(exported_path) as f:
        on_disk = json.load(f)
    assert on_disk["flags"] == []


# ---------- origin-aware use: pairing, "not compared", per-set reports ----------

import rule_gstr_reconciliation as rg


def test_pairing_lists_shared_and_one_sided_months_in_calendar_order():
    g1 = [_gstr1_row("May", "2025-26"), _gstr1_row("April", "2025-26"), _gstr1_row("January", "2025-26")]
    g3 = [_gstr3b_row("April", "2025-26"), _gstr3b_row("February", "2025-26")]
    p = rg.pairing(g1, g3)
    assert p["compared"] == [("2025-26", "April")]
    assert p["only_gstr1"] == [("2025-26", "May"), ("2025-26", "January")]
    assert p["only_gstr3b"] == [("2025-26", "February")]


def test_default_still_flags_a_gstr1_month_with_no_gstr3b_as_missing_filing():
    """The original behaviour (the sample set), pinned."""
    flags = check_gstr_reconciliation([_gstr1_row("May", "2025-26")], [])
    assert [f["check"] for f in flags] == ["missing_filing"]


def test_uploaded_mode_does_not_turn_a_missing_document_into_a_filing_failure():
    flags = check_gstr_reconciliation([_gstr1_row("May", "2025-26")], [], missing_is_filing_gap=False)
    assert flags == []


def test_uploaded_mode_still_compares_months_present_in_both():
    g1 = [_gstr1_row("May", "2025-26", taxable_value=1000)]
    g3 = [_gstr3b_row("May", "2025-26", taxable_value=2000)]
    flags = check_gstr_reconciliation(g1, g3, missing_is_filing_gap=False)
    assert any(f["check"] == "gstr1_vs_gstr3b_taxable_supplies" for f in flags)


def test_run_report_for_a_given_set_records_what_was_compared(tmp_path):
    g1 = [_gstr1_row("April", "2025-26"), _gstr1_row("May", "2025-26")]
    g3 = [_gstr3b_row("April", "2025-26")]
    out = tmp_path / "r.json"
    r = run_gstr_report(gstr1_rows=g1, gstr3b_rows=g3, out_path=str(out), label="Mine",
                        gstr1_sources=["a.pdf"], gstr3b_sources=["b.pdf"],
                        warnings=["x.pdf: unreadable"], missing_is_filing_gap=False)
    d = r["dataset"]
    assert r["status"] == "ok" and r["flags"] == [] and out.exists()
    assert d["label"] == "Mine" and d["gstr1_sources"] == ["a.pdf"] and d["gstr3b_sources"] == ["b.pdf"]
    assert d["periods_compared"] == ["April 2025-26"]
    assert d["periods_only_in_gstr1"] == ["May 2025-26"] and d["periods_only_in_gstr3b"] == []
    assert d["warnings"] == ["x.pdf: unreadable"]


def test_run_report_status_says_why_nothing_was_compared(tmp_path):
    base = dict(write_export=False, missing_is_filing_gap=False)
    assert run_gstr_report(gstr1_rows=[], gstr3b_rows=[_gstr3b_row("May", "2025-26")], **base)["status"] == "no_gstr1"
    assert run_gstr_report(gstr1_rows=[_gstr1_row("May", "2025-26")], gstr3b_rows=[], **base)["status"] == "no_gstr3b"
    r = run_gstr_report(gstr1_rows=[_gstr1_row("May", "2025-26")],
                        gstr3b_rows=[_gstr3b_row("June", "2025-26")], **base)
    assert r["status"] == "no_overlap" and r["flags"] == []
