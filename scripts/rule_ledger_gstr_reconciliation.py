"""
RULE: Ledger vs GSTR-1 Taxable-Supply Reconciliation
-------------------------------------------------------
Every other GST-related check in this project (rule_gstr_reconciliation.py)
compares GSTR-1 against GSTR-3B for the SAME period -- it has never been
able to check either against the general ledger itself, because no
ledger period on file overlapped any filed GSTR period. That changed when
the FY2025-26 ledger was reconstructed and adapted (see
BASELINE_CHECKPOINT_V3.11.md) alongside 16 months of real GSTR-1/GSTR-3B
data (V3.12): the ledger's dates (1-Apr-25 to 9-Mar-26) cover 12 of those
16 months (April 2025 - March 2026, FY2025-26) exactly. This rule is the
new check that overlap makes possible: does the ledger's own recorded
taxable supply match what was actually filed?

Deriving "taxable supply" from the ledger WITHOUT hardcoding account
names:
A chart of accounts is company-specific -- a different ledger could call
its GST-attracting revenue account anything. Rather than hardcode this
company's account names (which happen to be "Sales GST" and "Pollution
Checking Charges" -- confirmed by direct inspection, see
BASELINE_CHECKPOINT_V3.12.md), this rule derives the taxable-revenue
account SET structurally: any credit leg that shares a voucher with a
CGST/SGST/IGST credit leg is, by definition, part of that voucher's
taxable supply -- that is what "attracts GST" means. The only exclusion
needed is Tally's own standard "Journal" voucher type, used here (as is
conventional) for period-end GST liability set-off/adjustment entries
(Input CGST/SGST/IGST, GST Payable) that are bookkeeping, not sales --
confirmed directly: every Cr leg that co-occurs with CGST/SGST/IGST
across this ledger belongs to exactly one of {"Sales GST" voucher-type
sales, "Pollution Checking Charges" sales, or a "Journal" settlement
entry}, verified by inspecting the complete set before writing this rule,
not assumed. "Round Off/Discount" (a few paise of rounding per voucher)
is excluded by name since it is never itself a sold item.

Verification before accepting this approach: computed this way, the
ledger's monthly taxable value, CGST and SGST match gstr1_summary.csv's
corresponding fields EXACTLY (to the rupee) on all 12 overlapping months
-- see tests/test_rule_ledger_gstr_reconciliation.py. This is a strong
result: it means the FY2025-26 ledger reconstruction (V3.11) and the
GSTR-1 All_Months extraction (V3.12) are independently correct, cross-
validated against each other on 12 separate months, using documents from
two completely different sources.

Non-GST (fuel/petrol-diesel, Table 8) supply IS also reconciled here, as
of the header-detection bug investigation: the ledger's "Sales" account
(the non-taxable fuel revenue account, by the same mirror-image logic
Table 7/Table 8 split on in GSTR-1) used to appear with BOTH Dr and Cr
legs in a pattern that looked structural (dealer/credit-party
arrangements, returns?) and summed naively, was off from GSTR-1's
non_gst_value by roughly 5-8x -- too large to be rounding, not
understood, and deliberately left unreconciled rather than guessed at
(see BASELINE_CHECKPOINT_V3.12.md). That pattern turned out to be
entirely an artifact of a page-header-detection bug in the ledger
reconstruction itself (reconstruct_ledger_entries.py's Shape 2 parsing,
fixed investigating this exact account): under the bug, ~92% of the
ledger's occurrences had a wrong or dropped account label, and a large
number of OTHER accounts' legs were being silently mislabeled as "Sales".
Once that bug was fixed, "Sales" has ZERO Dr legs across the entire
ledger (2,145 occurrences, all Cr) -- exactly the clean shape a pure
revenue account should have -- and its Cr total matches GSTR-1's
non_gst_value EXACTLY (to the rupee) on all 12 overlapping months, with
zero exceptions. Derived the same way as taxable supply: every Cr leg
posted to the "Sales" account in a voucher that has NO CGST/SGST/IGST leg
(the mirror-image condition to taxable supply's "has a tax leg").

Generalised to any company's ledger (V3.14 Addendum 19):
This rule used to be tied to the FY2025-26 sample: fixed input files, the
exact account names "CGST"/"SGST"/"IGST"/"Sales"/"Round Off/Discount", and
a CRITICAL flag for every ledger month that had no GSTR-1 on file. It now
works on whatever ledger and GSTR-1 rows it is given:
  * Tax accounts are recognised by name pattern (tax_head()): "CGST",
    "Output CGST @9%", "Central Tax", "UTGST", "Integrated Tax" ... but
    never input-credit/payable/claim accounts. Rounding accounts likewise
    (is_rounding_account()).
  * Non-GST supply no longer needs an account literally called "Sales":
    it is the credit side of a SALES-type voucher (voucher type contains
    "sale") that carries no tax leg, minus rounding. Checked against the
    sample before adopting it: it selects exactly the same 2,145 "Sales"
    legs and reproduces GSTR-1's non-GST value to the paisa in all 12
    months. (A name test such as "contains 'sale'" was tried and rejected:
    it would have swept in the "Paytm Sale" collection account, 534 legs
    of Receipt vouchers.)
  * A ledger month with no GSTR-1 is flagged only when it falls INSIDE the
    span of months that returns were supplied for (a hole between two
    filed months). Months outside that span -- or every month, when no
    GSTR-1 was supplied at all -- are reported as "not covered", not as a
    missing filing: not having uploaded a return is not evidence that it
    was never filed.
  * run_ledger_gstr_report() can take GSTR-1 rows directly and write to a
    caller-chosen path, so the dashboard can run it once per uploaded
    ledger (see dashboard/data.py run_ledger_gstr_pipeline()).
The checks the result describes are still the ones above: the comparison is
only as good as the ledger's account naming, which is why the result lists
the accounts it treated as non-GST sales.
"""

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "output")
GL_FILE = os.path.join(DATA_DIR, "general_ledger_FY2025-26.csv")
GSTR1_FILE = os.path.join(DATA_DIR, "gstr1_summary.csv")

