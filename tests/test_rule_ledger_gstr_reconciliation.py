"""
Tests for rule_ledger_gstr_reconciliation.py -- the first check in this
project that compares the general ledger directly against filed GSTR-1
data, made possible by the FY2025-26 ledger (V3.11) and the GSTR
All_Months parsing (V3.12) finally sharing 12 overlapping months (April
2025 - March 2026). See that module's docstring for the full rationale,
especially why the taxable-revenue account set is DERIVED structurally
(any Cr leg sharing a voucher with a CGST/SGST/IGST Cr leg, excluding
Journal-type settlement vouchers) rather than hardcoded by account name.

Like rule_gstr_reconciliation.py, the real data reconciles cleanly (that
IS the finding -- two independently-reconstructed documents agreeing),
so correctness is proven here with synthetic, answer-keyed discrepancies
for the mismatch-detection logic, and separately pinned against the real
data for the "does it actually derive the right numbers" question.
"""
import csv
import os

import pytest

import rule_ledger_gstr_reconciliation as lgr

DATA_DIR = lgr.DATA_DIR
GL_FILE = lgr.GL_FILE
GSTR1_FILE = lgr.GSTR1_FILE


def _leg(entry_id, date, account, dr_cr, amount, vch_type="Sales GST"):
    return {
        "entry_id": entry_id, "date": date, "account": account,
        "dr_cr": dr_cr, "amount": str(amount), "vch_type": vch_type,
    }


# ---------- _period_key ----------

def test_period_key_within_fy_after_april():
    assert lgr._period_key("27-Aug-25") == ("2025-26", "August")


def test_period_key_january_belongs_to_previous_aprils_fy():
    """Indian FY runs April-March, so Jan/Feb/Mar 2026 is still FY2025-26,
    not FY2026-27."""
    assert lgr._period_key("15-Jan-26") == ("2025-26", "January")


def test_period_key_april_starts_new_fy():
    assert lgr._period_key("1-Apr-25") == ("2025-26", "April")
    assert lgr._period_key("31-Mar-26") == ("2025-26", "March")


# ---------- derive_monthly_taxable_supply ----------

def test_derive_excludes_journal_vouchers():
    """A Journal-type GST-settlement voucher (Input CGST/SGST, Gst
    Payable) must never be counted as taxable revenue, even though it has
    CGST/SGST-shaped account names in it."""
    rows = [
        _leg("Journal_1", "10-Apr-25", "Input CGST", "Cr", 1000, vch_type="Journal"),
        _leg("Journal_1", "10-Apr-25", "Gst Payable", "Dr", 1000, vch_type="Journal"),
    ]
    result = lgr.derive_monthly_taxable_supply(rows)
    assert result == {}


def test_derive_sums_multiple_revenue_accounts_in_one_voucher_series():
    """Two different Cr revenue accounts (e.g. this company's "Sales GST"
    and "Pollution Checking Charges") both co-occurring with tax legs,
    across different vouchers in the same month, must both be counted --
    nothing hardcodes a single account name."""
    rows = [
        _leg("Sales GST_1", "5-Apr-25", "Sales GST", "Cr", 1000),
        _leg("Sales GST_1", "5-Apr-25", "CGST", "Cr", 90),
        _leg("Sales GST_1", "5-Apr-25", "SGST", "Cr", 90),
        _leg("Sales GST_1", "5-Apr-25", "Customer A", "Dr", 1180),
        _leg("Sales Cash_2", "6-Apr-25", "Pollution Checking Charges", "Cr", 500),
        _leg("Sales Cash_2", "6-Apr-25", "CGST", "Cr", 45),
        _leg("Sales Cash_2", "6-Apr-25", "SGST", "Cr", 45),
        _leg("Sales Cash_2", "6-Apr-25", "Cash", "Dr", 590),
    ]
    result = lgr.derive_monthly_taxable_supply(rows)
    bucket = result[("2025-26", "April")]
    assert bucket["taxable_value"] == pytest.approx(1500.0)
    assert bucket["cgst"] == pytest.approx(135.0)
    assert bucket["sgst"] == pytest.approx(135.0)
    assert bucket["igst"] == pytest.approx(0.0)


