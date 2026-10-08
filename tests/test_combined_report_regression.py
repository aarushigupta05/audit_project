"""
Regression suite against the REAL, committed data in data/ -- not fixtures.

This formalizes the verification that was previously done by hand, one
script at a time, throughout the V3.3 ledger-reconstruction freeze and the
first real run of combined_report.py: specific numbers were computed,
checked against raw PDF evidence, and accepted as correct. This file pins
those already-verified numbers so a future change (a rule tweak, a
reconstruction fix, a schema adapter edit) that silently shifts them gets
caught immediately, instead of requiring another full manual forensic
pass like the V3.1/V3.2 baseline drift did.

If a test here fails after a deliberate, verified change (e.g. a new
reconstruction fix that legitimately changes voucher counts), that is
expected -- update the pinned value AND explain why in the commit message,
the same discipline BASELINE_CHECKPOINT_V3.3.md already follows. A failure
here should never be silenced without that explanation.
"""
import csv
import os

import pytest

import combined_report as cr

DATA_DIR = cr.DATA_DIR
GL_FILE = cr.GL_FILE

# The 5 vouchers that were corrupted pre-V3.3 by the fused vch_no+amount
# parsing bug (x_tolerance=3 merged "Journal 87" with "1,52,000.00" into
# "Journal 871,52,000.00", etc.) and their verified-correct totals, cross-
# checked against the raw PDF in BASELINE_CHECKPOINT_V3.3.md Section 4.
TARGET_VOUCHER_CORRECT_DR_TOTAL = {
    "Receipt_61": 113261.53,
    "Receipt_64": 216288.00,
    "Receipt_73": 111109.00,
    "Receipt_79": 109789.24,
    "Journal_87": 152000.00,
}

# The "ghost" vouchers that the fusion bug created via date-disambiguation
# (e.g. a truncated "Journal 8" with an inflated amount, wrongly treated as
# a different voucher from the already-existing real Journal_8). These must
# never reappear in the reconstructed ledger.
GHOST_VOUCHER_IDS = {
    "Journal_8_10-Jun-24",
    "Receipt_6_15-Apr-24",
    "Receipt_6_16-Apr-24",
    "Receipt_7_18-Apr-24",
    "Receipt_7_20-Apr-24",
}


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def real_report():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    return cr.run_combined_report(write_exports=False)


# ---------- V3.3 ledger reconstruction invariants ----------

def test_total_leg_count_pinned(real_gl_rows):
    """Re-pinned 2026-10-01 after the Sales/Purchase/Round-Off-Discount
    account-name canonicalization fix (V3.7, previously 17128). That fix
    consolidates 808 genuinely double-counted cross-page legs (391 Sales +
    416 Round Off/Discount + 1 Purchase) into single corroborated legs --
    see BASELINE_CHECKPOINT_V3.7.md and
    tests/test_account_canonicalization_fix.py for the full investigation
    and population verification.

    Re-pinned again 2026-10-02 after the CGST/SGST canonicalization fix
    (V3.8, previously 16320). That fix consolidates a further 1,558 rows
    (779 CGST + 779 SGST cross-variant vouchers) -- see
    BASELINE_CHECKPOINT_V3.8.md and
    tests/test_cgst_sgst_canonicalization_fix.py.

    Re-pinned again 2026-10-02 after the V3.9 name-pair fixes (previously
    14762). 91 further rows consolidated: 2 pairs via
    _ACCOUNT_NAME_CANONICALIZATION (JANDIYAL ELECTRONICS/PARTNER, KC
    EDU.SOCIETY) and 6 pairs via the new per-voucher
    _PER_VOUCHER_NAME_ALIASES mechanism. This also resolves all 77 of the
    originally-flagged flip-77 vouchers (120 -> 29 unbalanced overall). See
    BASELINE_CHECKPOINT_V3.9.md and tests/test_v3_9_name_pair_fixes.py.

    Re-pinned again 2026-10-02 investigating the remaining 29 unbalanced
    vouchers post-V3.9 (previously 14671). -46 rows total, from two
    independent, unrelated mechanisms, both consolidating genuinely
    double-counted/double-represented legs rather than losing data:

    Patterns 1-3 (-24 named legs): 4 new _ACCOUNT_NAME_CANONICALIZATION
    entries (two employees' salary A/c headers, EDLI
    Account, DISCOUNT Account -- each proven via exact Carried-Over/
    Brought-Forward balance continuity with their long-form page(s)) fold
    11 header-leg occurrences into their canonical spelling, plus 1 further
    DISCOUNT dedup via the pre-existing Pass-1 redundant-header-leg rule;
    1 new _PER_VOUCHER_NAME_ALIASES entry (Tr. A/c -> Ledger Tr. A/c)
    consolidates 12 "Carriage Expenses Consolidated" vouchers. Net: 21 of
    the 29 unbalanced vouchers resolved, 0 new conflicts.

    Pattern 4 (-22 orphan legs, 0 named legs): the redacted-account orphan-
    absorption rule in merge_occurrences() removes all 22 orphan legs
    (particulars=None) from the 6 affected vouchers (Journal_604/671/672/
    684/687/690) by folding each into the ALREADY-EXISTING named
    "[Redacted account -- TB opening ...]" leg in the same voucher that
    shares its exact amount and dr_cr -- these were the same physical leg
    counted twice (once via the redacted account's own synthetic header
    leg, once via the blanked-out counterparty text on another account's
    page, which parsed as an orphan). Gated on an exhaustive, read-only,
    whole-document bounded simulation BEFORE this code was written: all 22
    orphan legs in the full population resolved to exactly 1 eligible
    candidate each, 0 ambiguous, 0 unmatched, exactly the 6 known vouchers
    -- see the checkpoint doc for the full simulation report. Resolves the
    remaining 6 of the 29 originally-unbalanced vouchers (29 -> 2 remain:
    Payment_2/Sales_Payment, Pattern 5, deliberately deferred -- see
    tests/test_v3_9_name_pair_fixes.py::test_no_new_unbalanced_vouchers_introduced)."""
    assert len(real_gl_rows) == 14625