TOLERANCE_RUPEES = 10.0  # same convention as rule_gstr_reconciliation.py

# The canonical account names this project's own ledger adapter produces.
# Kept for reference/back-compat: tax_head() below is what the derivation
# actually uses, and it recognises these plus other companies' spellings.
TAX_ACCOUNTS = ("CGST", "SGST", "IGST")
# Never itself a sold item -- a few paise of invoice rounding, excluded by
# name (unlike the tax accounts, there is no structural way to derive
# "this is a rounding bucket" the way co-occurrence derives revenue).
NON_REVENUE_CR_ACCOUNTS = {"Round Off/Discount"}

_ROUNDING_RE = re.compile(r"round(?:ing|ed)?[\s\-/]*off|rounding", re.I)
# Accounts that carry a tax name but are NOT output tax on a sale: input
# credit, liability/receivable balances, claims, penalties, refunds.
_NOT_OUTPUT_TAX_RE = re.compile(
    r"input|payable|receivable|claim|credit|itc|reverse|rcm|late\s*fee|"
    r"interest|penalt|refund|paid|reversal", re.I)
_HEAD_PATTERNS = (
    ("cgst", re.compile(r"\bcgst\b|\bcentral\s*tax\b", re.I)),
    ("sgst", re.compile(r"\bsgst\b|\butgst\b|\bstate(?:/ut)?\s*tax\b", re.I)),
    ("igst", re.compile(r"\bigst\b|\bintegrated\s*tax\b", re.I)),
)
# Voucher types that are never a sale even if they carry a tax-named leg:
# Tally's Journal (period-end GST set-off), purchases and their returns,
# payments, contra.
_NON_SALES_TYPE_RE = re.compile(r"^\s*journal\s*$|purchase|debit\s*note|payment|contra", re.I)


def tax_head(account):
    """'cgst' / 'sgst' / 'igst' for an OUTPUT tax account, else None.
    'CGST', 'Output CGST @9%', 'Central Tax' -> 'cgst'; 'Input CGST',
    'CGST Payable', 'CGST to Be Claimed', 'Interest on GST' -> None."""
    name = str(account or "")
    if _NOT_OUTPUT_TAX_RE.search(name):
        return None
    for head, pattern in _HEAD_PATTERNS:
        if pattern.search(name):
            return head
    return None


