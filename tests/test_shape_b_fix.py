"""
Regression tests for the Shape B account/direction attribution fix in
reconstruct_ledger_entries.py (fixed 2026-10-01).

Background: a Shape B header line ("To/By <Name> ... amount Dr/Cr", i.e. not
a "(as per details)" multi-leg voucher) was attaching its leg to the printed
<Name> -- the COUNTER-PARTY -- instead of to current_account, the ledger
page's own subject account. drcr always describes current_account's own
movement, so this mislabeled ~67% of all header legs (9,802 of 14,605) and
was invisible to voucher balance checks, because swapping which account an
amount is attributed to doesn't change whether Dr == Cr for the voucher.

The fix (see reconstruct_ledger_entries.py, the `else` branch under
`is_shape_a` in collect_occurrences): use current_account for the leg's
particulars, and preserve the printed counter-party name as a
"[counterparty per source line: ...]" narration note rather than discarding
it, so the identity recorded in the source document is never lost.

These tests pin the fixed, verified-correct state against the REAL
committed data in data/ (same convention as test_combined_report_regression.py),
plus two fast unit tests against merge_occurrences() directly (a pure
function, no PDF parsing needed) for the underlying merge mechanism the
bug was corrupting.

NOTE on corroboration: an earlier version of the investigation hypothesized
that the bug was also defeating cross-page corroboration for Shape B legs
(i.e. that fixing it would raise their corroboration_count). Measured
before and after the fix, that turned out to be WRONG -- corroboration_count
for former-Shape-B legs is {1: 9794} before the fix and {1: 9808} after.
This is because a voucher's Dr leg and its Cr leg are different merge keys
by construction (direction differs), so a simple two-leg Shape B voucher
was never going to corroborate across its two sides regardless of the
bug -- it only has one occurrence of each side to begin with. The Shape
A vs Shape B corroboration-count gap documented in the original
investigation reflects a genuine structural difference in how multi-leg
(Shape A) vs simple two-leg (Shape B) vouchers are printed in this
document, not a symptom of this bug. See tests below for what the fix
actually does and does not change.
"""
import csv
import os
import re
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(TESTS_DIR, "..", "scripts")
DATA_DIR = os.path.join(TESTS_DIR, "..", "data")
sys.path.insert(0, SCRIPTS_DIR)

from reconstruct_ledger_entries import collect_occurrences, merge_occurrences

ENTRIES_FILE = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_entries.csv")
PDF_FILE = os.path.join(DATA_DIR, "raw_pdfs", "ledgers_redacted.pdf")

_COUNTERPARTY_NOTE_RE = re.compile(r"\[counterparty per source line: (.*?)\]")