def test_canonical_voucher_count_pinned(real_gl_rows):
    assert len({r["entry_id"] for r in real_gl_rows}) == 6216


def test_no_ghost_vouchers_present(real_gl_rows):
    present_ids = {r["entry_id"] for r in real_gl_rows}
    leaked_ghosts = present_ids & GHOST_VOUCHER_IDS
    assert not leaked_ghosts, (
        f"Ghost voucher(s) from the pre-V3.3 fusion bug have reappeared: "
        f"{leaked_ghosts}. This means the x_tolerance=1.5 fix in "
        f"reconstruct_ledger_entries.py regressed."
    )


@pytest.mark.parametrize("entry_id,expected_total", TARGET_VOUCHER_CORRECT_DR_TOTAL.items())
def test_target_voucher_amount_and_balance(real_gl_rows, entry_id, expected_total):
    legs = [r for r in real_gl_rows if r["entry_id"] == entry_id]
    assert legs, f"{entry_id} is missing from the reconstructed ledger entirely"
    assert all(r["entry_balanced"] == "True" for r in legs), (
        f"{entry_id} has an unbalanced leg -- was balanced in the V3.3 freeze"
    )
    dr_total = sum(float(r["amount"]) for r in legs if r["dr_cr"] == "Dr")
    assert dr_total == pytest.approx(expected_total, abs=0.01), (
        f"{entry_id} Dr total drifted from the verified V3.3 amount "
        f"(Rs.{expected_total}) to Rs.{dr_total} -- re-verify against the "
        f"raw PDF before accepting this."
    )


# ---------- combined_report.py output invariants ----------

def test_benford_chi_square_pinned(real_report):
    """Re-pinned 2026-10-01 alongside the blank-particulars-header fix in
    reconstruct_ledger_entries.py (previously 288.29). That fix recovered 6
    real transaction amounts (summing to Rs.55,00,000) and removed 6
    fabricated running-balance amounts (summing to ~Rs.4.46 crore) that had
    been mis-parsed as leg amounts -- changing the population of amounts
    Benford's Law runs over. The shift is small and in the expected
    direction (fewer non-transaction numbers in the dataset).

    Re-pinned again 2026-10-01 after the Sales/Purchase/Round-Off-Discount
    canonicalization fix (V3.7, previously 287.15). Benford counts every
    GL row once; the 808 consolidated cross-variant legs were previously
    counted TWICE each (once per spelling) with the identical amount, so
    this shift is a correction of inflated duplicate counts, not new
    noise. Still far above the deviation threshold both before and after
    -- the dataset-level conclusion is unchanged.

    Re-pinned again 2026-10-02 after the CGST/SGST canonicalization fix
    (V3.8, previously 296.93). Same mechanical cause: 1,558 further rows
    (779 CGST + 779 SGST) were each counted twice, now once. Still far
    above the deviation threshold -- conclusion unchanged.

    Re-pinned again 2026-10-02 after the V3.9 name-pair fixes (previously
    227.06). Same mechanical cause: 91 further rows were each counted
    twice (once per spelling), now once. Still far above the deviation
    threshold -- conclusion unchanged. See BASELINE_CHECKPOINT_V3.9.md.

    Re-pinned again 2026-10-02 investigating the remaining 29 unbalanced
    vouchers post-V3.9 (previously 228.15). Same mechanical cause as every
    prior re-pin here: the leg population Benford runs over shrank by 46
    (see test_total_leg_count_pinned for the exact breakdown -- 24 from
    Patterns 1-3's spelling consolidation, 22 from Pattern 4's orphan-leg
    absorption), each removed row a duplicate/double-represented copy of an
    amount already counted once elsewhere, not a new or lost transaction.
    Still far above the deviation threshold -- conclusion unchanged."""
    assert real_report["benford"]["chi_square"] == pytest.approx(229.74, abs=0.01)
    assert real_report["benford"]["deviates"] is True