def is_rounding_account(account):
    """A few paise of invoice rounding -- never itself a sold item."""
    return bool(_ROUNDING_RE.search(str(account or "")))


def is_sales_voucher_type(vch_type):
    """Tally's sales voucher types ('Sales', 'Sales GST', 'Sales Cash', ...)."""
    name = str(vch_type or "")
    return "sale" in name.lower() and not _NON_SALES_TYPE_RE.search(name)


def _counts_as_sale_voucher(vch_type):
    return not _NON_SALES_TYPE_RE.search(str(vch_type or ""))
# REFERENCE ONLY since Addendum 19 -- derive_monthly_non_gst_supply() no
# longer looks for this account by name (see "Generalised" in the module
# docstring); it is kept because the sample ledger's pinned tests assert
# against it. The original reasoning follows.
# The non-GST (fuel/petrol-diesel, Table 8) revenue account. Unlike
# taxable supply, there is no co-occurring tax leg to derive this
# structurally from (that is precisely what makes it non-GST) -- named by
# direct inspection instead, the same convention this module already uses
# for "Sales GST" and "Pollution Checking Charges" in the taxable case.
# Confirmed before relying on it: after the header-detection bug fix (see
# module docstring), "Sales" has zero Dr legs anywhere in the ledger (2,145
# occurrences, all Cr) -- the clean shape of a pure revenue account, not a
# suspense/contra account that would need netting.
NON_GST_REVENUE_ACCOUNT = "Sales"

_MONTH_NUM = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}


def _period_key(date_str):
    """'27-Aug-25' -> ('2025-26', 'August') -- Indian financial year runs
    April-March, so a date in Jan/Feb/Mar belongs to the FY that STARTED
    the previous calendar year."""
    dt = datetime.strptime(date_str.strip(), "%d-%b-%y")
    month_name = dt.strftime("%B")
    year = dt.year
    if dt.month >= 4:
        fy = f"{year}-{str(year + 1)[2:]}"
    else:
        fy = f"{year - 1}-{str(year)[2:]}"
    return fy, month_name


def _group_vouchers(gl_rows):
    by_voucher = defaultdict(list)
    for row in gl_rows:
        by_voucher[row["entry_id"]].append(row)
    return by_voucher


def _voucher_period(legs):
    """(financial_year, month) of a voucher, or None when its date cannot be
    read -- such a voucher is left out of the monthly sums rather than
    stopping the whole comparison (run_ledger_gstr_report() counts them)."""
    try:
        return _period_key(legs[0]["date"])
    except (ValueError, KeyError, AttributeError):
        return None


def derive_monthly_taxable_supply(gl_rows):
    """Groups ledger rows into vouchers, keeps only sale-like vouchers
    (not Journal/Purchase/Payment/...) that carry at least one output-tax
    credit leg (CGST/SGST/IGST -- see tax_head()), and sums every OTHER
    credit leg in that voucher (its taxable revenue, excluding rounding)
    plus the tax legs themselves, per (financial_year, period). See module
    docstring for why this derives the revenue-account set structurally
    instead of hardcoding it.

    Returns {(financial_year, period): {"taxable_value", "igst", "cgst",
    "sgst"}}, all floats.
    """
    monthly = defaultdict(lambda: {"taxable_value": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0})

    for entry_id, legs in _group_vouchers(gl_rows).items():
        if not legs or not _counts_as_sale_voucher(legs[0].get("vch_type")):
            continue
        tax_legs = [(tax_head(l["account"]), l) for l in legs if l["dr_cr"] == "Cr"]
        tax_legs = [(head, l) for head, l in tax_legs if head]
        if not tax_legs:
            continue

        key = _voucher_period(legs)
        if key is None:
            continue
        bucket = monthly[key]
        for head, l in tax_legs:
            bucket[head] += float(l["amount"])
        for l in legs:
            if (l["dr_cr"] == "Cr" and not tax_head(l["account"])
                    and not is_rounding_account(l["account"])):
                bucket["taxable_value"] += float(l["amount"])

    return dict(monthly)


