"""
Regression suite for scripts/adapt_gstr_to_schema.py.

UPDATED 2026-10-03: this module now covers TWO real-data shapes --
  1. One-off single-month PDFs (gstr1_<month>_<year>_redacted.pdf /
     gstr3b_<month>_<year>_redacted.pdf) -- the original shape, still on
     file as gstr1_june_2026_redacted.pdf / gstr3b_june_2026_redacted.pdf.
  2. Multi-month "All_Months" PDFs (GSTR1_All_Months_Anonymised.pdf /
     GSTR3B_All_Months_Anonymised.pdf), newly added, covering 16 months
     (April 2025 - July 2026, FY2025-26 and FY2026-27) in one PDF each.
The June 2026-27 period exists in BOTH shapes (the single-month PDF is a
duplicate of what's also inside the All_Months upload, confirmed by
identical ARN dates) -- adapt_gstr_docs() must merge the two sources and
drop that duplicate rather than double-count or silently prefer one
without saying so (see _dedup_by_period's docstring).

Investigating the 16 real months surfaced two real findings, both pinned
below rather than only described:
  - FIX: GSTR-1's "taxable_value"/"igst"/"cgst"/"sgst"/"cess" are derived
    from the return's own "Total Liability" line (minus Table 8's non-GST
    value, for the value column only), not from Table 7 (B2CS) alone.
    Quantified directly against the PDFs: on 15 of 16 months Table 7 is
    the only nonzero taxable-supply table, so the two approaches agree to
    the rupee; on the 16th (June 2025-26) a nonzero Table 4/B2B entry
    makes Table 7 alone under-count GSTR-3B's Table 3.1(a) by exactly the
    B2B amount (Rs.76,271.18 value / Rs.6,864.41 each of CGST+SGST). The
    Total-Liability-derived figure matches GSTR-3B exactly on all 16
    months, including this one -- see test_extract_gstr1_b2b_month below.
  - FIX: pdfplumber occasionally glues a stray capital letter onto an
    all-zero Table 6.1 cell with no surrounding whitespace at all (seen
    as "0.00L0.00", always on the all-zero "Integrated tax" row) -- found
    in exactly 2 of the 16 real GSTR-3B months (April 2025-26, July
    2026-27) and nowhere else in either form. _extract_payment_row's
    column separator (_COL_SEP) tolerates one such stray letter; see
    test_extract_gstr3b_tolerates_stray_letter_artifact below.
"""
import glob
import os

import pytest

import adapt_gstr_to_schema as adapt

RAW_PDFS_DIR = adapt.RAW_PDFS_DIR
GSTR1_ALL_MONTHS = os.path.join(RAW_PDFS_DIR, "GSTR1_All_Months_Anonymised.pdf")
GSTR3B_ALL_MONTHS = os.path.join(RAW_PDFS_DIR, "GSTR3B_All_Months_Anonymised.pdf")


def _skip_if_missing(*paths):
    for p in paths:
        if not os.path.exists(p):
            pytest.skip(f"Real PDF not present: {p}")


# ---------- Single-month path (original shape) ----------

@pytest.fixture(scope="module")
def real_gstr1_paths():
    paths = sorted(glob.glob(adapt.GSTR1_GLOB))
    if not paths:
        pytest.skip(f"No GSTR-1 PDFs present matching {adapt.GSTR1_GLOB}")
    return paths


@pytest.fixture(scope="module")
def real_gstr3b_paths():
    paths = sorted(glob.glob(adapt.GSTR3B_GLOB))
    if not paths:
        pytest.skip(f"No GSTR-3B PDFs present matching {adapt.GSTR3B_GLOB}")
    return paths


def test_exactly_one_single_month_filing_pair_on_file(real_gstr1_paths, real_gstr3b_paths):
    """Documents the current state: one one-off single-month pair (June
    2026-27), separate from the 16-month All_Months upload. If this ever
    fails because another one-off month was added, that's good news --
    update this test rather than silently raising the expected count."""
    assert len(real_gstr1_paths) == 1
    assert len(real_gstr3b_paths) == 1
    assert "june_2026" in real_gstr1_paths[0]
    assert "june_2026" in real_gstr3b_paths[0]


def test_extract_gstr1_real_figures(real_gstr1_paths):
    row = adapt.extract_gstr1(real_gstr1_paths[0])
    assert row["financial_year"] == "2026-27"
    assert row["period"] == "June"
    assert row["arn_date"] == "06/07/2026"
    # Table 7 (B2CS) is this month's only populated taxable-supply table,
    # so the Total-Liability-derived figure equals the old Table-7-only
    # one to the rupee.
    assert float(row["taxable_value"]) == pytest.approx(92970.96, abs=0.01)
    assert float(row["b2cs_taxable_value"]) == pytest.approx(92970.96, abs=0.01)
    assert float(row["igst"]) == pytest.approx(0.00, abs=0.01)
    assert float(row["cgst"]) == pytest.approx(8367.35, abs=0.01)
    assert float(row["sgst"]) == pytest.approx(8367.35, abs=0.01)
    assert float(row["cess"]) == pytest.approx(0.00, abs=0.01)
    assert float(row["non_gst_value"]) == pytest.approx(40990353.95, abs=0.01)
    # Total Liability = taxable + non-GST, to the rupee
    assert float(row["total_liability_value"]) == pytest.approx(
        92970.96 + 40990353.95, abs=0.01
    )
    assert float(row["total_liability_cgst"]) == pytest.approx(8367.35, abs=0.01)
    assert float(row["total_liability_sgst"]) == pytest.approx(8367.35, abs=0.01)


