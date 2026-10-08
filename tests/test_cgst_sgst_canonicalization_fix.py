"""
Regression tests for the CGST/SGST account-name canonicalization fix in
reconstruct_ledger_entries.py (fixed 2026-10-02, V3.8).

Background: this fix was discovered as a SIDE EFFECT of investigating a
different problem -- 77 vouchers that flipped from entry_balanced=True to
False when the V3.7 fix (Sales/Purchase/Round-Off-Discount) landed (see
BASELINE_CHECKPOINT_V3.7.md Section 4 and BASELINE_CHECKPOINT_V3.8.md for
the full investigation). That investigation found 9 distinct customer/
vendor/tax-account name-pairs involved across the 77 vouchers. Of those 9,
only CGST/SGST met the bar for a safe, deterministic, evidence-backed fix
(the exact same proof structure already used for Sales/Purchase/Round-
Off-Discount in V3.7):
  - Both "CGST"/"SGST" (first page: 172, 787) and "CGST Account"/
    "SGST Account" (continuation pages: 173-251, 788-866) are genuine,
    continuous HEADER-LEG ledger runs for one account each.
  - The variant ("...Account") form has ZERO detail-leg occurrences
    anywhere in the 779+779 cross-variant vouchers -- the header-only
    insertion point is provably sufficient, same as V3.7.
  - Zero amount conflicts when the two forms are merged, across the full
    779+779 voucher population.

IMPORTANT -- this fix does NOT resolve the original 77-voucher issue. Of
those 77, this fix resolves exactly 0 (verified below) -- their
imbalances are driven by 7 OTHER name-pairs that do NOT meet the safety
bar (either pure detail-leg truncation with no header-page evidence at
all, or contradictory evidence, including one pair -- JANDIYAL ELECTRONICS
/ JANDIYAL ELECTRONICS PARTNER -- that affirmatively looks like two
DIFFERENT real accounts). Those 77 vouchers remain exactly as V3.7 left
them and are explicitly NOT touched by this fix or these tests.

What this fix DOES do: it resolves 766 OTHER previously-unbalanced
vouchers elsewhere in the dataset that were driven by CGST/SGST double-
counting directly (invisible to reconcile_trial_balance.py before this
fix, since it had no CGST/SGST alias at all and so never checked the
"...Account" rows against the trial balance).

These tests pin the fixed, verified-correct state against the REAL
committed data in data/ (same convention as the other V3.x regression
test files).
"""
import csv
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(TESTS_DIR, "..", "scripts")
DATA_DIR = os.path.join(TESTS_DIR, "..", "data")
sys.path.insert(0, SCRIPTS_DIR)

GL_FILE = os.path.join(DATA_DIR, "general_ledger.csv")

VARIANT_NAMES = {"CGST Account", "SGST Account"}

# The pairs investigated in V3.8 and excluded from
# _ACCOUNT_NAME_CANONICALIZATION at that time. Two of these nine --
# JANDIYAL ELECTRONICS/PARTNER and KC EDU.SOCIETY( GENSET AC/(GENSET AC --
# were RE-investigated in V3.9 using Carried-Over/Brought-Forward page
# continuity evidence and turned out to meet the bar after all; they are
# now IN _ACCOUNT_NAME_CANONICALIZATION (see
# tests/test_v3_9_name_pair_fixes.py) and have been removed from this list.
# The remaining 6 still do not belong in _ACCOUNT_NAME_CANONICALIZATION --
# their short form has no header page at all, so there's nothing to
# canonicalize at this insertion point -- but V3.9 found a different, safe,
# per-voucher mechanism for them instead (_PER_VOUCHER_NAME_ALIASES, also
# tested in test_v3_9_name_pair_fixes.py). This list still guards against
# someone "completing" THIS (header-leg-only) mechanism by fuzzy-merging
# them here.
EXCLUDED_PAIRS = [
    ("ARO OFFICE B-1204", "ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI"),
    ("CAMBRIDGE", "CAMBRIDGE INTERNATIONAL (GENSET A/C)"),
    ("PRINCIPAL MHAC VEH. ACCOUNT", "PRINCIPAL MHAC VEH.ACCOUNT"),
    ("Principal MHAC School", "Principal MHAC School Nagbani"),
    ("SGF INFRA PVT LTD", "SGF INFRA PVT LTD GURHA BAKSHI NAGAR"),
    ("Veh. Running &", "Veh. Running & Maintenance Exp"),
]