def non_gst_revenue_accounts(gl_rows):
    """The accounts credited by sales-type vouchers that carry no tax leg
    (what derive_monthly_non_gst_supply sums) -- listed in the report so a
    reader can see exactly what was treated as non-GST sales."""
    accounts = set()
    for legs in _group_vouchers(gl_rows).values():
        if not legs or not is_sales_voucher_type(legs[0].get("vch_type")):
            continue
        if any(l["dr_cr"] == "Cr" and tax_head(l["account"]) for l in legs):
            continue
        for l in legs:
            if l["dr_cr"] == "Cr" and not is_rounding_account(l["account"]):
                accounts.add(l["account"])
    return sorted(accounts)


def derive_monthly_non_gst_supply(gl_rows):
    """Mirror image of derive_monthly_taxable_supply(): sale-type vouchers
    that carry NO output-tax credit leg (the non-GST/Table-8 condition),
    summing their credit legs (rounding excluded) per (financial_year,
    period).

    "Sale-type" is the voucher TYPE (contains "sale": Tally's 'Sales',
    'Sales GST', 'Sales Cash', ...), not an account name -- see the module
    docstring's Generalised section for why that replaced the literal
    account "Sales" (verified to select exactly the same legs there).

    Returns {(financial_year, period): non_gst_value}, floats.
    """
    monthly = defaultdict(float)

    for entry_id, legs in _group_vouchers(gl_rows).items():
        if not legs or not is_sales_voucher_type(legs[0].get("vch_type")):
            continue
        if any(l["dr_cr"] == "Cr" and tax_head(l["account"]) for l in legs):
            continue
        revenue_legs = [
            l for l in legs if l["dr_cr"] == "Cr" and not is_rounding_account(l["account"])
        ]
        if not revenue_legs:
            continue

        key = _voucher_period(legs)
        if key is None:
            continue
        for l in revenue_legs:
            monthly[key] += float(l["amount"])

    return dict(monthly)


def _period_ordinal(fy, month_name):
    """Months since a fixed origin, so periods across financial years order
    and subtract correctly ('2025-26','April' -> fy_start*12 + 0)."""
    return int(str(fy)[:4]) * 12 + (_MONTH_NUM[month_name] - 4) % 12


def _gstr1_span(gstr1_rows):
    """(first, last) period ordinals covered by the GSTR-1 rows, or None."""
    ordinals = []
    for r in gstr1_rows:
        try:
            ordinals.append(_period_ordinal(r["financial_year"], r["period"]))
        except (KeyError, ValueError):
            continue
    return (min(ordinals), max(ordinals)) if ordinals else None


def coverage(gl_rows, gstr1_rows):
    """How the ledger's months line up with the GSTR-1 rows supplied:
    {"compared": [keys in both], "missing_inside_span": [keys the ledger has
    but GSTR-1 lacks, between the first and last return supplied],
    "not_covered": [keys outside the span of returns supplied]}. Keys are
    (financial_year, period); each list is in calendar order."""
    ledger_keys = set(derive_monthly_taxable_supply(gl_rows)) | set(derive_monthly_non_gst_supply(gl_rows))
    have = {(r["financial_year"], r["period"]) for r in gstr1_rows}
    span = _gstr1_span(gstr1_rows)

    def order(k):
        return _period_ordinal(*k)

    compared = sorted((k for k in ledger_keys if k in have), key=order)
    missing, uncovered = [], []
    for k in sorted((k for k in ledger_keys if k not in have), key=order):
        if span is not None and span[0] <= order(k) <= span[1]:
            missing.append(k)
        else:
            uncovered.append(k)
    return {"compared": compared, "missing_inside_span": missing, "not_covered": uncovered}