def test_benford_mad_pinned(real_report):
    """ADDED 2026-10-02: Nigrini's MAD, a sample-size-independent second
    opinion alongside (not replacing) chi-square above. Read-only segment
    analysis (scripts/analyze_benford_segmentation.py) found chi-square
    deviates in EVERY voucher-type/account slice of this dataset, including
    the least-repetitive one -- consistent with chi-square's threshold
    being miscalibrated for a dataset this large (n=14,625) rather than
    the data being manipulated. MAD here lands in "Acceptable conformity",
    directly contradicting chi-square's "deviates" verdict -- both numbers
    are reported, deliberately, rather than picking one. If this value
    moves alongside a future ledger reconstruction fix, re-verify with a
    fresh `python3 scripts/rule_benfords_law.py` run before re-pinning,
    the same way test_benford_chi_square_pinned above has been each time."""
    assert real_report["benford"]["mad"] == pytest.approx(0.01061, abs=0.00001)
    assert real_report["benford"]["mad_conformity"] == "Acceptable conformity"


def test_tax_reconciliation_clean(real_report):
    """Re-verified 2026-10-02 after replacing data/form26as.csv and
    data/itr_summary.csv with figures extracted directly from the real
    source PDFs (see scripts/adapt_tax_docs_to_schema.py and
    tests/test_tax_docs_adapter.py). The previous committed data in these
    two files was real, but for a different taxpayer -- documents that
    ended up in this project's data/ folder alongside the firm's
    (general_ledger.csv's subject). The old data's "independently
    hand-verified as clean" note was true, just about that other filing,
    not this one -- set aside in a local backup folder kept out of the
    repository, not deleted.

    With the real data, zero mismatches is genuinely verified, not
    assumed: the one real deductor (Hindustan Petroleum Corporation
    Limited, TAN MUMH09973F) shows Rs.32,018 TDS deposited per the real
    Annual Tax Statement, and Rs.32,018 claimed per the real Computation
    of Income's own TDS schedule -- the same figure, independently
    confirmed from two different real documents."""
    assert real_report["tax_reconciliation_flags"] == []


def test_trial_balance_reconciliation_merged_and_clean(real_report):
    """ADDED 2026-10-02: Rule 6 (trial balance vs ledger reconciliation,
    scripts/rule_trial_balance_reconciliation.py) merged into
    combined_report.py's output -- per-user decision, distinguishing this
    from the GSTR rule (kept standalone because it checks an unrelated,
    non-overlapping period): Rule 6 operates on the exact same ledger and
    period as every other rule here, with no timeframe mismatch to guard
    against, so it belongs in the same report.

    All 82 matched trial-balance accounts reconcile cleanly against the
    real V3.10 ledger (0 mismatches) -- see
    tests/test_rule_trial_balance_reconciliation.py for the full real-data
    and stress-test coverage of the rule itself; this test only pins that
    the merge wired it into combined_report.py's output correctly."""
    assert "trial_balance_flags" in real_report
    assert real_report["trial_balance_flags"] == []