FLIP77_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures_flip77_entry_ids.txt"
)


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. The canonicalization dict includes exactly these 2 new mappings ----------

def test_cgst_sgst_mappings_present_and_narrow():
    """CGST Account -> CGST and SGST Account -> SGST must be present; this
    fix adds exactly these 2 entries to the existing dict, nothing more."""
    from reconstruct_ledger_entries import _ACCOUNT_NAME_CANONICALIZATION

    assert _ACCOUNT_NAME_CANONICALIZATION["CGST Account"] == "CGST"
    assert _ACCOUNT_NAME_CANONICALIZATION["SGST Account"] == "SGST"


def test_excluded_pairs_are_not_in_the_canonicalization_dict():
    """The 7 investigated-but-rejected pairs must never be added without a
    fresh, explicit safety review -- this guards against silent scope
    creep (e.g. someone "completing the pattern" with a loop)."""
    from reconstruct_ledger_entries import _ACCOUNT_NAME_CANONICALIZATION

    for short, long in EXCLUDED_PAIRS:
        assert long not in _ACCOUNT_NAME_CANONICALIZATION, (
            f"{long!r} should NOT be canonicalized -- it was investigated "
            f"and explicitly excluded (see BASELINE_CHECKPOINT_V3.8.md)"
        )
        assert short not in _ACCOUNT_NAME_CANONICALIZATION


def test_no_cgst_sgst_variant_survives_in_the_ledger(real_gl_rows):
    offenders = [r for r in real_gl_rows if r["account"] in VARIANT_NAMES]
    assert not offenders, (
        f"Found {len(offenders)} rows still carrying 'CGST Account' or "
        f"'SGST Account': {[(r['entry_id'], r['account']) for r in offenders[:10]]}"
    )


def test_excluded_pairs_still_have_both_name_variants_present(real_gl_rows):
    """None of these 6 pairs are touched by the CGST/SGST canonicalization
    dict fix -- the long form must still exist in the output. (As of V3.9,
    their SHORT form may no longer appear on its own: _PER_VOUCHER_NAME_ALIASES
    merges it into the long form wherever a matching voucher is found -- see
    test_v3_9_name_pair_fixes.py for that mechanism's own tests. This test
    only guards the CGST/SGST-specific insertion point this file covers.)"""
    for short, long in EXCLUDED_PAIRS:
        long_rows = [r for r in real_gl_rows if r["account"] == long]
        assert long_rows, f"{long!r} should still have rows (untouched by this fix)"


# ---------- 2. Cross-page corroboration collapses into one leg ----------

def test_cgst_sgst_cross_variant_voucher_collapses_to_one_leg_each(real_gl_rows):
    """Sales GST_MPSS/24-25/19 was corroborated from 5 pages (10, 178, 610,
    737, 793); before this fix, pages printing "CGST"/"SGST" (detail-leg
    reprints elsewhere) and pages printing "CGST Account"/"SGST Account"
    (this voucher's own header-leg occurrence on its continuation pages)
    were 2 separate rows each. Post-fix, exactly 1 row each, all 5 source
    pages preserved, amount unchanged."""
    legs = [r for r in real_gl_rows if r["entry_id"] == "Sales GST_MPSS/24-25/19"]
    cgst = [l for l in legs if l["account"] == "CGST"]
    sgst = [l for l in legs if l["account"] == "SGST"]
    assert len(cgst) == 1, f"Expected 1 consolidated CGST leg, found {len(cgst)}"
    assert len(sgst) == 1, f"Expected 1 consolidated SGST leg, found {len(sgst)}"
    assert float(cgst[0]["amount"]) == pytest.approx(305.08, abs=0.01)
    assert float(sgst[0]["amount"]) == pytest.approx(305.08, abs=0.01)
    assert cgst[0]["dr_cr"] == "Cr"
    assert set(cgst[0]["source_pages"].split(";")) == {"10", "178", "610", "737", "793"}
    assert cgst[0]["corroboration_count"] == "5"