def test_extract_gstr3b_real_figures(real_gstr3b_paths):
    row = adapt.extract_gstr3b(real_gstr3b_paths[0])
    assert row["financial_year"] == "2026-27"
    assert row["period"] == "June"
    assert row["arn_date"] == "17/07/2026"
    assert float(row["taxable_value"]) == pytest.approx(92970.96, abs=0.01)
    assert float(row["cgst"]) == pytest.approx(8367.35, abs=0.01)
    assert float(row["sgst"]) == pytest.approx(8367.35, abs=0.01)
    assert float(row["non_gst_value"]) == pytest.approx(40990353.95, abs=0.01)
    # Table 6.1: net payable 8367.00 (rounded), fully paid in cash, zero ITC
    assert float(row["cgst_payable"]) == pytest.approx(8367.00, abs=0.01)
    assert float(row["cgst_paid_total"]) == pytest.approx(8367.00, abs=0.01)
    assert float(row["sgst_payable"]) == pytest.approx(8367.00, abs=0.01)
    assert float(row["sgst_paid_total"]) == pytest.approx(8367.00, abs=0.01)
    assert float(row["igst_payable"]) == pytest.approx(0.00, abs=0.01)
    assert float(row["igst_paid_total"]) == pytest.approx(0.00, abs=0.01)


def test_gstr1_and_gstr3b_taxable_figures_match(real_gstr1_paths, real_gstr3b_paths):
    """The real cross-check that makes 'zero mismatch' genuine rather than
    assumed: GSTR-1's reported taxable total and GSTR-3B's Table 3.1(a)
    independently report the identical taxable value and tax figures for
    June 2026."""
    gstr1 = adapt.extract_gstr1(real_gstr1_paths[0])
    gstr3b = adapt.extract_gstr3b(real_gstr3b_paths[0])
    for field in ("taxable_value", "igst", "cgst", "sgst", "cess", "non_gst_value"):
        assert float(gstr1[field]) == pytest.approx(float(gstr3b[field]), abs=0.01), field


# ---------- Multi-month "All_Months" path ----------

def test_extract_gstr1_all_months_finds_16_months():
    _skip_if_missing(GSTR1_ALL_MONTHS)
    rows = adapt.extract_gstr1_all_months(GSTR1_ALL_MONTHS)
    assert len(rows) == 16
    periods = [(r["financial_year"], r["period"]) for r in rows]
    assert periods[0] == ("2025-26", "April")
    assert periods[-1] == ("2026-27", "July")
    assert len(set(periods)) == 16  # no two months collapsed into one


def test_extract_gstr3b_all_months_finds_16_months():
    _skip_if_missing(GSTR3B_ALL_MONTHS)
    rows = adapt.extract_gstr3b_all_months(GSTR3B_ALL_MONTHS)
    assert len(rows) == 16
    periods = [(r["financial_year"], r["period"]) for r in rows]
    assert periods[0] == ("2025-26", "April")
    assert periods[-1] == ("2026-27", "July")
    assert len(set(periods)) == 16


def test_extract_gstr1_b2b_month():
    """Pins the real finding that drove the taxable_value fix: June
    2025-26 has a nonzero Table 4 (B2B) entry alongside Table 7 (B2CS),
    so the reported taxable total must be their SUM, not Table 7 alone."""
    _skip_if_missing(GSTR1_ALL_MONTHS)
    rows = adapt.extract_gstr1_all_months(GSTR1_ALL_MONTHS)
    june = next(r for r in rows if (r["financial_year"], r["period"]) == ("2025-26", "June"))
    assert float(june["b2cs_taxable_value"]) == pytest.approx(90813.99, abs=0.01)
    assert float(june["taxable_value"]) == pytest.approx(167085.17, abs=0.01)  # B2CS + B2B
    assert float(june["b2cs_cgst"]) == pytest.approx(8173.21, abs=0.01)
    assert float(june["cgst"]) == pytest.approx(15037.62, abs=0.01)
    assert float(june["sgst"]) == pytest.approx(15037.62, abs=0.01)