def test_derive_excludes_round_off_discount_and_vouchers_without_tax():
    rows = [
        # No tax leg at all -- not taxable supply (e.g. the non-GST fuel account)
        _leg("Sales GST_3", "7-Apr-25", "Sales", "Cr", 2000, vch_type="Sales GST"),
        # Has tax -- Round Off/Discount must not be counted as revenue
        _leg("Sales GST_4", "8-Apr-25", "Sales GST", "Cr", 1000),
        _leg("Sales GST_4", "8-Apr-25", "CGST", "Cr", 90),
        _leg("Sales GST_4", "8-Apr-25", "SGST", "Cr", 90),
        _leg("Sales GST_4", "8-Apr-25", "Round Off/Discount", "Cr", 0.04),
        _leg("Sales GST_4", "8-Apr-25", "Customer B", "Dr", 1180.04),
    ]
    result = lgr.derive_monthly_taxable_supply(rows)
    bucket = result[("2025-26", "April")]
    assert bucket["taxable_value"] == pytest.approx(1000.0)  # not 1000 + 2000 + 0.04


# ---------- derive_monthly_non_gst_supply ----------

def test_derive_non_gst_sums_sales_account_across_tax_free_vouchers():
    rows = [
        _leg("Sales GST_3", "7-Apr-25", "Sales", "Cr", 2000, vch_type="Sales GST"),
        _leg("Sales GST_3", "7-Apr-25", "Customer C", "Dr", 2000, vch_type="Sales GST"),
        _leg("Sales Cash_5", "9-Apr-25", "Sales", "Cr", 500, vch_type="Sales Cash"),
        _leg("Sales Cash_5", "9-Apr-25", "Cash", "Dr", 500, vch_type="Sales Cash"),
    ]
    result = lgr.derive_monthly_non_gst_supply(rows)
    assert result[("2025-26", "April")] == pytest.approx(2500.0)


def test_derive_non_gst_excludes_vouchers_with_a_tax_leg():
    """Mirror image of the taxable-supply test: a voucher WITH a tax leg is
    taxable supply, not non-GST supply, even if it happens to also use the
    "Sales" account name."""
    rows = [
        _leg("Sales GST_4", "8-Apr-25", "Sales", "Cr", 1000),
        _leg("Sales GST_4", "8-Apr-25", "CGST", "Cr", 90),
        _leg("Sales GST_4", "8-Apr-25", "SGST", "Cr", 90),
    ]
    assert lgr.derive_monthly_non_gst_supply(rows) == {}


def test_derive_non_gst_excludes_journal_vouchers():
    rows = [
        _leg("Journal_1", "10-Apr-25", "Sales", "Cr", 1000, vch_type="Journal"),
    ]
    assert lgr.derive_monthly_non_gst_supply(rows) == {}


def test_derive_non_gst_ignores_sale_named_accounts_in_other_voucher_types():
    """Non-GST supply comes from SALES-type vouchers, not from any account
    whose name contains "sale": the sample ledger's "Paytm Sale" is a
    collection account credited by Receipt vouchers (534 legs), and must
    not be swept in."""
    rows = [
        _leg("Receipt_6", "9-Apr-25", "Paytm Sale", "Cr", 300, vch_type="Receipt"),
        _leg("Payment_7", "9-Apr-25", "Sales", "Cr", 300, vch_type="Payment"),
    ]
    assert lgr.derive_monthly_non_gst_supply(rows) == {}


def test_derive_non_gst_works_for_a_differently_named_sales_account():
    """The old rule needed an account literally called "Sales"; another
    company's chart of accounts may call it anything."""
    rows = [
        _leg("Sales_1", "9-Apr-25", "Diesel Sales (Exempt)", "Cr", 800, vch_type="Sales"),
        _leg("Sales_1", "9-Apr-25", "Round Off/Discount", "Cr", 0.2, vch_type="Sales"),
        _leg("Sales_1", "9-Apr-25", "Cash", "Dr", 800.2, vch_type="Sales"),
    ]
    assert lgr.derive_monthly_non_gst_supply(rows) == {("2025-26", "April"): pytest.approx(800.0)}
    assert lgr.non_gst_revenue_accounts(rows) == ["Diesel Sales (Exempt)"]