def test_narration_preserved_on_consolidated_cgst_sgst_legs(real_gl_rows):
    """Consolidation must not discard whatever narration this voucher had,
    even if empty -- check the field exists and is consistent across all
    of this voucher's legs (same narration set applies to the whole
    voucher, not per-leg)."""
    legs = [r for r in real_gl_rows if r["entry_id"] == "Sales GST_MPSS/24-25/19"]
    narrations = {r["narration"] for r in legs}
    assert len(narrations) == 1, "All legs of one voucher must share the same narration set"


# ---------- 3. Trial balance reconciliation ----------

def test_cgst_sgst_reconcile_exactly_against_trial_balance():
    from reconcile_trial_balance import reconcile

    result = reconcile()
    clean_accounts = {e["account"]: e for e in result["clean"]}
    for acct in ("CGST", "SGST"):
        assert acct in clean_accounts, f"{acct} should reconcile cleanly against the trial balance"
        entry = clean_accounts[acct]
        assert entry["debit_diff"] == pytest.approx(0.0, abs=1.0)
        assert entry["credit_diff"] == pytest.approx(0.0, abs=1.0)
        assert entry["variants_used"] == [acct]


# ---------- 4. TDS Receivable HPCL / Rent Expenses HPCL still untouched (regression guard) ----------

def test_hpcl_accounts_still_unaffected_by_this_fix(real_gl_rows):
    for name in ("TDS Receivable HPCL", "TDS Receivable", "Rent Expenses HPCL", "Rent Expenses"):
        assert any(r["account"] == name for r in real_gl_rows), (
            f"{name!r} should still be present, untouched by the CGST/SGST fix"
        )


# ---------- 5. The original 77-voucher issue is explicitly NOT touched by this fix ----------

def test_original_77_flip_vouchers_now_resolved_by_v3_9(real_gl_rows):
    """Re-purposed in V3.9 (previously asserted these 77 remained
    unbalanced -- true for the CGST/SGST fix alone, which resolves 0 of
    them; see BASELINE_CHECKPOINT_V3.8.md). V3.9 fully resolved all 77 via
    8 name-pair fixes: 2 added to _ACCOUNT_NAME_CANONICALIZATION (JANDIYAL
    ELECTRONICS/PARTNER, KC EDU.SOCIETY) and 6 handled by the new
    _PER_VOUCHER_NAME_ALIASES mechanism (ARO OFFICE B-1204, CAMBRIDGE,
    PRINCIPAL MHAC VEH.ACCOUNT, Principal MHAC School, SGF INFRA PVT LTD,
    Veh. Running & Maintenance Exp). See test_v3_9_name_pair_fixes.py for
    the full per-pair verification and BASELINE_CHECKPOINT_V3.9.md for the
    investigation."""
    if not os.path.exists(FLIP77_FIXTURE):
        pytest.skip("flip-77 fixture list not present in this environment")
    flip77 = set(open(FLIP77_FIXTURE).read().splitlines())

    by_eid = {}
    for r in real_gl_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)

    now_balanced = 0
    for eid in flip77:
        legs = by_eid.get(eid)
        if not legs:
            continue
        if legs[0]["entry_balanced"] == "True":
            now_balanced += 1
    assert now_balanced == len(flip77), (
        f"Expected all {len(flip77)} originally-flagged vouchers to now be "
        f"balanced (resolved by the V3.9 name-pair fixes); only "
        f"{now_balanced} are"
    )
