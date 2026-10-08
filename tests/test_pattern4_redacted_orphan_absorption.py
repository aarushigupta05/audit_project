"""
Regression tests for Pattern 4 (redacted-account orphan absorption),
investigating the remaining 29 unbalanced vouchers post-V3.9.

Root cause: the 2 redacted accounts (see _resolve_headerless_pages() in
reconstruct_ledger_entries.py) get a synthetic header leg on their OWN
page(s) (477, 546) using the invented placeholder label as particulars.
But every time one of their vouchers is ALSO reprinted on some OTHER
account's own page (where this account is the counterparty), the
counterparty's name was blanked out in the source document itself, so it
parses as an orphan leg (particulars=None) rather than the placeholder text
-- the placeholder is a label this project invented for the account's own
header; it is never literally printed as body-line particulars anywhere in
the source.

merge_occurrences() now absorbs such an orphan leg into the SAME voucher's
already-existing named "[Redacted account -- TB opening ...]" leg when,
and ONLY when, they share the exact amount and exact dr_cr AND there is
EXACTLY ONE such eligible redacted-placeholder leg in that voucher --
folding the orphan's source page into that leg's corroboration and removing
the orphan (it is the same physical leg, not a new one, so it must not be
double-counted against the voucher's Dr/Cr balance).

This was gated on an exhaustive, read-only, whole-document bounded
simulation BEFORE any code was written: every one of the 22 orphan legs in
the full 926-page population resolved to exactly 1 eligible candidate, 0
ambiguous, 0 unmatched, exactly the 6 vouchers below -- no unexpected
candidates. See the checkpoint doc for the full simulation report.
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

REDACTED_477 = "[Redacted account — TB opening Cr 2,05,51,866.76]"
REDACTED_546 = "[Redacted account — TB opening Cr 85,21,875.81]"

# The 6 vouchers the bounded simulation proved are affected, with the
# expected (amount, dr_cr, placeholder) of each absorbed orphan leg.
AFFECTED_VOUCHERS = {
    "Journal_604": [(1556.0, "Cr", REDACTED_546)],
    "Journal_671": [(121794.0, "Dr", REDACTED_546), (284186.0, "Dr", REDACTED_477)],
    "Journal_672": [(11179.0, "Dr", REDACTED_546), (26086.0, "Dr", REDACTED_477)],
    "Journal_684": [(360000.0, "Cr", REDACTED_546), (840000.0, "Cr", REDACTED_477)],
    "Journal_687": [(210000.0, "Dr", REDACTED_546), (490000.0, "Dr", REDACTED_477)],
    "Journal_690": [(463626.0, "Cr", REDACTED_546), (1090441.0, "Cr", REDACTED_477)],
}


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. Integration: all 6 affected vouchers, against the real data ----------

def test_all_6_affected_vouchers_are_now_balanced(real_gl_rows):
    """The whole point of Pattern 4: these 6 vouchers were unbalanced in
    the V3.9 baseline (their orphan legs were double-counting an already-
    represented redacted-placeholder leg) and must be balanced now."""
    by_eid = {}
    for r in real_gl_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    for eid in AFFECTED_VOUCHERS:
        legs = by_eid.get(eid)
        assert legs, f"{eid} is missing from the reconstructed ledger entirely"
        assert all(l["entry_balanced"] == "True" for l in legs), (
            f"{eid} is still unbalanced after Pattern 4"
        )


def test_all_6_affected_vouchers_have_no_remaining_orphans(real_gl_rows):
    """Every orphan leg in these 6 vouchers was absorbed -- none should
    remain as an "UNRESOLVED" row."""
    offenders = [
        r for r in real_gl_rows
        if r["entry_id"] in AFFECTED_VOUCHERS and r["account"] == "UNRESOLVED"
    ]
    assert not offenders, (
        f"Expected zero remaining orphan legs in the 6 Pattern-4 vouchers; "
        f"found {len(offenders)}: {[(r['entry_id'], r['amount']) for r in offenders]}"
    )


def test_absorbed_legs_carry_the_expected_amount_and_dr_cr(real_gl_rows):
    """Each voucher's redacted-placeholder leg(s) must still carry exactly
    the amount/dr_cr the bounded simulation found -- absorption must never
    change the amount or direction of the leg it folds into."""
    by_eid = {}
    for r in real_gl_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    for eid, expected_legs in AFFECTED_VOUCHERS.items():
        rows = by_eid[eid]
        for amount, dr_cr, placeholder in expected_legs:
            matches = [
                r for r in rows
                if r["account"] == placeholder and r["dr_cr"] == dr_cr
                and float(r["amount"]) == pytest.approx(amount, abs=0.01)
            ]
            assert len(matches) == 1, (
                f"{eid}: expected exactly 1 leg {placeholder!r} {dr_cr} Rs.{amount}, "
                f"found {len(matches)}"
            )


def test_no_other_vouchers_gained_or_lost_a_redacted_placeholder_leg(real_gl_rows):
    """Structural guard: Pattern 4 must only ever fold an orphan INTO an
    EXISTING named leg of its own voucher -- it must never create a new
    redacted-placeholder leg, and the set of vouchers carrying one must be
    exactly the 14 (REDACTED_477) / 11 (REDACTED_546) from the header-
    carryover-fix tests, unchanged by this pattern."""
    eids_477 = {r["entry_id"] for r in real_gl_rows if r["account"] == REDACTED_477}
    eids_546 = {r["entry_id"] for r in real_gl_rows if r["account"] == REDACTED_546}
    assert len(eids_477) == 14
    assert len(eids_546) == 11
    # Every Pattern-4-affected voucher must already be in one of these sets
    # (absorption never invents a new placeholder leg for a voucher that
    # didn't already have one).
    for eid in AFFECTED_VOUCHERS:
        assert eid in eids_477 or eid in eids_546, (
            f"{eid} has no redacted-placeholder leg to absorb into"
        )


# ---------- 2. Unit tests: merge_occurrences() absorption logic in isolation ----------

def _make_occ(entry_id, page, account, legs, date="1-Apr-24", vch_type="Journal", vch_no="1"):
    return {
        "entry_id": entry_id, "date": date, "vch_type": vch_type, "vch_no": vch_no,
        "page": page, "account": account, "narration": [], "legs": legs,
    }


def test_orphan_absorbed_on_exact_amount_and_dr_cr_match():
    """The core case: one orphan leg, one eligible redacted-placeholder leg
    in the same voucher, same amount, same dr_cr -- must be absorbed (the
    orphan disappears from orphan_legs, and its page is folded into the
    matched leg's source_pages)."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_1", 477, "[Redacted account — TB opening Cr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_1", 3, "ADVANCE TAX", [
            {"particulars": None, "amount": 5000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_1"]
    assert e["orphan_legs"] == [], "Orphan leg should have been absorbed, none should remain"
    key = ("[Redacted account — TB opening Cr 1,00,000.00]", "Dr")
    assert key in e["named_legs"]
    assert set(e["named_legs"][key]["source_pages"]) == {477, 3}


def test_orphan_not_absorbed_when_dr_cr_differs():
    """Same amount, same voucher, but opposite dr_cr -- must NOT be
    absorbed (not the same leg)."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_2", 477, "[Redacted account — TB opening Cr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_2", 3, "ADVANCE TAX", [
            {"particulars": None, "amount": 5000.0, "dr_cr": "Cr", "resolved": False, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_2"]
    assert len(e["orphan_legs"]) == 1, "Orphan leg must survive -- dr_cr doesn't match"
    assert e["orphan_legs"][0]["amount"] == 5000.0
    assert e["orphan_legs"][0]["dr_cr"] == "Cr"


def test_orphan_not_absorbed_when_no_eligible_redacted_leg_exists():
    """No redacted-placeholder leg at all in this voucher -- the orphan
    must be left exactly as parsed (still reported as UNRESOLVED), not
    guessed into some unrelated named leg."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_3", 3, "ADVANCE TAX", [
            {"particulars": "ADVANCE TAX", "amount": 7000.0, "dr_cr": "Cr", "resolved": True, "is_header_leg": True},
            {"particulars": None, "amount": 5000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_3"]
    assert len(e["orphan_legs"]) == 1, "Orphan leg must survive -- no redacted leg to absorb into"
    assert e["orphan_legs"][0]["amount"] == 5000.0


def test_orphan_not_absorbed_when_amount_differs():
    """A redacted-placeholder leg exists in this voucher, same dr_cr, but a
    DIFFERENT amount -- must NOT be absorbed."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_4", 477, "[Redacted account — TB opening Cr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_4", 3, "ADVANCE TAX", [
            {"particulars": None, "amount": 4999.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_4"]
    assert len(e["orphan_legs"]) == 1, "Orphan leg must survive -- amount doesn't match exactly"
    assert e["orphan_legs"][0]["amount"] == 4999.0


def test_orphan_not_absorbed_when_candidates_are_ambiguous():
    """TWO eligible redacted-placeholder legs in the same voucher share the
    same amount and dr_cr as the orphan -- this is ambiguous (the rule
    requires EXACTLY ONE eligible candidate), so the orphan must be left
    unresolved rather than guessed into either one."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_5", 477, "[Redacted account — TB opening Cr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_5", 546, "[Redacted account — TB opening Cr 2,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 2,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_5", 3, "ADVANCE TAX", [
            {"particulars": None, "amount": 5000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_5"]
    assert len(e["orphan_legs"]) == 1, "Orphan leg must survive -- 2 eligible candidates is ambiguous"
    assert e["orphan_legs"][0]["amount"] == 5000.0
    # Neither candidate's source_pages should have been touched.
    key_a = ("[Redacted account — TB opening Cr 1,00,000.00]", "Dr")
    key_b = ("[Redacted account — TB opening Cr 2,00,000.00]", "Dr")
    assert e["named_legs"][key_a]["source_pages"] == [477]
    assert e["named_legs"][key_b]["source_pages"] == [546]


def test_orphan_absorption_scoped_to_its_own_voucher():
    """A redacted-placeholder leg of the exact matching amount/dr_cr exists,
    but in a DIFFERENT voucher -- must never be absorbed across vouchers."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_6a", 477, "[Redacted account — TB opening Cr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ], vch_no="6a"),
        _make_occ("FakeVoucher_P4_6b", 3, "ADVANCE TAX", [
            {"particulars": None, "amount": 5000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ], vch_no="6b"),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    assert entries["FakeVoucher_P4_6a"]["orphan_legs"] == []
    assert len(entries["FakeVoucher_P4_6b"]["orphan_legs"]) == 1


def test_multiple_orphans_can_absorb_into_the_same_candidate_leg():
    """Two separate orphan occurrences (different pages) both matching the
    SAME single eligible candidate -- both must absorb into it (this is
    exactly the real-data shape: e.g. Journal_687's redacted-477 leg
    absorbs orphan reprints from both page 3 and page 477 itself)."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_7", 477, "[Redacted account — TB opening Cr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Cr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_7", 3, "ADVANCE TAX", [
            {"particulars": None, "amount": 5000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
        _make_occ("FakeVoucher_P4_7", 901, "Some Other Account", [
            {"particulars": None, "amount": 5000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_7"]
    assert e["orphan_legs"] == []
    key = ("[Redacted account — TB opening Cr 1,00,000.00]", "Dr")
    assert set(e["named_legs"][key]["source_pages"]) == {477, 3, 901}


def test_voucher_balance_after_absorption_matches_real_data_shape():
    """End-to-end sanity check mirroring the real Journal_687 shape: a
    3-legged voucher (1 Cr named leg + 2 Dr redacted-placeholder legs, each
    with a same-amount orphan reprint on another page) must be exactly
    balanced after absorption -- this is the entire point of Pattern 4."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_P4_8", 3, "ADVANCE TAX", [
            {"particulars": "ADVANCE TAX", "amount": 700000.0, "dr_cr": "Cr", "resolved": True, "is_header_leg": True},
            {"particulars": None, "amount": 490000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
            {"particulars": None, "amount": 210000.0, "dr_cr": "Dr", "resolved": False, "is_header_leg": False},
        ]),
        _make_occ("FakeVoucher_P4_8", 477, "[Redacted account — TB opening Cr 2,05,51,866.76]", [
            {"particulars": "[Redacted account — TB opening Cr 2,05,51,866.76]", "amount": 490000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_P4_8", 546, "[Redacted account — TB opening Cr 85,21,875.81]", [
            {"particulars": "[Redacted account — TB opening Cr 85,21,875.81]", "amount": 210000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_P4_8"]
    assert e["orphan_legs"] == []
    dr_total = sum(l["amount"] for k, l in e["named_legs"].items() if k[1] == "Dr")
    cr_total = sum(l["amount"] for k, l in e["named_legs"].items() if k[1] == "Cr")
    assert dr_total == pytest.approx(cr_total, abs=0.01)