def test_extract_gstr3b_tolerates_stray_letter_artifact():
    """Pins the pdfplumber text-extraction quirk found on exactly 2 of the
    16 real months: a stray capital letter glued onto the all-zero
    Integrated-tax row with no surrounding whitespace ("0.00L0.00").
    Both affected months must still extract cleanly, with every Integrated
    figure correctly read as zero (not dropped, not misparsed)."""
    _skip_if_missing(GSTR3B_ALL_MONTHS)
    rows = adapt.extract_gstr3b_all_months(GSTR3B_ALL_MONTHS)
    for fy, period in [("2025-26", "April"), ("2026-27", "July")]:
        row = next(r for r in rows if (r["financial_year"], r["period"]) == (fy, period))
        assert float(row["igst_payable"]) == pytest.approx(0.00, abs=0.01)
        assert float(row["igst_paid_total"]) == pytest.approx(0.00, abs=0.01)


def test_all_16_months_reconcile_taxable_value_cleanly():
    """With the taxable_value fix in place, every one of the 16 real
    months' GSTR-1 and GSTR-3B figures should agree to the rupee -- this
    is 16 independent real cross-checks, not one."""
    _skip_if_missing(GSTR1_ALL_MONTHS, GSTR3B_ALL_MONTHS)
    gstr1_rows = adapt.extract_gstr1_all_months(GSTR1_ALL_MONTHS)
    gstr3b_rows = adapt.extract_gstr3b_all_months(GSTR3B_ALL_MONTHS)
    gstr3b_by_period = {(r["financial_year"], r["period"]): r for r in gstr3b_rows}
    assert len(gstr1_rows) == len(gstr3b_rows) == 16
    for g1 in gstr1_rows:
        g3b = gstr3b_by_period[(g1["financial_year"], g1["period"])]
        for field in ("taxable_value", "igst", "cgst", "sgst", "cess", "non_gst_value"):
            assert float(g1[field]) == pytest.approx(float(g3b[field]), abs=0.01), (
                g1["period"], g1["financial_year"], field
            )


# ---------- Merging both shapes, with dedup ----------

def test_adapt_gstr_docs_merges_and_dedups(tmp_path):
    """Running the real adapter against ALL real PDFs (single-month +
    All_Months) must produce exactly 16 rows per output -- the June
    2026-27 single-month PDF is a duplicate of what the All_Months upload
    already has (same ARN dates) and must be dropped, not appended as a
    17th row -- and must reproduce exactly what's committed in
    data/gstr1_summary.csv / data/gstr3b_summary.csv."""
    out_gstr1 = tmp_path / "gstr1_summary.csv"
    out_gstr3b = tmp_path / "gstr3b_summary.csv"
    gstr1_rows, gstr3b_rows = adapt.adapt_gstr_docs(
        out_gstr1_path=str(out_gstr1), out_gstr3b_path=str(out_gstr3b)
    )

    assert len(gstr1_rows) == 16
    assert len(gstr3b_rows) == 16
    assert len({(r["financial_year"], r["period"]) for r in gstr1_rows}) == 16
    assert len({(r["financial_year"], r["period"]) for r in gstr3b_rows}) == 16

    with open(adapt.OUTPUT_GSTR1, encoding="utf-8") as f:
        committed_gstr1 = f.read()
    with open(out_gstr1, encoding="utf-8") as f:
        fresh_gstr1 = f.read()
    assert committed_gstr1 == fresh_gstr1, (
        "data/gstr1_summary.csv no longer matches a fresh run of the "
        "adapter against the real PDFs"
    )

    with open(adapt.OUTPUT_GSTR3B, encoding="utf-8") as f:
        committed_gstr3b = f.read()
    with open(out_gstr3b, encoding="utf-8") as f:
        fresh_gstr3b = f.read()
    assert committed_gstr3b == fresh_gstr3b, (
        "data/gstr3b_summary.csv no longer matches a fresh run of the "
        "adapter against the real PDFs"
    )


def test_dedup_by_period_keeps_first_seen():
    rows = [
        {"financial_year": "2026-27", "period": "June", "arn_date": "all_months_copy"},
        {"financial_year": "2026-27", "period": "July", "arn_date": "unique"},
        {"financial_year": "2026-27", "period": "June", "arn_date": "single_month_copy"},
    ]
    deduped = adapt._dedup_by_period(rows, "GSTR-1")
    assert len(deduped) == 2
    assert deduped[0]["arn_date"] == "all_months_copy"  # first-seen wins
    assert deduped[1]["period"] == "July"


# ---------- Segmentation helper ----------

def test_segment_month_blocks_raises_on_no_markers():
    import re
    with pytest.raises(ValueError, match="No month boundaries found"):
        adapt._segment_month_blocks(["no marker here", "still nothing"], re.compile(r"NEVER MATCHES"))


def test_segment_month_blocks_splits_on_each_marker_page():
    import re
    pages = ["Tax period April\npage 1 content", "page 2", "Tax period May\npage 3"]
    blocks = adapt._segment_month_blocks(pages, re.compile(r"Tax period\s+\w+"))
    assert len(blocks) == 2
    assert "April" in blocks[0] and "page 2" in blocks[0]
    assert "May" in blocks[1] and "page 3" in blocks[1]