# ---------- check_ledger_gstr_reconciliation (synthetic, answer-keyed) ----------

def _gstr1_row(period, financial_year, taxable_value, igst=0.0, cgst=0.0, sgst=0.0, non_gst_value=0.0):
    return {
        "financial_year": financial_year, "period": period,
        "taxable_value": str(taxable_value), "igst": str(igst),
        "cgst": str(cgst), "sgst": str(sgst), "non_gst_value": str(non_gst_value),
    }


def test_clean_matching_period_produces_no_flags():
    gl_rows = [
        _leg("Sales GST_1", "5-Apr-25", "Sales GST", "Cr", 1000),
        _leg("Sales GST_1", "5-Apr-25", "CGST", "Cr", 90),
        _leg("Sales GST_1", "5-Apr-25", "SGST", "Cr", 90),
    ]
    gstr1_rows = [_gstr1_row("April", "2025-26", 1000, cgst=90, sgst=90)]
    assert lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows) == []


def test_taxable_value_mismatch_flagged_with_correct_severity():
    gl_rows = [
        _leg("Sales GST_1", "5-Apr-25", "Sales GST", "Cr", 1000),
        _leg("Sales GST_1", "5-Apr-25", "CGST", "Cr", 90),
        _leg("Sales GST_1", "5-Apr-25", "SGST", "Cr", 90),
    ]
    # GSTR-1 reports a taxable value 50% higher than the ledger -> CRITICAL
    gstr1_rows = [_gstr1_row("April", "2025-26", 1500, cgst=90, sgst=90)]
    flags = lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows)
    taxable_flags = [f for f in flags if f["check"] == "ledger_vs_gstr1_taxable_supply"
                     and "Taxable value" in f["reason"]]
    assert len(taxable_flags) == 1
    assert taxable_flags[0]["severity"] == "CRITICAL"


def test_small_cgst_mismatch_flagged_medium():
    gl_rows = [
        _leg("Sales GST_1", "5-Apr-25", "Sales GST", "Cr", 1000),
        _leg("Sales GST_1", "5-Apr-25", "CGST", "Cr", 188),
        _leg("Sales GST_1", "5-Apr-25", "SGST", "Cr", 90),
    ]
    # Rs.12 gap on a Rs.200 reference = 6% -- above the Rs.10 tolerance
    # floor but below the 10% HIGH threshold.
    gstr1_rows = [_gstr1_row("April", "2025-26", 1000, cgst=200, sgst=90)]
    flags = lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows)
    cgst_flags = [f for f in flags if "CGST" in f["reason"]]
    assert len(cgst_flags) == 1
    assert cgst_flags[0]["severity"] == "MEDIUM"


def _sale(entry_id, date, taxable, vch_type="Sales GST"):
    half = round(taxable * 0.09, 2)
    return [
        _leg(entry_id, date, "Sales GST", "Cr", taxable, vch_type),
        _leg(entry_id, date, "CGST", "Cr", half, vch_type),
        _leg(entry_id, date, "SGST", "Cr", half, vch_type),
    ]


def test_ledger_month_missing_between_two_filed_months_is_flagged_critical():
    """A hole INSIDE the span of returns on file is a missing filing."""
    gl_rows = _sale("S1", "5-Apr-25", 1000) + _sale("S2", "5-May-25", 1000) + _sale("S3", "5-Jun-25", 1000)
    gstr1_rows = [
        _gstr1_row("April", "2025-26", 1000, cgst=90, sgst=90),
        _gstr1_row("June", "2025-26", 1000, cgst=90, sgst=90),
    ]
    flags = lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows)
    assert len(flags) == 1
    assert flags[0]["check"] == "ledger_activity_with_no_gstr1_filing"
    assert flags[0]["period"] == "May 2025-26"
    assert flags[0]["severity"] == "CRITICAL"


def test_no_gstr1_supplied_at_all_flags_nothing():
    """Not having uploaded a return is not evidence it was never filed."""
    gl_rows = _sale("S1", "5-Apr-25", 1000)
    assert lgr.check_ledger_gstr_reconciliation(gl_rows, []) == []


