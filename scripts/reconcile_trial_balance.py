"""
reconcile_trial_balance.py
----------------------------
Compares the partnership firm's official Tally trial balance
(data/raw_pdfs/trial_balance_redacted.pdf, parsed by parse_trial_balance.py)
against the reconstructed general ledger (data/general_ledger.csv), per
account: does the ledger's sum of Dr legs / Cr legs for that account match
the trial balance's "Transactions Debit"/"Transactions Credit" columns?

ACCOUNT NAME ALIASING (the point of this script, not just a passthrough):
investigation on 2026-10-01 found that several accounts are printed under
TWO DIFFERENT name strings in the source ledger PDF -- the full/first-page
name, and a shortened or differently-punctuated name on that account's own
CONTINUATION pages (pages after its first). This is a source-document
inconsistency, not a reconstruction defect: reconstruct_ledger_entries.py
correctly detects a header on every one of these pages, it just detects two
different strings for what is logically one account. Confirmed via raw PDF
page headers (e.g. page 561 "Purchase" vs pages 562-594 "Purchase Account"
for the SAME ledger run) and the trial balance using only the shorter name.

This script treats each alias group as one account for comparison purposes.
It does NOT change the ledger data itself -- general_ledger.csv keeps both
name variants as written. This is a comparison-side fix only.

KNOWN ALIAS GROUPS (evidence: reconstruct_ledger_entries page-header scan,
2026-10-01). Each maps the name the trial balance actually prints to every
variant found in general_ledger.csv for that same logical account:
  - Purchase            <- {Purchase, Purchase Account}             (p.561 vs p.562-594)
  - Sales                <- {Sales, Sales Account}                   (p.658 vs p.659-733)
  - TDS Receivable HPCL <- {TDS Receivable HPCL, TDS Receivable}    (p.908 vs p.909-912)
  - Rent Expenses HPCL  <- {Rent Expenses HPCL, Rent Expenses}      (p.601 vs p.602-604)
  - Round Off/Discount  <- {Round Off/Discount, "Round Off /Discount"} (p.609 vs p.610-651, note the space)

NOTE on residuals, AS OF THE 2026-10-01 INVESTIGATION THAT WROTE THIS
SCRIPT (historical -- see the 2026-10-02 update below for the current,
resolved state; kept here rather than deleted so the investigation trail
stays intact):
  - TDS Receivable HPCL and Rent Expenses HPCL reconcile EXACTLY once
    aliased -- these were never reconstruction errors, only an artifact of
    comparing by exact string before this script existed.
  - Purchase reconciles to within Rs.1,203.60 (~0.0003%), consistent with
    routine rounding.
  - Sales does NOT reconcile even combined (GL overshoots TB by ~Rs.8.27
    crore) -- this points to something beyond simple name fragmentation
    and needs its own separate investigation.
  - Round Off/Discount does not fully reconcile either (debit roughly 2x
    TB's figure) -- also needs separate investigation.
  - The J & K Bank Ltd Rs.55,00,000 credit-side gap and the 3-page
    header-carryover bug (Laptop / Pollution Fees, pages 477/546/547) are
    UNRELATED to name aliasing and are not addressed by this script.

UPDATE (2026-10-02): the Sales and Round Off/Discount residuals above are
RESOLVED. The later V3.8 (CGST/SGST canonicalization) and V3.9 (name-pair
fixes) work fixed the underlying duplicate-counting/name-fragmentation
causes -- already pinned clean by
test_account_canonicalization_fix.py::test_sales_purchase_roundoff_reconcile_exactly_against_trial_balance
and test_cgst_sgst_canonicalization_fix.py::test_cgst_sgst_reconcile_exactly_against_trial_balance.
A fresh run of reconcile() against the current general_ledger.csv (V3.10)
shows all 82 named, matched trial-balance accounts clean, 0 mismatched --
re-verified when rule_trial_balance_reconciliation.py was built to expose
this check as a proper rule rather than leaving it as test-only
infrastructure. The J & K Bank Ltd gap and header-carryover bug mentioned
above were separate, unrelated findings already resolved in their own
right by the blank-particulars and header-carryover fixes (see
test_blank_particulars_fix.py, test_header_carryover_fix.py) well before
this update.
"""
import csv
import os
import sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
GL_FILE = os.path.join(DATA_DIR, "general_ledger.csv")