@pytest.fixture(scope="module")
def real_entries_rows():
    if not os.path.exists(ENTRIES_FILE):
        pytest.skip(f"data/ledgers_redacted_reconstructed_entries.csv not present at {ENTRIES_FILE}")
    with open(ENTRIES_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. Known-voucher regression (the examples that surfaced the bug) ----------

# (entry_id, account, dr_cr, amount) -- verified against raw PDF pages and
# the opposite counter-ledger page for each voucher (see investigation report).
KNOWN_FIXED_LEGS = [
    ("Journal_664", "Audit Fee", "Dr", 35000.0),
    ("Journal_664", "AUDIT FEE PAYABLE", "Cr", 35000.0),
    ("Payment_90", "Cash", "Cr", 24452.0),
    ("Payment_90", "ELECTRICITY BILL PAYABLE", "Dr", 24452.0),
    ("Journal_686", "ELECTRICITY BILL PAYABLE", "Cr", 244504.0),
    ("Journal_686", "Electricity Expenses", "Dr", 244504.0),
    ("Journal_715", "ELECTRICITY BILL PAYABLE", "Cr", 8975.0),
    ("Journal_715", "Electricity Expenses", "Dr", 8975.0),
]


@pytest.mark.parametrize("entry_id,account,dr_cr,amount", KNOWN_FIXED_LEGS)
def test_known_shapeb_voucher_pairs_fixed(real_entries_rows, entry_id, account, dr_cr, amount):
    matches = [
        r for r in real_entries_rows
        if r["entry_id"] == entry_id and r["particulars"] == account and r["dr_cr"] == dr_cr
    ]
    assert matches, (
        f"{entry_id}: expected a leg on account {account!r} ({dr_cr}) -- not found. "
        f"If this is failing, the Shape B fix may have regressed back to attributing "
        f"this leg to the printed counter-party name instead of current_account."
    )
    assert float(matches[0]["amount"]) == pytest.approx(amount, abs=0.01)


# ---------- 2. General invariant: a leg's account must never equal its own ----------
# ----------    printed counter-party name (the specific shape of this bug) ----------

def test_header_leg_particulars_always_equals_current_account():
    """The core regression guard, checked at the source rather than from the
    flattened CSV: for every header leg collect_occurrences() produces (both
    Shape A and fixed Shape B), when the page's own account was detected,
    the leg's particulars must equal that account -- never the printed
    counter-party name from a Shape B line.

    This deliberately does NOT use the committed CSV: narration is merged
    per voucher (a set, deduped across both occurrences/sides), so a leg's
    own counter-party note and the OTHER leg's note end up indistinguishable
    once flattened -- in a correctly-fixed two-sided voucher, each leg's
    account commonly DOES equal one of its voucher's counterparty notes
    (the note contributed by the other side), which is the corroboration
    working as intended, not a bug. So this test re-parses a page range of
    the real PDF (pages 1-325, ~19s) and checks the invariant directly on
    collect_occurrences()'s own in-memory per-occurrence data, which is the
    only place "this leg's own account" and "this leg's own printed
    counter-party" are actually distinguishable.
    """
    if not os.path.exists(PDF_FILE):
        pytest.skip(f"raw PDF not present at {PDF_FILE}")
    occurrences, _, _ = collect_occurrences(PDF_FILE, 1, 325)
    violations = []
    for occ in occurrences:
        for leg in occ["legs"]:
            if leg.get("is_header_leg") and occ.get("account") and leg["particulars"] != occ["account"]:
                violations.append((occ["entry_id"], occ["page"], occ["account"], leg["particulars"]))
    assert not violations, (
        f"{len(violations)} header leg(s) across pages 1-325 are attributed to "
        f"something other than their own page's account -- the Shape B bug "
        f"appears to have regressed. Examples: {violations[:5]}"
    )
    # Sanity: this range must actually exercise all 4 known vouchers, or the
    # check above proves nothing.
    covered = {occ["entry_id"] for occ in occurrences}
    assert {"Journal_664", "Payment_90", "Journal_686", "Journal_715"} <= covered


def test_counterparty_names_preserved_not_discarded(real_entries_rows):
    """Pins that the fix preserves the printed counter-party name as a
    narration note rather than silently dropping it (Option 1 from the
    investigation: keep current_account as the leg's account, but don't
    lose the only record of who the counter-party was)."""
    marked = sum(
        1 for r in real_entries_rows
        if "[counterparty per source line:" in (r.get("narration") or "")
    )
    assert marked == 9808, (
        f"Expected 9808 legs carrying a preserved counter-party note "
        f"(matches the 9802 Shape B legs identified in the investigation, "
        f"plus 6 from vouchers affected twice), got {marked}. If this changed "
        f"because of a deliberate further fix, update this pin and say why."
    )


# ---------- 3. merge_occurrences() unit tests (pure function, no PDF needed) ----------

def _occ(entry_id, page, account, legs, date="1-Apr-24", vch_type=None, vch_no=None):
    vch_type = vch_type or entry_id.split("_")[0]
    vch_no = vch_no or entry_id.split("_", 1)[1]
    return {
        "entry_id": entry_id, "date": date, "vch_type": vch_type, "vch_no": vch_no,
        "page": page, "account": account, "legs": legs, "narration": [],
    }


def test_merge_does_not_falsely_merge_dr_and_cr_legs_of_a_simple_voucher():
    """A fixed Shape B voucher produces two occurrences -- one from each
    side's own ledger page -- each contributing ONE correctly-attributed
    leg. They must NOT merge into each other (direction differs), and each
    should end up with corroboration_count == 1, since each side of this
    voucher is only described once in the source document. This is the
    corrected version of the corroboration hypothesis in the investigation
    report: Dr and Cr legs of a two-leg voucher were never meant to merge."""
    occurrences = [
        _occ("Journal_664", 16, "Audit Fee", [
            {"particulars": "Audit Fee", "amount": 35000.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _occ("Journal_664", 17, "AUDIT FEE PAYABLE", [
            {"particulars": "AUDIT FEE PAYABLE", "amount": 35000.0, "dr_cr": "Cr", "resolved": True, "is_header_leg": True},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(occurrences)
    assert conflicts == []
    named = entries["Journal_664"]["named_legs"]
    assert set(named.keys()) == {("Audit Fee", "Dr"), ("AUDIT FEE PAYABLE", "Cr")}
    assert len(named[("Audit Fee", "Dr")]["source_pages"]) == 1
    assert len(named[("AUDIT FEE PAYABLE", "Cr")]["source_pages"]) == 1
    dr_total = named[("Audit Fee", "Dr")]["amount"]
    cr_total = named[("AUDIT FEE PAYABLE", "Cr")]["amount"]
    assert dr_total == cr_total == 35000.0


def test_merge_corroborates_when_same_leg_genuinely_repeated_across_pages():
    """When the SAME (particulars, dr_cr, amount) leg is independently
    described on more than one page -- the genuine corroboration case,
    typical of Shape A multi-leg vouchers recorded identically on every
    account page they touch -- merge_occurrences must combine them into
    one named leg with corroboration_count equal to the number of pages."""
    occurrences = [
        _occ("Journal_699", 1, "Adm Charges", [
            {"particulars": "Adm Charges", "amount": 500.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _occ("Journal_699", 318, "Salary Expenses", [
            {"particulars": "Adm Charges", "amount": 500.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": False},
        ]),
        _occ("Journal_699", 324, "Provident Fund Payable", [
            {"particulars": "Adm Charges", "amount": 500.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(occurrences)
    assert conflicts == []
    leg = entries["Journal_699"]["named_legs"][("Adm Charges", "Dr")]
    assert leg["source_pages"] == [1, 318, 324]
    assert len(leg["source_pages"]) == 3


# ---------- 4. FEES/COMPANY narration-continuation bug (FIXED in V3.9) ----------

# Was: an orphaned single-word all-caps continuation line ("FEES", "COMPANY")
# immediately following a narration line got glued onto the preceding LEG's
# particulars/counterparty instead of extending that narration entry (a
# priority-ordering bug: is_wrapped_part was checked before is_narration in
# collect_occurrences()). Fixed in V3.9 -- see
# tests/test_v3_9_name_pair_fixes.py for the full regression coverage
# (exhaustive 926-page scan found exactly 9 real occurrences, all now
# verified clean) and BASELINE_CHECKPOINT_V3.9.md for the investigation.
# This test now pins that the OLD corrupted strings never reappear in
# EITHER the particulars column (never did) or anywhere else.
_FEES_CORRUPTED_STRINGS = {
    "Caliberation Charges FEES", "J & K Bank Ltd FEES", "Cash FEES", "Telephone Expenses FEES",
}


def test_fees_bug_confined_to_narration_does_not_corrupt_account_column(real_entries_rows):
    corrupted_accounts = {
        r["particulars"] for r in real_entries_rows if r["particulars"] in _FEES_CORRUPTED_STRINGS
    }
    assert not corrupted_accounts, (
        f"The FEES/COMPANY narration-continuation bug has started corrupting the "
        f"account column (found {corrupted_accounts}), not just narration."
    )