def test_months_outside_the_supplied_returns_are_not_flagged():
    gl_rows = _sale("S1", "5-Mar-25", 1000) + _sale("S2", "5-Apr-25", 1000) + _sale("S3", "5-Jul-25", 1000)
    gstr1_rows = [_gstr1_row("April", "2025-26", 1000, cgst=90, sgst=90)]
    assert lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows) == []
    cov = lgr.coverage(gl_rows, gstr1_rows)
    assert cov["compared"] == [("2025-26", "April")]
    assert cov["not_covered"] == [("2024-25", "March"), ("2025-26", "July")]
    assert cov["missing_inside_span"] == []


# ---------- recognising another company's account names ----------

@pytest.mark.parametrize("name,head", [
    ("CGST", "cgst"), ("Output CGST @ 9%", "cgst"), ("CGST Output", "cgst"),
    ("Central Tax", "cgst"), ("SGST", "sgst"), ("UTGST", "sgst"),
    ("State Tax 9%", "sgst"), ("IGST", "igst"), ("Integrated Tax 18%", "igst"),
    ("Input CGST", None), ("Input IGST", None), ("CGST Payable", None),
    ("CGST to Be Claimed", None), ("Interest on GST", None), ("Gst Late Fee", None),
    ("Sales GST", None), ("Gst Payable", None), ("", None), (None, None),
])
def test_tax_head_recognises_output_tax_accounts_only(name, head):
    assert lgr.tax_head(name) == head


@pytest.mark.parametrize("name,expected", [
    ("Round Off/Discount", True), ("Rounding", True), ("Round off (+)", True),
    ("Rounded Off", True), ("Sales", False), ("Surround Sound", False),
])
def test_is_rounding_account(name, expected):
    assert lgr.is_rounding_account(name) is expected


def test_sales_voucher_types():
    for t in ("Sales", "Sales GST", "Sales Cash"):
        assert lgr.is_sales_voucher_type(t)
    for t in ("Purchase", "Journal", "Receipt", "Payment", "Contra", "Debit Note", None):
        assert not lgr.is_sales_voucher_type(t)


def test_another_companys_account_names_reconcile_end_to_end():
    """Different names for revenue and tax accounts, plus a purchase-return
    voucher that credits a tax-named account (must not count as a sale)."""
    gl_rows = [
        _leg("Sales_1", "5-Apr-25", "Domestic Revenue", "Cr", 2000, "Sales"),
        _leg("Sales_1", "5-Apr-25", "Output CGST @9%", "Cr", 180, "Sales"),
        _leg("Sales_1", "5-Apr-25", "Output SGST @9%", "Cr", 180, "Sales"),
        _leg("Sales_1", "5-Apr-25", "Customer X", "Dr", 2360, "Sales"),
        # purchase return: credits the tax account but is not a sale
        _leg("Debit Note_1", "6-Apr-25", "Purchase Returns", "Cr", 500, "Debit Note"),
        _leg("Debit Note_1", "6-Apr-25", "CGST", "Cr", 45, "Debit Note"),
    ]
    gstr1_rows = [_gstr1_row("April", "2025-26", 2000, cgst=180, sgst=180)]
    assert lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows) == []
    monthly = lgr.derive_monthly_taxable_supply(gl_rows)[("2025-26", "April")]
    assert monthly["taxable_value"] == pytest.approx(2000.0)
    assert monthly["cgst"] == pytest.approx(180.0)


def test_voucher_with_unreadable_date_is_skipped_not_fatal():
    gl_rows = _sale("S1", "not a date", 1000) + _sale("S2", "5-Apr-25", 1000)
    monthly = lgr.derive_monthly_taxable_supply(gl_rows)
    assert list(monthly) == [("2025-26", "April")]


# ---------- run_ledger_gstr_report for an arbitrary ledger ----------