sys.path.insert(0, SCRIPT_DIR)
from parse_trial_balance import parse_trial_balance

# Known continuation-page name variants. Keys are what the trial balance
# itself prints; values are every variant found in general_ledger.csv that
# is logically the same account.
ACCOUNT_ALIASES = {
    "Purchase": {"Purchase", "Purchase Account"},
    "Sales": {"Sales", "Sales Account"},
    "TDS Receivable HPCL": {"TDS Receivable HPCL", "TDS Receivable"},
    "Rent Expenses HPCL": {"Rent Expenses HPCL", "Rent Expenses"},
    "Round Off/Discount": {"Round Off/Discount", "Round Off /Discount"},
}

TOLERANCE_RUPEES = 1.0


def _gl_dr_cr_sums(gl_rows):
    dr = defaultdict(float)
    cr = defaultdict(float)
    for r in gl_rows:
        acct = (r.get("account") or "").strip()
        try:
            amt = float(r.get("amount", 0) or 0)
        except ValueError:
            continue
        if r.get("entry_type") == "Dr":
            dr[acct] += amt
        elif r.get("entry_type") == "Cr":
            cr[acct] += amt
    return dr, cr


def reconcile(tb_rows=None, gl_rows=None):
    """Returns a dict with clean/mismatched/unmatched lists. Each entry:
        {account, tb_debit, gl_debit, debit_diff, tb_credit, gl_credit,
         credit_diff, variants_used}
    """
    if tb_rows is None:
        tb_rows = parse_trial_balance()
    if gl_rows is None:
        with open(GL_FILE, "r", encoding="utf-8") as f:
            gl_rows = list(csv.DictReader(f))

    gl_dr, gl_cr = _gl_dr_cr_sums(gl_rows)
    gl_accounts = set(gl_dr) | set(gl_cr)

    clean, mismatched, unmatched = [], [], []

    for r in tb_rows:
        acct = r["account"]
        if not acct:
            continue
        variants = ACCOUNT_ALIASES.get(acct, {acct})
        present_variants = variants & gl_accounts
        if not present_variants:
            unmatched.append(acct)
            continue

        gl_debit = sum(gl_dr.get(v, 0.0) for v in variants)
        gl_credit = sum(gl_cr.get(v, 0.0) for v in variants)
        debit_diff = abs(gl_debit - r["debit"])
        credit_diff = abs(gl_credit - r["credit"])

        entry = {
            "account": acct, "page": r["page"],
            "tb_debit": r["debit"], "gl_debit": gl_debit, "debit_diff": debit_diff,
            "tb_credit": r["credit"], "gl_credit": gl_credit, "credit_diff": credit_diff,
            "variants_used": sorted(present_variants),
        }
        if debit_diff <= TOLERANCE_RUPEES and credit_diff <= TOLERANCE_RUPEES:
            clean.append(entry)
        else:
            mismatched.append(entry)

    return {"clean": clean, "mismatched": mismatched, "unmatched": unmatched}


def main():
    result = reconcile()
    clean, mismatched, unmatched = result["clean"], result["mismatched"], result["unmatched"]

    print(f"Trial balance named rows with a matching GL account (aliased where known): "
          f"{len(clean) + len(mismatched)}")
    print(f"  Clean (within Rs.{TOLERANCE_RUPEES:.2f}): {len(clean)}")
    print(f"  Mismatched: {len(mismatched)}")
    print(f"Trial balance rows with NO matching GL account at all: {len(unmatched)} "
          f"(expected -- Tally group/category headers and zero-activity fixed assets)")
    print()

    if mismatched:
        print("=== MISMATCHED (after aliasing) ===")
        for e in mismatched:
            aliased_note = f" [aliased: {e['variants_used']}]" if len(e["variants_used"]) > 1 else ""
            print(f"p{e['page']:2} {e['account']:30}{aliased_note}")
            print(f"     debit:  TB={e['tb_debit']:>14,.2f}  GL={e['gl_debit']:>14,.2f}  diff={e['debit_diff']:>12,.2f}")
            print(f"     credit: TB={e['tb_credit']:>14,.2f}  GL={e['gl_credit']:>14,.2f}  diff={e['credit_diff']:>12,.2f}")


if __name__ == "__main__":
    main()
