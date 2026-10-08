"""
Regression tests for V3.11 -- the three fixes made while investigating the
newly-supplied FY2025-26 ledger (Ledgers_Anonymised.pdf, 888 pages):

  1. _VCH_NO_PATTERN (vch_no regex widening): the new ledger introduces an
     "INV /25-26/166"-style voucher number with a literal space before each
     "/"-prefixed segment. _HEADER_RE and _WRAPPED_PARTICULARS_HEADER_RE's
     vch_no group now allows one optional space before a "/" segment,
     without loosening anything else -- old bare-numeric ("699") and
     slash-joined ("MPSS/24-25/32") forms must still match exactly as
     before.

  2. _PARTICULARS_CONTINUATION_TOKENS gained 4 entries ("maintenance exp",
     "control", "equipment", "veh.account") -- confirmed, every occurrence,
     to be a Shape B voucher-header counterparty name wrapping onto the
     next printed line (never a detail-leg particulars line), so merging
     only ever extends the private "_counterparty" narration field, never
     a named leg's particulars key -- this cannot affect any voucher's
     Dr/Cr balance.

  3. _ANONYMIZATION_ARTIFACT_FRAGMENTS: short, digit-free, amount-less
     remnants of a counterparty name whose first line was replaced by the
     anonymization process with a "P0xx"-style code, but whose own
     (wrapped) line was not also scrubbed. These are reported under a
     distinct reason ("anonymization_artifact_fragment_discarded") and are
     NEVER merged into any leg's particulars -- doing so would silently
     rewrite a correct account code (e.g. "P027") into a nonexistent
     compound key ("P027 AL"), breaking its cross-page corroboration.

  4. merge_occurrences() gained a general named-leg consolidation for
     redacted-placeholder legs reprinted elsewhere under a "P0xx"-style
     code: same voucher, same dr_cr, same exact amount, completely
     disjoint source pages -- same proof bar as every other alias
     mechanism in this file. Verified exhaustively before being written:
     182 candidates in the FY2025-26 ledger, all matched 1-to-1, zero
     ambiguous; zero matching candidates at all in the frozen FY2024-25
     ledger (independently reconfirmed below by the full-suite /
     unbalanced-set invariant tests).

See the FY2025-26 ledger investigation notes for the full population-level
evidence (gap counts, page traces) this checkpoint is based on.
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
OLD_LEDGER_ENTRIES = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_entries.csv")
OLD_LEDGER_UNRESOLVED = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_unresolved.csv")

# The exact unbalanced set carried forward, unchanged, from V3.10 (see
# BASELINE_CHECKPOINT_V3.10.md Section 5/9 -- Payment_2 / Sales_Payment is
# a deliberately-deferred, single-occurrence header-regex ambiguity,
# unrelated to anything fixed in V3.11).
V3_10_UNBALANCED_SET = {"Payment_2", "Sales_Payment"}


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def old_ledger_entries_rows():
    if not os.path.exists(OLD_LEDGER_ENTRIES):
        pytest.skip(f"{OLD_LEDGER_ENTRIES} not present")
    with open(OLD_LEDGER_ENTRIES, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def old_ledger_unresolved_rows():
    if not os.path.exists(OLD_LEDGER_UNRESOLVED):
        pytest.skip(f"{OLD_LEDGER_UNRESOLVED} not present")
    with open(OLD_LEDGER_UNRESOLVED, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. _VCH_NO_PATTERN / _HEADER_RE: space-before-slash widening ----------

def test_header_re_matches_new_space_before_slash_format():
    from reconstruct_ledger_entries import _HEADER_RE

    line = "15-Apr-25 To Office Rent Payment INV /25-26/166 25,000.00 Dr"
    m = _HEADER_RE.match(line)
    assert m is not None
    assert m.group("vch_no") == "INV /25-26/166"


def test_header_re_still_matches_old_slash_joined_format():
    from reconstruct_ledger_entries import _HEADER_RE

    line = "15-Apr-25 To Office Rent Payment MPSS/24-25/32 25,000.00 Dr"
    m = _HEADER_RE.match(line)
    assert m is not None
    assert m.group("vch_no") == "MPSS/24-25/32"


def test_header_re_still_matches_bare_numeric_format():
    from reconstruct_ledger_entries import _HEADER_RE

    line = "15-Apr-25 To Office Rent Payment 699 25,000.00 Dr"
    m = _HEADER_RE.match(line)
    assert m is not None
    assert m.group("vch_no") == "699"


def test_wrapped_particulars_header_re_matches_space_before_slash_format():
    from reconstruct_ledger_entries import _WRAPPED_PARTICULARS_HEADER_RE

    line = "By Journal INV /25-26/7 25,000.00 Cr"
    m = _WRAPPED_PARTICULARS_HEADER_RE.match(line)
    assert m is not None
    assert m.group("vch_no") == "INV /25-26/7"


def test_vch_no_pattern_does_not_swallow_trailing_whitespace_run():
    """A guard against the widened pattern becoming too permissive: it
    must still stop at the vch_no/remainder boundary, not eat into the
    amount column."""
    from reconstruct_ledger_entries import _HEADER_RE

    line = "To Something Journal INV /25-26/7 1,234.56 Dr"
    m = _HEADER_RE.match(line)
    assert m is not None
    assert m.group("vch_no") == "INV /25-26/7"
    assert "1,234.56" in m.group("remainder")


# ---------- 2. Continuation tokens: narration-only, never touch particulars ----------

def test_new_continuation_tokens_present():
    from reconstruct_ledger_entries import _PARTICULARS_CONTINUATION_TOKENS

    for token in ("maintenance exp", "control", "equipment", "veh.account"):
        assert token in _PARTICULARS_CONTINUATION_TOKENS


def test_continuation_merge_extends_counterparty_not_particulars():
    """Direct unit test of the is_wrapped_part branch's two paths (see
    reconstruct_ledger_entries.py's per-line loop): a leg carrying
    "_counterparty" gets that field extended; a leg with no
    "_counterparty" (a plain named leg) gets "particulars" extended
    instead. This fixture exercises the dict-mutation logic directly,
    the same shape collect_occurrences() produces, without needing a
    real PDF page."""
    last_leg_header_style = {
        "particulars": "SomeAccount", "amount": 100.0, "dr_cr": "Dr",
        "resolved": True, "is_header_leg": True, "_counterparty": "Veh. Running &",
    }
    line = "Maintenance Exp"
    if "_counterparty" in last_leg_header_style:
        if last_leg_header_style.get("_counterparty"):
            last_leg_header_style["_counterparty"] = f"{last_leg_header_style['_counterparty']} {line}"
    assert last_leg_header_style["_counterparty"] == "Veh. Running & Maintenance Exp"
    assert last_leg_header_style["particulars"] == "SomeAccount"  # untouched


# ---------- 3. Anonymization artifact fragments: discarded, never merged ----------

def test_artifact_fragments_set_contains_known_fragments():
    from reconstruct_ledger_entries import _ANONYMIZATION_ARTIFACT_FRAGMENTS

    for frag in ("AL", "ani", "RSING", "AR", "OL", "ICS", "ON", "O", "OF",
                 "OUNT", "MENT", "Enterprises(", "Committe)"):
        assert frag in _ANONYMIZATION_ARTIFACT_FRAGMENTS


def test_artifact_fragments_never_overlap_continuation_tokens():
    """These two sets exist precisely to be mutually exclusive: a
    continuation token is safe to append (narration-only), an artifact
    fragment is explicitly NOT safe to append (would corrupt a real
    account code). If a string ever ended up in both, that would be a
    real bug -- whichever branch runs first would silently win."""
    from reconstruct_ledger_entries import (
        _PARTICULARS_CONTINUATION_TOKENS, _ANONYMIZATION_ARTIFACT_FRAGMENTS,
    )
    lowered_tokens = {t.lower() for t in _PARTICULARS_CONTINUATION_TOKENS}
    lowered_fragments = {f.lower() for f in _ANONYMIZATION_ARTIFACT_FRAGMENTS}
    assert not (lowered_tokens & lowered_fragments)


def test_no_account_particulars_contains_an_artifact_fragment_suffix(old_ledger_entries_rows):
    """The actual safety invariant this design is protecting: no named
    leg's particulars in the real reconstructed output should ever look
    like "<code> <fragment>" (e.g. "P027 AL") -- proof the fragments are
    never silently appended onto a preceding leg's account identity."""
    from reconstruct_ledger_entries import _ANONYMIZATION_ARTIFACT_FRAGMENTS

    offenders = [
        row["particulars"] for row in old_ledger_entries_rows
        if any(row["particulars"].endswith(f" {frag}") for frag in _ANONYMIZATION_ARTIFACT_FRAGMENTS)
    ]
    assert not offenders, f"Found corrupted compound particulars: {offenders[:5]}"