def _write_gl(path, rows):
    cols = ["entry_id", "date", "account", "dr_cr", "amount", "vch_type"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def test_run_report_for_an_uploaded_ledger(tmp_path):
    gl = tmp_path / "gl.csv"
    _write_gl(gl, _sale("S1", "5-Apr-25", 1000) + _sale("S2", "5-May-25", 1000))
    out = tmp_path / "report.json"
    gstr1 = [_gstr1_row("April", "2025-26", 1000, cgst=90, sgst=90),
             _gstr1_row("May", "2025-26", 1500, cgst=90, sgst=90)]
    result = lgr.run_ledger_gstr_report(
        gl_file=str(gl), gstr1_rows=gstr1, out_path=str(out),
        ledger_label="My ledger", gstr1_sources=["my_gstr1.pdf"],
    )
    assert result["status"] == "ok"
    assert result["dataset"]["ledger_label"] == "My ledger"
    assert result["dataset"]["gstr1_sources"] == ["my_gstr1.pdf"]
    assert result["dataset"]["periods_compared"] == ["April 2025-26", "May 2025-26"]
    assert [f["period"] for f in result["flags"]] == ["May 2025-26"]
    assert out.exists()


def test_run_report_status_explains_why_nothing_was_compared(tmp_path):
    gl = tmp_path / "gl.csv"
    _write_gl(gl, _sale("S1", "5-Apr-25", 1000))
    base = dict(gl_file=str(gl), write_export=False)
    assert lgr.run_ledger_gstr_report(gstr1_rows=[], **base)["status"] == "no_returns"
    later = [_gstr1_row("October", "2025-26", 1000, cgst=90, sgst=90)]
    r = lgr.run_ledger_gstr_report(gstr1_rows=later, **base)
    assert r["status"] == "no_overlap" and r["flags"] == []
    assert r["dataset"]["periods_not_covered_by_gstr1"] == ["April 2025-26"]
    empty = tmp_path / "empty.csv"
    _write_gl(empty, [_leg("J1", "5-Apr-25", "Cash", "Dr", 10, "Journal")])
    assert lgr.run_ledger_gstr_report(gl_file=str(empty), gstr1_rows=later, write_export=False)["status"] == "no_sales"


def test_period_with_no_ledger_activity_is_not_flagged():
    """A GSTR-1 period the ledger has no data for at all (outside its date
    range) is a coverage gap, not a discrepancy -- must not be flagged."""
    gstr1_rows = [_gstr1_row("July", "2026-27", 5000, cgst=450, sgst=450)]
    assert lgr.check_ledger_gstr_reconciliation([], gstr1_rows) == []


def test_clean_matching_non_gst_period_produces_no_flags():
    gl_rows = [
        _leg("Sales Cash_5", "9-Apr-25", "Sales", "Cr", 2500, vch_type="Sales Cash"),
    ]
    gstr1_rows = [_gstr1_row("April", "2025-26", 0, non_gst_value=2500)]
    assert lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows) == []


def test_non_gst_mismatch_flagged_with_correct_severity():
    gl_rows = [
        _leg("Sales Cash_5", "9-Apr-25", "Sales", "Cr", 2500, vch_type="Sales Cash"),
    ]
    # GSTR-1 reports non-GST value 50% higher than the ledger -> CRITICAL
    gstr1_rows = [_gstr1_row("April", "2025-26", 0, non_gst_value=3750)]
    flags = lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows)
    non_gst_flags = [f for f in flags if f["check"] == "ledger_vs_gstr1_non_gst_supply"]
    assert len(non_gst_flags) == 1
    assert non_gst_flags[0]["severity"] == "CRITICAL"


def test_taxable_and_non_gst_checks_are_independent_in_the_same_period():
    """A period can have a clean taxable match AND a non-GST mismatch (or
    vice versa) -- the two checks must not interfere with each other."""
    gl_rows = [
        _leg("Sales GST_1", "5-Apr-25", "Sales GST", "Cr", 1000),
        _leg("Sales GST_1", "5-Apr-25", "CGST", "Cr", 90),
        _leg("Sales GST_1", "5-Apr-25", "SGST", "Cr", 90),
        _leg("Sales Cash_5", "9-Apr-25", "Sales", "Cr", 2500, vch_type="Sales Cash"),
    ]
    gstr1_rows = [_gstr1_row("April", "2025-26", 1000, cgst=90, sgst=90, non_gst_value=9999)]
    flags = lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows)
    assert len(flags) == 1
    assert flags[0]["check"] == "ledger_vs_gstr1_non_gst_supply"