def test_leg_flag_counts_pinned(real_report):
    """Re-pinned 2026-10-01 after the Shape B account-attribution fix in
    reconstruct_ledger_entries.py (previously 1437/8/1429). The fix relabeled
    9,802 legs from a printed counter-party name to the page's own real
    account; Rule 5 (duplicate_transaction) matches on account name, so it
    was directly affected. The full before/after delta was investigated leg
    by leg: 32 flags removed, 10 added, net -22, and every single one of the
    42 traces cleanly to a leg whose account the fix corrected (either
    directly, or a sibling leg in the same match group). No unexplained
    residue. See the investigation for the full breakdown, including the
    repeat-vendor pair (this rule's documented known-limitation
    example) which no longer fires because the leg that triggered it was
    itself mislabeled pre-fix.

    Re-pinned again 2026-10-01 after the blank-particulars-header fix
    (previously 1415/8/1407). That fix recovered 6 real J & K Bank Ltd
    credit legs that previously didn't exist at all (the header line that
    should have created them was being discarded and its contents mis-
    parsed as a garbage detail leg elsewhere). 4 of the 6 happen to be
    round-number amounts (Rule 1), so they each pick up one new flag; the
    other 2 (Payment_420, Payment_428) aren't round numbers and don't flag.
    Net +4, fully traced -- see the investigation. No new duplicate
    (Rule 5) flags: none of the 6 new legs coincides with another voucher
    on (date, account, amount, direction).

    Re-pinned again 2026-10-01 after the Sales/Purchase/Round-Off-Discount
    canonicalization fix (V3.7, previously 1419/8/1411). Full key-level
    decomposition (entry_id, account, amount): 201 flags removed, 76
    added, net -125, zero unexplained in either direction -- every single
    changed flag's account is one of {Sales, Sales Account, Purchase,
    Purchase Account, Round Off/Discount, Round Off /Discount}. Of the
    1,214 flags common to both runs, 0 changed severity or source_rules.
    Multi-signal count and its exact entry list are unchanged (8, same
    Receipt_19/20/87/88 and Sales Cash_MPSS/24-25/814 group). See
    BASELINE_CHECKPOINT_V3.7.md for the full breakdown.

    Re-pinned again 2026-10-02 after the CGST/SGST canonicalization fix
    (V3.8, previously 1294/8/1286). Full key-level decomposition
    (entry_id, account, amount, severity, source_rules): 12 flags removed,
    0 added, zero unexplained -- every one of the 12 removed flags' account
    is CGST Account or SGST Account, all LOW severity, all
    duplicate_transaction-only. Multi-signal set unchanged (8, identical
    entries). See BASELINE_CHECKPOINT_V3.8.md.

    Unchanged by V3.9 (checked, not re-pinned): the 91 rows consolidated by
    the V3.9 name-pair fixes are each a unique debtor name used by only one
    voucher, so Rule 5 (duplicate_transaction, which matches across
    DIFFERENT vouchers sharing date+account+amount) never matched between a
    pair's short and long spelling in the first place -- full key-level
    diff (entry_id, account, amount, severity, source_rules) against the
    V3.8 baseline: 0 removed, 0 added. See BASELINE_CHECKPOINT_V3.9.md.

    Re-pinned again 2026-10-02 investigating the remaining 29 unbalanced
    vouchers post-V3.9 (previously 1282/8/1274). Full key-level
    decomposition (entry_id, account, amount): 11 flags removed, 0 added,
    zero unexplained, multi-signal set unchanged (8, identical entries).
    Every one of the 11 traces to one of the two Patterns 1-3/4
    mechanisms, and in every case the SURVIVING flag (same amount, same
    severity, same source_rules) is still present under its canonical
    representation -- nothing was lost, only a duplicate copy of an
    already-flagged leg was removed:
      - 3x employee salary A/c (header spelling variant) Rs.30,000 MEDIUM round_number_bias
        (Journal_657/658/659): before Pattern 1's canonicalization, this
        voucher's salary leg was double-counted as two separate named legs
        (one per spelling), each independently flagged; canonicalizing the
        header folds them into one leg under the canonical spelling,
        which was ALREADY flagged and remains flagged, unchanged.
      - 8x 'UNRESOLVED' HIGH round_number_bias (Journal_684: 2x Rs.360,000
        + 2x Rs.840,000; Journal_687: 2x Rs.210,000 + 2x Rs.490,000): these
        were the orphan-leg occurrences Pattern 4 absorbs -- each one a
        second, unparseable-counterparty copy of a leg already carried by
        an existing '[Redacted account -- TB opening ...]' named leg of
        the identical amount, which was ALREADY flagged and remains
        flagged, unchanged. See test_total_leg_count_pinned for the full
        mechanism and the bounded-simulation gate this was proven against
        before the code was written."""
    # Re-pinned 2026-10-07 (Addendum 21): sub-rupee postings to a rounding
    # account no longer count as duplicate candidates (they repeat by nature).
    # 129 legs -- all Round Off/Discount, all 50 paise or less -- left the
    # list: 1,271 -> 1,142. Multi-signal is unchanged at 8 (none of them was
    # a rounding leg), so single-signal falls by the same 129: 1,263 -> 1,134.
    summary = real_report["summary"]
    assert summary["total_legs_flagged"] == 1142
    assert summary["multi_signal"] == 8
    assert summary["single_signal"] == 1134


