"""
RULE: GSTR-1 vs GSTR-3B Monthly GST Reconciliation
----------------------------------------------------
A new, separate stream from Rule 4 (ITR vs Form 26AS) -- that rule checks
ANNUAL income-tax TDS reconciliation; this one checks MONTHLY GST
compliance. Same underlying logic, though: GSTR-1 (outward supplies,
filed by the taxpayer describing what they sold) and GSTR-3B (the summary
return that settles what tax is actually paid on those same supplies) are
two independently-structured filings describing the SAME real-world
supplies for the SAME period. They should match -- a mismatch is a direct,
provable discrepancy, not an inferred pattern.

Three checks, in order:
  1. GSTR-1's Table 7 (B2CS) taxable-supply figures against GSTR-3B's
     Table 3.1(a) -- these describe the identical taxable outward supply.
  2. GSTR-1's Table 8 non-GST supply value (fuel -- petrol/diesel are
     outside GST) against GSTR-3B's Table 3.1(e).
  3. GSTR-3B's own internal payment completeness: tax payable vs. tax
     actually paid (through ITC + cash), per tax head -- an outstanding,
     unpaid liability for a period that's already been filed.

LIMITATION (updated 2026-10-03, originally written 2026-10-02 -- read
before adding more checks): real data now covers 16 months (April 2025 -
July 2026, FY2025-26 and FY2026-27, via adapt_gstr_to_schema.py's
All_Months PDFs), but this STILL has ZERO overlap with
general_ledger.csv (FY2024-25) -- the same shape of problem the old
form26as.csv/itr_summary.csv mismatch had before that was rebuilt from the
matching-year real documents. There is currently no ledger period this
rule's output can be cross-checked against. All 16 real months on file
reconcile cleanly against each other (no cross-filing discrepancy exists
to detect) -- this was re-verified after adapt_gstr_to_schema.py's
taxable_value fix (deriving it from the GSTR-1 "Total Liability" line
rather than Table 7 alone; see that module's docstring), which was itself
needed BECAUSE one of these 16 real months (June 2025-26) has a nonzero
Table 4/B2B entry that the old Table-7-only figure under-counted against
GSTR-3B by exactly that amount -- a real extraction gap, not a reconciled-
away false positive. This rule's own logic (the comparison and severity
grading below) is therefore still validated here only with synthetic,
stress-test-injected discrepancies (see test_rule_gstr_reconciliation.py),
not against a real cross-filing anomaly -- the same validation approach
used for Rules 2, 4 and 5's stress tests. Re-verify this note's accuracy
once a ledger period overlapping a filed GSTR period exists.

Origin-aware use (V3.14 Addendum 20): run_gstr_report() can be given GSTR-1
and GSTR-3B rows directly, so the dashboard checks the SAMPLE returns against
each other and the person's UPLOADED returns against each other, as two
separate reports -- never a mix. For the uploaded set it runs with
missing_is_filing_gap=False: a month present in only one of the two
documents is listed as "not compared" instead of being flagged as a missing
filing, because not having uploaded a return is not evidence that it was
never filed. (The sample set keeps the original behaviour, pinned as a
regression check.)
"""

import csv
import json
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "output")
GSTR1_FILE = os.path.join(DATA_DIR, "gstr1_summary.csv")
GSTR3B_FILE = os.path.join(DATA_DIR, "gstr3b_summary.csv")

TOLERANCE_RUPEES = 10.0  # allow small rounding differences, nothing more


def classify_severity(mismatch_amount, reference_amount):
    """Same severity ladder as rule_itr_26as_reconciliation.classify_severity
    (not imported -- every rule_*.py in this project is standalone and
    independently runnable, and combined_report.py keeps its own inline
    copies of each rule rather than importing them, so this follows the
    same convention rather than introducing a cross-module dependency).
    CRITICAL for a zero reference amount with a real nonzero mismatch (the
    percentage gap is undefined/maximal, not small), NONE only for a
    genuine zero/zero, and the usual >=30/>=10/>0 percentage bands
    otherwise."""
    if reference_amount == 0:
        return "CRITICAL" if mismatch_amount != 0 else "NONE"
    pct_diff = abs(mismatch_amount / reference_amount) * 100
    if pct_diff >= 30:
        return "CRITICAL"
    elif pct_diff >= 10:
        return "HIGH"
    elif pct_diff > 0:
        return "MEDIUM"
    else:
        return "NONE"