# ---------- Real data ----------

@pytest.fixture(scope="module")
def real_data_available():
    if not os.path.exists(GL_FILE) or not os.path.exists(GSTR1_FILE):
        pytest.skip("Real FY2025-26 ledger or GSTR-1 summary not present")


def test_real_data_derives_exactly_12_overlapping_months(real_data_available):
    with open(GL_FILE, encoding="utf-8") as f:
        gl_rows = list(csv.DictReader(f))
    monthly = lgr.derive_monthly_taxable_supply(gl_rows)
    assert len(monthly) == 12
    assert ("2025-26", "April") in monthly
    assert ("2025-26", "March") in monthly


def test_real_data_derives_exactly_12_overlapping_non_gst_months(real_data_available):
    """Pinned alongside the header-detection bug fix (reconstruct_ledger_
    entries.py): before that fix, the "Sales" account's Dr/Cr pattern was
    not understood and this reconciliation was deliberately left out (see
    module docstring). After the fix, "Sales" has zero Dr legs anywhere in
    the ledger and derives cleanly across the same 12 months as taxable
    supply."""
    with open(GL_FILE, encoding="utf-8") as f:
        gl_rows = list(csv.DictReader(f))
    assert not any(
        r["account"] == lgr.NON_GST_REVENUE_ACCOUNT and r["dr_cr"] == "Dr" for r in gl_rows
    )
    monthly = lgr.derive_monthly_non_gst_supply(gl_rows)
    assert len(monthly) == 12
    assert ("2025-26", "April") in monthly
    assert ("2025-26", "March") in monthly


def test_real_data_reconciles_cleanly(real_data_available):
    """The headline result: the independently-reconstructed ledger and
    the independently-parsed GSTR-1 data agree to the rupee on taxable
    value, CGST, SGST, AND non-GST (Table 8 / fuel) supply across all 12
    overlapping months -- both the taxable-supply reconciliation and the
    non-GST reconciliation that the header-detection bug fix made possible
    (see module docstring)."""
    with open(GL_FILE, encoding="utf-8") as f:
        gl_rows = list(csv.DictReader(f))
    with open(GSTR1_FILE, encoding="utf-8") as f:
        gstr1_rows = list(csv.DictReader(f))
    assert lgr.check_ledger_gstr_reconciliation(gl_rows, gstr1_rows) == []


def test_real_data_non_gst_matches_gstr1_exactly_every_month(real_data_available):
    """Direct, explicit pin of the exact figures (not just "no flags"):
    the ledger's derived non-GST supply equals GSTR-1's non_gst_value to
    the paisa on all 12 months, with zero exceptions."""
    with open(GL_FILE, encoding="utf-8") as f:
        gl_rows = list(csv.DictReader(f))
    with open(GSTR1_FILE, encoding="utf-8") as f:
        gstr1_rows = list(csv.DictReader(f))
    ledger_non_gst = lgr.derive_monthly_non_gst_supply(gl_rows)
    gstr1_by_period = {(r["financial_year"], r["period"]): float(r["non_gst_value"]) for r in gstr1_rows}
    assert len(ledger_non_gst) == 12
    for key, ledger_amt in ledger_non_gst.items():
        assert key in gstr1_by_period
        assert ledger_amt == pytest.approx(gstr1_by_period[key], abs=0.01)


def test_run_ledger_gstr_report_shape_and_real_data(real_data_available, tmp_path, monkeypatch):
    monkeypatch.setattr(lgr, "OUTPUT_DIR", str(tmp_path))
    result = lgr.run_ledger_gstr_report(write_export=True)

    assert len(result["dataset"]["ledger_periods_with_taxable_activity"]) == 12
    assert len(result["dataset"]["ledger_periods_with_non_gst_activity"]) == 12
    assert result["flags"] == []
    assert result["summary"] == {"total_flagged": 0, "by_severity": {}}

    exported_path = tmp_path / "ledger_gstr_reconciliation_report.json"
    assert exported_path.exists()