def test_severity_distribution_pinned(real_report):
    """Re-pinned 2026-10-01 alongside test_leg_flag_counts_pinned -- see that
    test's comment for why these numbers moved. Re-pinned again the same day
    after the blank-particulars-header fix: of the 4 new round-number flags,
    3 are HIGH (Payment_292 Rs.5,00,000; Payment_294 Rs.7,50,000; Payment_429
    Rs.9,00,000) and 1 is CRITICAL (Payment_299 Rs.12,50,000), per Rule 1's
    severity thresholds. LOW and MEDIUM are unaffected.

    Re-pinned again 2026-10-01 after the Sales/Purchase/Round-Off-Discount
    canonicalization fix (V3.7, previously LOW=242/MEDIUM=190/HIGH=627/
    CRITICAL=360). Of the 201 removed / 76 added flags: HIGH and CRITICAL
    are untouched (both sides' round-number/duplicate amounts in those
    tiers were already single-counted correctly); LOW drops 242->146 and
    MEDIUM drops 190->161 because the consolidated (no-longer-duplicated)
    legs in those tiers now produce one flag instead of two. See
    BASELINE_CHECKPOINT_V3.7.md for the full severity-tier breakdown.

    Re-pinned again 2026-10-02 after the CGST/SGST canonicalization fix
    (V3.8, previously LOW=146/MEDIUM=161/HIGH=627/CRITICAL=360). All 12
    removed flags are LOW severity (round-number/duplicate CGST-Account/
    SGST-Account amounts that are now single-counted); MEDIUM, HIGH and
    CRITICAL untouched. See BASELINE_CHECKPOINT_V3.8.md.

    Re-pinned again 2026-10-02 investigating the remaining 29 unbalanced
    vouchers post-V3.9 (previously LOW=134/MEDIUM=161/HIGH=627/
    CRITICAL=360). See test_leg_flag_counts_pinned for the full per-flag
    breakdown of the 11 removed flags: the 3 employee salary A/c
    duplicates are MEDIUM (161->158); the 8 UNRESOLVED redacted-account
    orphan duplicates are HIGH (627->619). LOW and CRITICAL untouched --
    neither pattern touches a LOW- or CRITICAL-severity leg."""
    # Re-pinned 2026-10-07 (Addendum 17): a round number ALONE is now LOW
    # (it only reaches HIGH/CRITICAL when another independent rule fires on
    # the same leg). Total flagged legs is unchanged at 1,271 -- detection
    # did not change, only ranking. Previously LOW=134/MEDIUM=158/HIGH=619/
    # CRITICAL=360: 1,056 round-only legs moved to LOW (117 MEDIUM, 597
    # HIGH, 342 CRITICAL of them); what remains above LOW is corroborated.
    # Re-pinned 2026-10-07 (Addendum 21): the 129 sub-rupee rounding legs that
    # left the duplicate list were 100 LOW and 29 MEDIUM; HIGH and CRITICAL
    # are untouched (LOW 1,190 -> 1,090, MEDIUM 41 -> 12).
    assert real_report["summary"]["by_severity"] == {
        "LOW": 1090, "MEDIUM": 12, "HIGH": 22, "CRITICAL": 18,
    }


def test_severity_counts_sum_to_total_flagged(real_report):
    """Structural invariant, not a pinned number: every flagged leg has
    exactly one severity, so the per-severity counts must always sum to
    the total regardless of what the individual numbers are."""
    summary = real_report["summary"]
    assert sum(summary["by_severity"].values()) == summary["total_legs_flagged"]


def test_multi_signal_entries_all_have_signal_count_above_one(real_report):
    multi_signal_legs = [leg for leg in real_report["ledger_leg_flags"] if leg["signal_count"] > 1]
    assert len(multi_signal_legs) == real_report["summary"]["multi_signal"]
    assert all(leg["signal_count"] >= 2 for leg in multi_signal_legs)
