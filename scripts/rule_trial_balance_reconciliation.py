"""
RULE: Trial Balance vs General Ledger Reconciliation
--------------------------------------------------------
A standard, direct audit check that was already built and used as internal
validation infrastructure during the V3.x ledger reconstruction (see
reconcile_trial_balance.py), but never exposed as a user-facing rule in
its own right. This module is that exposure: it wraps reconcile_trial_balance
.reconcile() -- the official Tally trial balance (data/raw_pdfs/
trial_balance_redacted.pdf, same FY as the ledger) against the reconstructed
general_ledger.csv, per account -- and turns its mismatches into
severity-tiered flags, the same shape every other rule_*.py in this project
produces.

Why this one is unusually strong evidence: the trial balance is the firm's
own OFFICIAL Tally output for the same period, independent of every step
reconstruct_ledger_entries.py took to rebuild the ledger from the raw PDF
text. If the two don't agree on an account's Dr/Cr totals, that's a direct,
provable discrepancy, not an inferred pattern -- the same category of
check as Rule 4 (ITR vs 26AS) and the GSTR-1/GSTR-3B rule, just applied to
the ledger itself instead of a tax filing.

CURRENT REAL-DATA RESULT (2026-10-02, re-verified when this rule was
built): all 82 trial-balance accounts with a matching ledger account
reconcile cleanly (0 mismatched) -- see
test_trial_balance_reconciliation.py's real-data test for the pinned
figures. reconcile_trial_balance.py's own module docstring, written during
an earlier investigation (2026-10-01), still describes two of these
accounts (Sales, Round Off/Discount) as NOT reconciling -- that was true at
the time, but the later V3.8/V3.9 canonicalization fixes resolved both
(already pinned by test_account_canonicalization_fix.py and
test_cgst_sgst_canonicalization_fix.py); that docstring has been corrected
separately to stop describing a problem that no longer exists.

Unmatched trial-balance rows (49 of them currently -- Tally group/category
headers like "Loans (Liability)" and zero-activity fixed asset lines) are
NOT treated as flags here, consistent with reconcile_trial_balance.py's own
established finding that these are expected structural rows, not missing
accounts -- see that module's docstring for the evidence behind that call.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from reconcile_trial_balance import reconcile, TOLERANCE_RUPEES


def classify_severity(mismatch_amount, reference_amount):
    """Same severity ladder as every other reconciliation rule in this
    project (rule_itr_26as_reconciliation.classify_severity,
    rule_gstr_reconciliation.classify_severity) -- not imported, to keep
    this module standalone/independently runnable like every other
    rule_*.py here. CRITICAL for a zero reference with a real nonzero
    mismatch, NONE for a genuine zero/zero, and the usual >=30/>=10/>0
    percentage bands otherwise."""
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


def check_trial_balance_reconciliation(tb_rows=None, gl_rows=None):
    """Returns a flat list of flag dicts, one per side (debit/credit) of
    an account whose ledger total doesn't match the trial balance's
    figure beyond reconcile_trial_balance.TOLERANCE_RUPEES. Each flag:
        {account, side, tb_amount, gl_amount, mismatch_amount, severity,
         variants_used}
    """
    result = reconcile(tb_rows=tb_rows, gl_rows=gl_rows)
    flags = []

    for entry in result["mismatched"]:
        if entry["debit_diff"] > TOLERANCE_RUPEES:
            flags.append({
                "account": entry["account"],
                "side": "debit",
                "tb_amount": entry["tb_debit"],
                "gl_amount": entry["gl_debit"],
                "mismatch_amount": round(entry["gl_debit"] - entry["tb_debit"], 2),
                "severity": classify_severity(entry["debit_diff"], entry["tb_debit"]),
                "variants_used": entry["variants_used"],
            })
        if entry["credit_diff"] > TOLERANCE_RUPEES:
            flags.append({
                "account": entry["account"],
                "side": "credit",
                "tb_amount": entry["tb_credit"],
                "gl_amount": entry["gl_credit"],
                "mismatch_amount": round(entry["gl_credit"] - entry["tb_credit"], 2),
                "severity": classify_severity(entry["credit_diff"], entry["tb_credit"]),
                "variants_used": entry["variants_used"],
            })

    return flags


if __name__ == "__main__":
    flags = check_trial_balance_reconciliation()
    print(f"Trial balance vs ledger discrepancies: {len(flags)}")
    print()
    if flags:
        for f in flags:
            print(f"  [{f['severity']}] {f['account']} ({f['side']}): "
                  f"TB=Rs.{f['tb_amount']:,.2f} GL=Rs.{f['gl_amount']:,.2f} "
                  f"(gap: Rs.{f['mismatch_amount']:,.2f})")
    else:
        print("  All accounts reconcile within tolerance "
              f"(Rs.{TOLERANCE_RUPEES:.2f}).")