def check_ledger_gstr_reconciliation(gl_rows, gstr1_rows):
    """Compares the ledger's own derived monthly taxable supply (see
    derive_monthly_taxable_supply) against GSTR-1's filed taxable_value/
    igst/cgst/sgst for every period present in BOTH.

    A ledger month with no GSTR-1 is flagged ("ledger_activity_with_no_
    gstr1_filing") ONLY when it lies inside the span of months the supplied
    returns cover -- a hole between two returns that are on file. A month
    before the first or after the last return supplied (or any month, when
    no return was supplied at all) is a coverage gap, not evidence of a
    missing filing -- the person may simply not have uploaded that return
    -- so it is not flagged here; coverage() reports it separately.
    Periods the ledger has no data for are never flagged either.

    Also compares the ledger's derived monthly non-GST supply (see
    derive_monthly_non_gst_supply) against GSTR-1's non_gst_value for every
    period present in both, using the same tolerance/severity convention,
    under a separate "ledger_vs_gstr1_non_gst_supply" check name. A period
    with non-GST ledger activity but no GSTR-1 filing is NOT flagged
    separately from the taxable-supply case above (one combined
    "ledger_activity_with_no_gstr1_filing" flag per period covers both,
    since it is the same missing filing either way).

    Returns a flat list of flag dicts, same shape convention as
    rule_gstr_reconciliation.py's check_gstr_reconciliation().
    """
    ledger_by_period = derive_monthly_taxable_supply(gl_rows)
    non_gst_by_period = derive_monthly_non_gst_supply(gl_rows)
    gstr1_by_period = {(r["financial_year"], r["period"]): r for r in gstr1_rows}
    span = _gstr1_span(gstr1_rows)

    flags = []
    all_periods = sorted(set(ledger_by_period) | set(non_gst_by_period),
                         key=lambda k: _period_ordinal(*k))
    for key in all_periods:
        fy, period = key
        label = f"{period} {fy}"
        ledger = ledger_by_period.get(key)
        non_gst_amt = non_gst_by_period.get(key)
        g1 = gstr1_by_period.get(key)
        if g1 is None:
            if span is None or not (span[0] <= _period_ordinal(fy, period) <= span[1]):
                continue  # not covered by the returns supplied -- see docstring
            parts = []
            if ledger is not None:
                parts.append(f"Rs.{ledger['taxable_value']:.2f} of taxable supply")
            if non_gst_amt is not None:
                parts.append(f"Rs.{non_gst_amt:.2f} of non-GST supply")
            flags.append({
                "period": label,
                "check": "ledger_activity_with_no_gstr1_filing",
                "reason": (
                    f"Ledger shows {' and '.join(parts)} this period, but no "
                    f"GSTR-1 is on file for it, although returns on file cover "
                    f"the months before and after it"
                ),
                "severity": "CRITICAL",
            })
            continue

        if ledger is not None:
            for field, label_name in [
                ("taxable_value", "Taxable value"), ("igst", "IGST"),
                ("cgst", "CGST"), ("sgst", "SGST"),
            ]:
                ledger_amt = ledger[field]
                gstr_amt = float(g1[field])
                mismatch = ledger_amt - gstr_amt
                if abs(mismatch) > TOLERANCE_RUPEES:
                    pct = abs(mismatch / gstr_amt * 100) if gstr_amt else None
                    severity = (
                        "CRITICAL" if gstr_amt == 0 or pct >= 30 else
                        "HIGH" if pct >= 10 else
                        "MEDIUM"
                    )
                    flags.append({
                        "period": label,
                        "check": "ledger_vs_gstr1_taxable_supply",
                        "reason": f"{label_name}: ledger shows Rs.{ledger_amt:.2f} but "
                                  f"GSTR-1 reports Rs.{gstr_amt:.2f} (gap: Rs.{mismatch:.2f})",
                        "severity": severity,
                    })

        if non_gst_amt is not None:
            gstr_non_gst = float(g1["non_gst_value"])
            mismatch = non_gst_amt - gstr_non_gst
            if abs(mismatch) > TOLERANCE_RUPEES:
                pct = abs(mismatch / gstr_non_gst * 100) if gstr_non_gst else None
                severity = (
                    "CRITICAL" if gstr_non_gst == 0 or pct >= 30 else
                    "HIGH" if pct >= 10 else
                    "MEDIUM"
                )
                flags.append({
                    "period": label,
                    "check": "ledger_vs_gstr1_non_gst_supply",
                    "reason": f"Non-GST value: ledger shows Rs.{non_gst_amt:.2f} but "
                              f"GSTR-1 reports Rs.{gstr_non_gst:.2f} (gap: Rs.{mismatch:.2f})",
                    "severity": severity,
                })

    return flags


def _status(gl_rows_have_sales, gstr1_rows, cov):
    """One word for why the comparison did or did not happen, so the
    dashboard can say it plainly: ok / no_returns / no_sales / no_overlap."""
    if not gl_rows_have_sales:
        return "no_sales"
    if not gstr1_rows:
        return "no_returns"
    if not cov["compared"]:
        return "no_overlap"
    return "ok"


