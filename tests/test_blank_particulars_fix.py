"""
Regression tests for the blank-counterparty voucher-header fix in
reconstruct_ledger_entries.py (fixed 2026-10-01, same day as the Shape B
account-attribution fix, but a separate and unrelated bug).

Background: a voucher header line with a genuinely blank/omitted
counter-party name -- e.g. "By Payment 292 5,00,000.00 1,00,00,906.17 Dr",
with nothing printed between "By" and the voucher type -- still matches
_WRAPPED_PARTICULARS_HEADER_RE (which has no particulars group at all), but
the surrounding code previously required the FOLLOWING source line to look
like a continuation of a two-line-wrapped printed name before accepting the
match. For a genuinely blank-particulars line, the real next line is just
the next unrelated transaction, so that check failed and the match was
discarded entirely: h_date/vch_type/vch_no fell back to None, and the line
fell through to the generic Detail-Leg regex, which wrongly consumed the
trailing running-balance figure as the "amount" and the garbled remainder
(including the real amount, now embedded as text) as "particulars" -- a
single fabricated leg silently injected into whatever voucher happened to
still be active on that page. This both lost the real transaction (its own
voucher never got created) and corrupted an unrelated voucher's balance.

Discovered investigating a Rs.55,00,000 J & K Bank Ltd credit-side gap
against the partnership firm's official trial balance (see
BASELINE_CHECKPOINT_V3.4.md and the investigation report, 2026-10-01).

The fix (collect_occurrences(), the _WRAPPED_PARTICULARS_HEADER_RE handling
block): when the next-line continuation check fails, treat the match as a
legitimate blank-particulars header instead of discarding it -- particulars
is set to "" explicitly, current_account is still attributed via the
existing Shape B branch (particulars == "" fails the "(as per details)"
check, so it falls into the Shape B else-branch, which already uses
current_account post the Shape B fix), and a dedicated
"[no counterparty name printed in source: <vch_type> <vch_no>]" narration
note is added for traceability, since there is no printed name to preserve.

These tests pin the fixed, verified-correct state against the REAL
committed data in data/ (same convention as test_shape_b_fix.py and
test_combined_report_regression.py).
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

ENTRIES_FILE = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_entries.csv")
GL_FILE = os.path.join(DATA_DIR, "general_ledger.csv")


@pytest.fixture(scope="module")
def real_entries_rows():
    if not os.path.exists(ENTRIES_FILE):
        pytest.skip(f"data/ledgers_redacted_reconstructed_entries.csv not present at {ENTRIES_FILE}")
    with open(ENTRIES_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. The 6 known recovered transactions, with REAL amounts ----------
# (entry_id, account, dr_cr, amount, source_page) -- verified against the raw
# PDF and, independently, against Tally's own printed cumulative running
# totals and a naive ground-truth regex re-scan (see the J & K Bank
# investigation). Every one of these sums to exactly Rs.55,00,000 on the
# credit side, closing the trial-balance gap exactly.
RECOVERED_LEGS = [
    ("Payment_292", "J & K Bank Ltd", "Cr", 500000.0, "433"),
    ("Payment_294", "J & K Bank Ltd", "Cr", 750000.0, "433"),
    ("Payment_299", "J & K Bank Ltd", "Cr", 1250000.0, "434"),
    ("Payment_420", "J & K Bank Ltd", "Cr", 1365000.0, "449"),
    ("Payment_428", "J & K Bank Ltd", "Cr", 735000.0, "450"),
    ("Payment_429", "J & K Bank Ltd", "Cr", 900000.0, "450"),
]

# The corrupted running-balance figures the pre-fix Detail-Leg fallthrough
# wrongly captured as "amount" for each of the 6 -- a leg with one of these
# amounts must never appear attributed to these vouchers again.
_GARBAGE_AMOUNTS_BY_VOUCHER = {
    "Payment_291": 10000906.17,  # contaminated by the Payment_292 header line
    "Payment_419": 4455834.97,   # contaminated by the Payment_420 header line
    "Receipt_1177": {4584177.65, 5484177.65},  # contaminated by Payment_429 and Payment_428
    "Receipt_837": 10520124.52,  # contaminated by the Payment_294 header line
    "Receipt_852": 9526937.21,   # contaminated by the Payment_299 header line
}


@pytest.mark.parametrize("entry_id,account,dr_cr,amount,source_page", RECOVERED_LEGS)
def test_recovered_legs_have_correct_account_and_real_amount(
    real_entries_rows, entry_id, account, dr_cr, amount, source_page
):
    matches = [
        r for r in real_entries_rows
        if r["entry_id"] == entry_id and r["dr_cr"] == dr_cr and r["particulars"] == account
    ]
    assert matches, (
        f"{entry_id}: expected a {dr_cr} leg on account {account!r} -- not found. "
        f"If this is failing, the blank-particulars fix may have regressed."
    )
    assert float(matches[0]["amount"]) == pytest.approx(amount, abs=0.01), (
        f"{entry_id}: amount drifted from the verified real transaction amount "
        f"(Rs.{amount}) -- check whether the running-balance fallthrough bug "
        f"has reappeared."
    )
    assert source_page in matches[0]["source_pages"], (
        f"{entry_id}: expected to be sourced from page {source_page}, got "
        f"{matches[0]['source_pages']!r}"
    )


def test_recovered_legs_sum_to_exactly_fifty_five_lakh(real_entries_rows):
    total = sum(
        float(r["amount"]) for r in real_entries_rows
        if r["entry_id"] in {eid for eid, *_ in RECOVERED_LEGS}
        and r["particulars"] == "J & K Bank Ltd" and r["dr_cr"] == "Cr"
    )
    assert total == pytest.approx(5500000.0, abs=0.01), (
        f"The 6 recovered J & K Bank Ltd credit legs should sum to exactly "
        f"Rs.55,00,000 (the trial-balance gap this fix closes); got Rs.{total:,.2f}"
    )


# ---------- 2. The 5 contaminated vouchers no longer carry a garbage leg ----------

@pytest.mark.parametrize("entry_id", sorted(_GARBAGE_AMOUNTS_BY_VOUCHER))
def test_contaminated_vouchers_no_longer_carry_garbage_leg(real_entries_rows, entry_id):
    legs = [r for r in real_entries_rows if r["entry_id"] == entry_id]
    assert legs, f"{entry_id} is missing from the reconstructed ledger entirely"

    garbage = _GARBAGE_AMOUNTS_BY_VOUCHER[entry_id]
    garbage_set = garbage if isinstance(garbage, set) else {garbage}
    leg_amounts = {float(r["amount"]) for r in legs}
    leaked = {a for a in leg_amounts if any(abs(a - g) < 0.01 for g in garbage_set)}
    assert not leaked, (
        f"{entry_id} still carries a garbage leg with the corrupted "
        f"running-balance amount {leaked} -- the blank-particulars fix "
        f"appears to have regressed."
    )
    assert all(r["entry_balanced"] == "True" for r in legs), (
        f"{entry_id} should now be balanced (2 real legs, no garbage leg) "
        f"after the fix, but has an unbalanced leg."
    )
    # Receipt_1177 had two garbage legs pre-fix (2 contaminating header lines
    # landed on it); every other voucher here had exactly one. Both cases
    # should settle at exactly 2 real legs post-fix.
    assert len(legs) == 2, (
        f"{entry_id}: expected exactly 2 legs after removing the garbage "
        f"leg(s), found {len(legs)}: {[(r['particulars'], r['dr_cr'], r['amount']) for r in legs]}"
    )


# ---------- 3. Population-wide guard: no garbled header text in the account column ----------

# Signature of the pre-fix corruption: the Detail-Leg regex consuming a whole
# blank-particulars header line as "particulars", which always starts with
# "To "/"By " followed by a voucher type and number, and always still
# contains a second embedded amount-like number (the real transaction
# amount that should have been its own field).
_GARBLED_HEADER_IN_ACCOUNT_RE = re.compile(
    r"^(To|By)\s+(Journal|Payment|Receipt|Contra|Sales|Purchase|Debit\s+Note|Credit\s+Note)\b.*[\d,]+\.\d{2}"
)


def test_no_account_value_looks_like_a_garbled_voucher_header(real_gl_rows):
    offenders = [
        r for r in real_gl_rows
        if _GARBLED_HEADER_IN_ACCOUNT_RE.match((r.get("account") or "").strip())
    ]
    assert not offenders, (
        f"{len(offenders)} row(s) in general_ledger.csv have an account value "
        f"that looks like a whole un-parsed voucher header line (the exact "
        f"shape of the blank-particulars bug) rather than a real account "
        f"name -- e.g. {offenders[:3]}. This means the Detail-Leg fallthrough "
        f"is swallowing blank-particulars headers again somewhere in the "
        f"926-page population, not just the 6 known instances."
    )


# ---------- 4. Trial-balance reconciliation: J & K Bank Ltd closes to zero ----------

def test_jk_bank_reconciles_against_trial_balance():
    """The whole point of this fix: the firm's official Tally trial balance
    showed a Rs.55,00,000 credit-side gap for J & K Bank Ltd against the
    reconstructed ledger. This pins that reconcile_trial_balance.py now
    reports it as clean (both debit and credit sides within tolerance)."""
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    tb_pdf = os.path.join(DATA_DIR, "raw_pdfs", "trial_balance_redacted.pdf")
    if not os.path.exists(tb_pdf):
        pytest.skip(f"trial balance PDF not present at {tb_pdf}")

    from reconcile_trial_balance import reconcile

    result = reconcile()
    jk_bank = [e for e in result["clean"] + result["mismatched"] if e["account"] == "J & K Bank Ltd"]
    assert jk_bank, "J & K Bank Ltd not found in the trial-balance reconciliation at all"
    entry = jk_bank[0]
    assert entry["credit_diff"] <= 1.0, (
        f"J & K Bank Ltd credit-side gap against the trial balance has not "
        f"closed: diff=Rs.{entry['credit_diff']:,.2f} (TB=Rs.{entry['tb_credit']:,.2f}, "
        f"GL=Rs.{entry['gl_credit']:,.2f}). The blank-particulars fix appears "
        f"to have regressed."
    )
    assert entry in result["clean"], (
        "J & K Bank Ltd is no longer in the mismatched bucket on the credit "
        "side, but still shows up outside the clean bucket -- check the "
        "debit side too."
    )