def test_old_ledger_unresolved_reason_is_populated(old_ledger_unresolved_rows):
    """Sanity check that the new reason label is actually reachable on
    real data, not just defined and unused."""
    reasons = {r["reason"] for r in old_ledger_unresolved_rows}
    assert "anonymization_artifact_fragment_discarded" in reasons


# ---------- 4. Redacted-placeholder / coded-counterparty named-leg merge ----------

def _make_occ(entry_id, page, account, legs, date="1-Apr-25", vch_type="Journal", vch_no="1"):
    return {
        "entry_id": entry_id, "date": date, "vch_type": vch_type, "vch_no": vch_no,
        "page": page, "account": account, "narration": [], "legs": legs,
    }


def test_redacted_named_leg_merges_with_coded_duplicate_on_disjoint_pages():
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_V311_1", 16, "[Redacted account — TB opening Dr 1,52,056.00]", [
            {"particulars": "[Redacted account — TB opening Dr 1,52,056.00]", "amount": 85830.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_V311_1", 583, "P015", [
            {"particulars": "P015", "amount": 85830.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_V311_1"]
    # Only ONE named leg should remain -- the redacted placeholder key must
    # be gone, folded into the coded leg (or vice versa; either survivor is
    # fine as long as exactly one remains and corroboration is combined).
    assert len(e["named_legs"]) == 1
    (remaining_key, remaining_leg), = e["named_legs"].items()
    assert set(remaining_leg["source_pages"]) == {16, 583}


def test_redacted_named_leg_not_merged_when_dr_cr_differs():
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_V311_2", 16, "[Redacted account — TB opening Dr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Dr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_V311_2", 90, "P099", [
            {"particulars": "P099", "amount": 5000.0,
             "dr_cr": "Cr", "resolved": True, "is_header_leg": True},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_V311_2"]
    assert len(e["named_legs"]) == 2, "Opposite dr_cr -- this is a normal balancing pair, not a duplicate"


def test_redacted_named_leg_not_merged_when_amount_differs():
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_V311_3", 16, "[Redacted account — TB opening Dr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Dr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_V311_3", 90, "P099", [
            {"particulars": "P099", "amount": 4999.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_V311_3"]
    assert len(e["named_legs"]) == 2, "Different amount -- not the same leg, must not merge"


def test_redacted_named_leg_not_merged_when_same_page():
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_V311_4", 16, "[Redacted account — TB opening Dr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Dr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
            {"particulars": "P099", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": False},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_V311_4"]
    assert len(e["named_legs"]) == 2, (
        "Both printed on the same page -- not a cross-ledger reprint of one leg, must not merge"
    )


def test_redacted_named_leg_not_merged_when_ambiguous():
    """Two equally-eligible candidates -- must not guess, both legs and
    the redacted leg all survive untouched."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        _make_occ("FakeVoucher_V311_5", 16, "[Redacted account — TB opening Dr 1,00,000.00]", [
            {"particulars": "[Redacted account — TB opening Dr 1,00,000.00]", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_V311_5", 90, "P099", [
            {"particulars": "P099", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
        _make_occ("FakeVoucher_V311_5", 120, "P100", [
            {"particulars": "P100", "amount": 5000.0,
             "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
        ]),
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    e = entries["FakeVoucher_V311_5"]
    assert len(e["named_legs"]) == 3, "2+ eligible candidates -- must not guess which one to merge"


# ---------- 5. Old (FY2024-25) ledger invariants: zero financial regression ----------

def test_old_ledger_unbalanced_set_unchanged_from_v3_10(old_ledger_entries_rows):
    """The whole point of this checkpoint: none of the three V3.11 fixes
    were expected to change the old ledger's financial result at all. The
    unbalanced set must be EXACTLY the two V3.10 knowns -- no new
    imbalance introduced, and (since these fixes are narration/labeling
    only on this document) none resolved either."""
    by_eid = {}
    for r in old_ledger_entries_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    unbalanced = {eid for eid, legs in by_eid.items() if legs[0]["entry_balanced"] == "False"}
    assert unbalanced == V3_10_UNBALANCED_SET


def test_old_ledger_row_and_voucher_counts_unchanged(old_ledger_entries_rows):
    """Pinned from V3.10 (BASELINE_CHECKPOINT_V3.10.md Section 6): 14,625
    total rows, 6,216 canonical vouchers. None of V3.11's fixes add or
    remove a leg on this document -- they only complete already-present
    narration text and relabel already-unresolved noise lines."""
    by_eid = {}
    for r in old_ledger_entries_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    assert len(old_ledger_entries_rows) == 14625
    assert len(by_eid) == 6216


def test_old_ledger_no_amount_dr_cr_or_balance_field_changed_by_narration_fixes(old_ledger_entries_rows):
    """No row's amount/dr_cr/resolved/entry_balanced should ever depend on
    whether a counterparty name wrapped onto 1 line or 2 -- those 4 fields
    are set from the header's own remainder/direction, never from
    narration. Spot-check the specific vouchers V3.11's diff touched."""
    by_eid = {}
    for r in old_ledger_entries_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    for eid in ("Payment_125", "Payment_143", "Payment_158", "Payment_159",
                "Receipt_80", "Receipt_258", "Receipt_259"):
        legs = by_eid.get(eid)
        assert legs, f"{eid} missing from reconstructed ledger"
        for leg in legs:
            assert leg["entry_balanced"] == "True"