def _period_label(row):
    return f"{row['period']} {row['financial_year']}"


_MONTHS = ["April", "May", "June", "July", "August", "September", "October",
           "November", "December", "January", "February", "March"]


def _period_order(key):
    """(financial_year, month) -> sortable number (calendar order)."""
    fy, month = key
    try:
        return int(str(fy)[:4]) * 12 + _MONTHS.index(month)
    except (ValueError, IndexError):
        return 0


def pairing(gstr1_rows, gstr3b_rows):
    """Which periods the two documents share and which only one has:
    {"compared": [...], "only_gstr1": [...], "only_gstr3b": [...]}, each a
    list of (financial_year, period) keys in calendar order."""
    k1 = {(r["financial_year"], r["period"]) for r in gstr1_rows}
    k3 = {(r["financial_year"], r["period"]) for r in gstr3b_rows}
    order = lambda keys: sorted(keys, key=_period_order)  # noqa: E731
    return {"compared": order(k1 & k3), "only_gstr1": order(k1 - k3),
            "only_gstr3b": order(k3 - k1)}


def check_gstr_reconciliation(gstr1_rows, gstr3b_rows, missing_is_filing_gap=True):
    """Returns a flat list of flag dicts, each tagged with which of the
    three checks (above) it came from, so callers can distinguish
    cross-filing mismatches from a within-GSTR-3B payment shortfall.

    `missing_is_filing_gap` (default True, the original behaviour): a GSTR-1
    period with no GSTR-3B is flagged CRITICAL "missing_filing". Pass False
    for documents a person uploaded: such a month is then simply not
    compared (see pairing()) -- it is not evidence the return was never
    filed. A GSTR-3B period with no GSTR-1 is never flagged either way."""
    gstr3b_by_period = {(r["financial_year"], r["period"]): r for r in gstr3b_rows}
    flags = []

    for g1 in gstr1_rows:
        key = (g1["financial_year"], g1["period"])
        g3b = gstr3b_by_period.get(key)
        if g3b is None:
            if not missing_is_filing_gap:
                continue
            flags.append({
                "period": _period_label(g1),
                "check": "missing_filing",
                "reason": "GSTR-1 was filed for this period but no matching GSTR-3B was found",
                "severity": "CRITICAL",
            })
            continue

        for field, label in [
            ("taxable_value", "Taxable value"),
            ("igst", "IGST"),
            ("cgst", "CGST"),
            ("sgst", "SGST"),
            ("cess", "Cess"),
        ]:
            g1_amt = float(g1[field])
            g3b_amt = float(g3b[field])
            mismatch = g1_amt - g3b_amt
            if abs(mismatch) > TOLERANCE_RUPEES:
                flags.append({
                    "period": _period_label(g1),
                    "check": "gstr1_vs_gstr3b_taxable_supplies",
                    "reason": f"{label}: GSTR-1 reports Rs.{g1_amt} but GSTR-3B reports "
                              f"Rs.{g3b_amt} (gap: Rs.{mismatch:.2f})",
                    "severity": classify_severity(mismatch, g3b_amt),
                })

        non_gst_mismatch = float(g1["non_gst_value"]) - float(g3b["non_gst_value"])
        if abs(non_gst_mismatch) > TOLERANCE_RUPEES:
            flags.append({
                "period": _period_label(g1),
                "check": "gstr1_vs_gstr3b_non_gst_supplies",
                "reason": f"Non-GST supply value: GSTR-1 reports Rs.{g1['non_gst_value']} "
                          f"but GSTR-3B reports Rs.{g3b['non_gst_value']} "
                          f"(gap: Rs.{non_gst_mismatch:.2f})",
                "severity": classify_severity(non_gst_mismatch, float(g3b["non_gst_value"])),
            })

    for g3b in gstr3b_rows:
        for head in ("cgst", "sgst", "igst"):
            payable = float(g3b[f"{head}_payable"])
            paid = float(g3b[f"{head}_paid_total"])
            shortfall = payable - paid
            if abs(shortfall) > TOLERANCE_RUPEES:
                flags.append({
                    "period": _period_label(g3b),
                    "check": "gstr3b_payment_completeness",
                    "reason": f"{head.upper()}: Rs.{payable} payable but only Rs.{paid} "
                              f"paid (shortfall: Rs.{shortfall:.2f})",
                    "severity": classify_severity(shortfall, payable),
                })

    return flags


