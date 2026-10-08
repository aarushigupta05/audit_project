"""
RULE 5: Duplicate Transaction Detection
----------------------------------------
Flags transactions where two or more ledger legs share:
  - Same account (exact match)
  - Same amount (exact match)
  - Same date (exact match)
  - Same dr_cr / entry_type direction
  - DIFFERENT voucher (entry_id)

Legs within the SAME voucher are excluded, as they represent the balancing sides
or multiple line items of a single legitimate transaction.

Sub-rupee postings to a rounding account (see _is_invoice_rounding) are not
considered: invoice rounding repeats by nature. A rounding-account posting of
Re.1 or more is still checked like any other.

KNOWN LIMITATION (investigated, not a bug -- decided to document rather than
suppress):
  Rule 5 matches on (date, account, amount, direction) only. It has no way to
  see voucher narration or a reference number, so it cannot distinguish a
  genuine erroneous repost from two real, independent transactions that
  happen to land on the same account, amount, and date.

  Case in point: a repeat-vendor pair (Purchase_524 / Payment_526)
  was investigated by reading the full source ledger pages (page 492 showing
  both legs balancing independently, page 101's cash book showing a running
  balance drop matching the payment) and confirmed to be two genuine,
  separate transactions -- not a parsing defect, not a duplicate posting.

  Decision: do NOT special-case this pattern out of the rule. A business
  with repeat counterparties and round, recurring amounts (e.g. a fixed
  monthly fee, a standard invoice size) will produce more of these
  coincidental matches than a business with varied transaction sizes --
  excluding them here would mean overfitting the rule to this one verified
  case and would risk hiding a real duplicate posting with the same shape in
  a different dataset. Rule 5's output is a CANDIDATE list for review, not a
  verdict; combined_report.py's convergence logic (a leg flagged by more
  than one rule ranks higher) is the intended way lower-confidence
  single-signal candidates like this get deprioritized automatically.

  UPDATE 2026-10-01: this specific pair no longer appears in Rule 5's output
  as of the Shape B account-attribution fix in reconstruct_ledger_entries.py.
  Payment_526's leg was itself mislabeled by that bug -- pre-fix it showed
  the vendor's name (the printed counter-party), when its real
  account (per its own ledger page) is "Cash", matching the page 101 cash-
  book evidence this limitation note already cites. So the original
  Purchase_524/Payment_526 match was partly an artifact of the very bug that
  got fixed, not purely the coincidental-recurring-counterparty case this
  note describes. The underlying LIMITATION (Rule 5 can't distinguish a
  coincidence from a real repost without narration/reference-number context)
  is still real and still undecided -- this update only corrects the record
  on which example demonstrated it. Left in place as documentation rather
  than deleted, since the general reasoning above still applies to whatever
  the next coincidental match turns out to be.
"""

import csv
import os
from collections import defaultdict

from rule_ledger_gstr_reconciliation import is_rounding_account

# Tally's invoice rounding never exceeds 50 paise; anything a rounding account
# holds below one rupee is rounding by design, and repeating is its nature.
ROUNDING_CEILING = 1.0

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
INPUT_FILE = os.path.join(DATA_DIR, "general_ledger.csv")


def _is_invoice_rounding(row):
    """A sub-rupee posting to a rounding account (Round Off, Rounding, ...).
    Two of these matching on date, account and amount is how rounding
    behaves, not a repeated entry -- they were 129 of the 215 duplicate flags
    on the FY2024-25 sample ledger and told a reviewer nothing. Rounding
    accounts are recognised by name pattern, never by one company's account
    name, and a posting of Re.1 or more (a real discount or adjustment) is
    still checked."""
    if not is_rounding_account(row.get("account")):
        return False
    try:
        return abs(float(row.get("amount", 0))) < ROUNDING_CEILING
    except (TypeError, ValueError):
        return False


def identify_duplicate_legs(rows):
    """
    Identifies duplicate legs across different vouchers.
    Returns:
      duplicate_info: dict mapping row index (int) -> reason string
      flagged_rows: list of flagged row dicts
    """
    groups = defaultdict(list)
    for idx, row in enumerate(rows):
        acct = (row.get("account") or "").strip()
        if _is_invoice_rounding(row):
            continue
        date = (row.get("date") or "").strip()
        dr_cr = (row.get("entry_type") or row.get("dr_cr") or "").strip()
        try:
            amt = f"{float(row.get('amount', 0)):.2f}"
        except ValueError:
            amt = (row.get("amount") or "").strip()

        key = (date, acct, amt, dr_cr)
        groups[key].append((idx, row))

    duplicate_info = {}
    flagged_indices = set()

    for key, items in groups.items():
        if len(items) < 2:
            continue
        vch_ids = set(r.get("entry_id") for idx, r in items)
        # Critical exclusion: Must span more than one distinct voucher
        if len(vch_ids) > 1:
            for idx, r in items:
                flagged_indices.add(idx)
                other_vchs = sorted(list(vch_ids - {r.get("entry_id")}))
                date, acct, amt, dr_cr = key
                duplicate_info[idx] = (
                    f"Duplicate transaction candidate: matches voucher(s) "
                    f"{', '.join(other_vchs[:3])} on {date} ({dr_cr} {acct} Rs.{amt})"
                )

    flagged_rows = [rows[idx] for idx in sorted(flagged_indices)]
    return duplicate_info, flagged_rows


def check_duplicate_transactions(rows):
    """Convenience function returning list of flagged rows (matching other rules)."""
    _, flagged_rows = identify_duplicate_legs(rows)
    return flagged_rows


if __name__ == "__main__":
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total entries in ledger: {len(rows)}")

    dup_info, flagged = identify_duplicate_legs(rows)

    print(f"Entries flagged (duplicate transaction candidate): {len(flagged)}")
    print()

    print("Sample flagged entries:")
    for row in flagged[:5]:
        print(f"  {row.get('entry_id')} | {row.get('date')} | {row.get('account')} | "
              f"amount={row.get('amount')} | type={row.get('entry_type')}")

    # Step 4 (VALIDATION ONLY): check against planted issues if column present
    if rows and "is_planted_issue" in rows[0]:
        true_positives = sum(
            1 for row in flagged
            if row.get("is_planted_issue") in ("duplicate_source", "duplicate_copy")
        )
        total_planted = sum(
            1 for row in rows
            if row.get("is_planted_issue") in ("duplicate_source", "duplicate_copy")
        )

        print()
        print("--- Validation against planted issues (test only) ---")
        print(f"Planted duplicate issues in data: {total_planted}")
        print(f"Correctly caught by our rule: {true_positives}")
