"""
tax_compliance_report.py
------------------------
The dashboard's Tax & Compliance results, built from WHATEVER tax documents
are on file -- chosen by their content (see source_resolver.py), not by an
established filename. Writes output/tax_compliance_report.json.

Two checks, each independently reported (one being impossible never hides
the other):

1. ITR vs Form 26AS (TDS reconciliation). Needs a Form 26AS plus the
   Computation of Income and ITR-V acknowledgement for the SAME assessment
   year (source_resolver.resolve_tax_sources()). The existing, tested
   adapt_tax_docs() does the extraction; rule_reconciliation() from
   combined_report.py does the comparison -- no new audit logic here.

2. Trial balance vs ledger. Needs a trial balance plus a processed ledger
   covering the same dates. The trial balance's period is read from its own
   header; the ledger whose dates fall inside that period is the one it is
   reconciled against, using the existing reconcile_trial_balance /
   rule_trial_balance_reconciliation code unchanged. No ledger covering
   that period -> an explicit "no matching ledger" status, never a
   reconciliation against the wrong year.

Every section carries a status:
  "ok"          -- ran; results below are real
  "incomplete"  -- some of the required documents are missing (reason says
                   which, e.g. "no Computation of Income for AY 2025-26")
  "none"        -- no document of that kind on file at all
  "error"       -- the documents were found but could not be analysed
                   (error says why)
and the report always lists which files were used, so a reader can see
exactly what each number came from.

Nothing here modifies data/form26as.csv, data/itr_summary.csv or any
combined_report*.json -- the frozen baseline pipeline is untouched; this
writes only its own output/tax_compliance_report.json plus two CSVs next to
it (output/tax_form26as.csv, output/tax_itr_summary.csv).
"""
import csv
import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import source_resolver as sr

OUTPUT_DIR = sr.OUTPUT_DIR
TAX_COMPLIANCE_REPORT_PATH = os.path.join(OUTPUT_DIR, "tax_compliance_report.json")
OUT_26AS_CSV = os.path.join(OUTPUT_DIR, "tax_form26as.csv")
OUT_ITR_CSV = os.path.join(OUTPUT_DIR, "tax_itr_summary.csv")


def _itr_vs_26as(documents):
    tax = sr.resolve_tax_sources(documents)
    section = {
        "status": tax["status"],
        "assessment_year": tax["assessment_year"],
        "sources": tax["filenames"],
        "reason": tax["reason"],
        "missing": tax["missing"],
        "deductor_count": 0,
        "total_claimed": 0.0,
        "total_reported": 0.0,
        "flags": [],
    }
    if tax["status"] != "ok":
        return section
    try:
        import adapt_tax_docs_to_schema as adapter
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        adapter.adapt_tax_docs(
            as26_path=tax["form_26as"], coi_path=tax["itr_computation"],
            ack_path=tax["itr_acknowledgement"],
            out_26as_path=OUT_26AS_CSV, out_itr_path=OUT_ITR_CSV,
        )
        with open(OUT_26AS_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        from combined_report import rule_reconciliation
        section["flags"] = rule_reconciliation(rows)
        section["deductor_count"] = len(rows)
        section["total_claimed"] = sum(float(r["tds_claimed_in_itr_share"]) for r in rows)
        section["total_reported"] = sum(float(r["tds_reported_by_deductor"]) for r in rows)
    except ImportError:
        raise  # a missing dependency is a setup problem, surfaced as such by the caller
    except Exception as e:
        section["status"] = "error"
        section["reason"] = f"Could not read the tax documents: {e}"
    return section


def _ledger_period(csv_path):
    """(min_date, max_date) of a normalized ledger CSV, or None."""
    lo = hi = None
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = sr.parse_dmy(row.get("date") or "")
                if d is None:
                    continue
                lo = d if lo is None or d < lo else lo
                hi = d if hi is None or d > hi else hi
    except OSError:
        return None
    return (lo, hi) if lo is not None else None


def _trial_balance_vs_ledger(documents, ledger_candidates):
    """`ledger_candidates`: [{"label": str, "csv_path": str}, ...] -- every
    ledger with a normalized CSV on disk (the two established ones plus any
    the person has processed)."""
    tb = sr.resolve_trial_balance(documents)
    section = {
        "status": "none", "sources": {}, "period": None, "ledger_label": None,
        "reason": "", "clean_count": 0, "mismatched_count": 0,
        "unmatched_count": 0, "total_checked": 0, "flags": [],
    }
    if tb is None:
        section["reason"] = "No trial balance has been uploaded."
        return section
    section["sources"] = {"trial_balance": tb["filename"]}
    period = tb["period"]
    if period is None:
        section["status"] = "incomplete"
        section["reason"] = (
            f"Could not read the period from {tb['filename']}, so it cannot be "
            f"matched to a ledger."
        )
        return section
    start, end = period
    section["period"] = f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"

    match = None
    for cand in ledger_candidates:
        lp = _ledger_period(cand["csv_path"])
        if lp is None:
            continue
        if lp[0] >= start and lp[1] <= end:
            match = cand
            break
    if match is None:
        section["status"] = "incomplete"
        section["reason"] = (
            f"Trial balance {tb['filename']} covers {section['period']}, but no "
            f"processed ledger covers those dates. Upload and process the ledger "
            f"for the same period to reconcile it."
        )
        return section

    try:
        from parse_trial_balance import parse_trial_balance
        from reconcile_trial_balance import reconcile
        from rule_trial_balance_reconciliation import check_trial_balance_reconciliation
        with open(match["csv_path"], "r", encoding="utf-8") as f:
            gl_rows = list(csv.DictReader(f))
        tb_rows = parse_trial_balance(tb["path"])
        result = reconcile(tb_rows=tb_rows, gl_rows=gl_rows)
        section["flags"] = check_trial_balance_reconciliation(tb_rows=tb_rows, gl_rows=gl_rows)
        section["clean_count"] = len(result["clean"])
        section["mismatched_count"] = len(result["mismatched"])
        section["unmatched_count"] = len(result["unmatched"])
        section["total_checked"] = section["clean_count"] + section["mismatched_count"]
        section["ledger_label"] = match["label"]
        section["status"] = "ok"
        if section["total_checked"] == 0:
            section["status"] = "incomplete"
            section["reason"] = (
                f"Trial balance {tb['filename']} and ledger '{match['label']}' cover "
                f"the same dates, but no account name appears in both -- they may "
                f"belong to different entities."
            )
    except ImportError:
        raise
    except Exception as e:
        section["status"] = "error"
        section["reason"] = f"Could not reconcile the trial balance: {e}"
    return section


def run_tax_compliance_report(documents=None, ledger_candidates=None, write=True,
                              report_path=None):
    """Builds (and by default writes) the tax-compliance report.

    `documents`: source_resolver.scan_documents()'s list (scanned here when
    omitted). `ledger_candidates`: see _trial_balance_vs_ledger(); empty by
    default, which simply means "no ledger to reconcile a trial balance
    against yet". Returns the report dict."""
    if documents is None:
        documents = sr.scan_documents()
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "itr_vs_26as": _itr_vs_26as(documents),
        "trial_balance": _trial_balance_vs_ledger(documents, ledger_candidates or []),
    }
    if write:
        path = report_path or TAX_COMPLIANCE_REPORT_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        os.replace(tmp, path)  # atomic: a page reading it never sees a half-written file
    return report


if __name__ == "__main__":
    rep = run_tax_compliance_report()
    print(json.dumps(rep, indent=2, default=str))