def _status(gstr1_rows, gstr3b_rows, paired):
    """Why a comparison did or did not happen: ok / no_gstr1 / no_gstr3b /
    no_overlap (both uploaded, but for different months)."""
    if not gstr1_rows:
        return "no_gstr1"
    if not gstr3b_rows:
        return "no_gstr3b"
    if not paired["compared"]:
        return "no_overlap"
    return "ok"


def _labels(keys):
    return [f"{period} {fy}" for fy, period in keys]


def run_gstr_report(write_export=True, gstr1_rows=None, gstr3b_rows=None, out_path=None,
                    label=None, gstr1_sources=None, gstr3b_sources=None, warnings=None,
                    missing_is_filing_gap=True):
    """Loads the CSVs (or uses the rows passed in), runs
    check_gstr_reconciliation(), and returns a result dict in the same shape
    combined_report.py's run_combined_report() returns (generated_at/dataset/
    flags/summary) -- added 2026-10-02 so a dashboard (or anything else
    downstream) has one stable JSON source for this rule, the same way
    combined_report.py already exports its own output/combined_report.json.
    This rule stays a SEPARATE export, not merged into combined_report.json
    itself -- see this module's docstring for why GSTR is kept standalone
    (different, non-overlapping period).

    With no arguments it behaves exactly as before: data/gstr1_summary.csv
    and data/gstr3b_summary.csv, written to output/gstr_report.json. To check
    another set pass its rows as `gstr1_rows`/`gstr3b_rows` and a path for
    `out_path`; `label`, `gstr1_sources`, `gstr3b_sources` and `warnings` are
    recorded so a reader can see exactly which documents were compared."""
    if gstr1_rows is None:
        with open(GSTR1_FILE, "r") as f:
            gstr1_rows = list(csv.DictReader(f))
    if gstr3b_rows is None:
        with open(GSTR3B_FILE, "r") as f:
            gstr3b_rows = list(csv.DictReader(f))

    flags = check_gstr_reconciliation(gstr1_rows, gstr3b_rows,
                                      missing_is_filing_gap=missing_is_filing_gap)
    paired = pairing(gstr1_rows, gstr3b_rows)
    severity_counts = {}
    for f in flags:
        severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": _status(gstr1_rows, gstr3b_rows, paired),
        "dataset": {
            "gstr1_file": os.path.abspath(GSTR1_FILE) if gstr1_sources is None else None,
            "gstr3b_file": os.path.abspath(GSTR3B_FILE) if gstr3b_sources is None else None,
            "label": label,
            "gstr1_sources": list(gstr1_sources) if gstr1_sources is not None else None,
            "gstr3b_sources": list(gstr3b_sources) if gstr3b_sources is not None else None,
            "warnings": list(warnings or []),
            "gstr1_periods": len(gstr1_rows),
            "gstr3b_periods": len(gstr3b_rows),
            "periods": sorted({_period_label(r) for r in gstr1_rows} |
                               {_period_label(r) for r in gstr3b_rows}),
            "periods_compared": _labels(paired["compared"]),
            "periods_only_in_gstr1": _labels(paired["only_gstr1"]),
            "periods_only_in_gstr3b": _labels(paired["only_gstr3b"]),
        },
        "flags": flags,
        "summary": {
            "total_flagged": len(flags),
            "by_severity": severity_counts,
        },
    }

    if write_export:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        json_path = out_path or os.path.join(OUTPUT_DIR, "gstr_report.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(result, jf, indent=2, ensure_ascii=False)
        print(f"[EXPORT] Wrote structured report to:\n  {json_path}")

    return result


if __name__ == "__main__":
    result = run_gstr_report()

    print(f"GSTR-1 periods on file: {result['dataset']['gstr1_periods']}")
    print(f"GSTR-3B periods on file: {result['dataset']['gstr3b_periods']}")
    print()

    flags = result["flags"]
    print(f"Discrepancies found: {len(flags)}")
    print()
    if flags:
        for flag in flags:
            print(f"  [{flag['severity']}] {flag['period']} ({flag['check']}): {flag['reason']}")
    else:
        print("  All filed periods reconcile within tolerance (Rs.10).")