def _label_list(keys):
    return [f"{p} {fy}" for fy, p in keys]


def run_ledger_gstr_report(gl_file=GL_FILE, gstr1_file=GSTR1_FILE, write_export=True,
                           gstr1_rows=None, out_path=None, ledger_label=None,
                           gstr1_sources=None, gstr1_warnings=None):
    """Runs the comparison and (optionally) writes the JSON report.

    Defaults reproduce the original behaviour exactly: the FY2025-26 sample
    ledger against data/gstr1_summary.csv, written to
    output/ledger_gstr_reconciliation_report.json. To check another ledger
    pass its CSV as `gl_file`, the GSTR-1 rows it should be checked against
    as `gstr1_rows` (list of dicts; `gstr1_file` is then not read), and
    `out_path` for its own report. `ledger_label` / `gstr1_sources` are
    recorded in the report so a reader can see what was compared with what,
    and `gstr1_warnings` (return files that could not be read) so a skipped
    month is never silent.
    """
    with open(gl_file, "r", encoding="utf-8") as f:
        gl_rows = list(csv.DictReader(f))
    read_from_file = gstr1_rows is None
    if read_from_file:
        with open(gstr1_file, "r", encoding="utf-8") as f:
            gstr1_rows = list(csv.DictReader(f))

    flags = check_ledger_gstr_reconciliation(gl_rows, gstr1_rows)
    ledger_periods = sorted(derive_monthly_taxable_supply(gl_rows).keys(), key=lambda k: _period_ordinal(*k))
    non_gst_periods = sorted(derive_monthly_non_gst_supply(gl_rows).keys(), key=lambda k: _period_ordinal(*k))
    cov = coverage(gl_rows, gstr1_rows)
    unreadable_dates = sum(
        1 for legs in _group_vouchers(gl_rows).values() if legs and _voucher_period(legs) is None
    )
    severity_counts = {}
    for f in flags:
        severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": _status(bool(ledger_periods or non_gst_periods), gstr1_rows, cov),
        "dataset": {
            "general_ledger_file": os.path.abspath(gl_file),
            "gstr1_file": os.path.abspath(gstr1_file) if read_from_file else None,
            "ledger_label": ledger_label,
            "gstr1_sources": list(gstr1_sources) if gstr1_sources is not None else None,
            "gstr1_warnings": list(gstr1_warnings or []),
            "ledger_periods_with_taxable_activity": _label_list(ledger_periods),
            "ledger_periods_with_non_gst_activity": _label_list(non_gst_periods),
            "periods_compared": _label_list(cov["compared"]),
            "periods_not_covered_by_gstr1": _label_list(cov["not_covered"]),
            "periods_missing_inside_gstr1_span": _label_list(cov["missing_inside_span"]),
            "non_gst_sales_accounts": non_gst_revenue_accounts(gl_rows),
            "vouchers_with_unreadable_dates": unreadable_dates,
        },
        "flags": flags,
        "summary": {
            "total_flagged": len(flags),
            "by_severity": severity_counts,
        },
    }

    if write_export:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        json_path = out_path or os.path.join(OUTPUT_DIR, "ledger_gstr_reconciliation_report.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(result, jf, indent=2, ensure_ascii=False)
        print(f"[EXPORT] Wrote structured report to:\n  {json_path}")

    return result


if __name__ == "__main__":
    result = run_ledger_gstr_report()
    print(f"Status: {result['status']}")
    print(f"Ledger periods with taxable activity: "
          f"{len(result['dataset']['ledger_periods_with_taxable_activity'])}")
    print(f"Ledger periods with non-GST activity: "
          f"{len(result['dataset']['ledger_periods_with_non_gst_activity'])}")
    print(f"Months compared: {len(result['dataset']['periods_compared'])}")
    print()
    flags = result["flags"]
    print(f"Discrepancies found: {len(flags)}")
    print()
    if flags:
        for flag in flags:
            print(f"  [{flag['severity']}] {flag['period']} ({flag['check']}): {flag['reason']}")
    else:
        print("  Every month covered by both the ledger and the filed GSTR-1 "
              "returns reconciles cleanly -- taxable supply AND non-GST "
              "supply (Rs.10 tolerance).")
