"""
Regression tests for the V3.9 fixes:

1. FEES/COMPANY narration-split bug fix (priority-ordering bug in
   collect_occurrences()'s per-line classification loop).
2. Two more header-leg-only name-pairs added to
   _ACCOUNT_NAME_CANONICALIZATION (JANDIYAL ELECTRONICS/PARTNER,
   KC EDU.SOCIETY), re-investigated and found safe after all via
   Carried-Over/Brought-Forward page continuity evidence.
3. A new, narrower mechanism (_PER_VOUCHER_NAME_ALIASES) for 6 name-pairs
   that do NOT qualify for _ACCOUNT_NAME_CANONICALIZATION (their short form
   has no header page anywhere) -- merged per-voucher only, using
   entry_id + amount + dr_cr matching across DIFFERENT source pages as
   deterministic proof, never string resemblance.

Together, these fixes resolve all 77 of the vouchers originally flagged in
the V3.6->V3.7 77-voucher investigation (see
tests/fixtures_flip77_entry_ids.txt and
test_cgst_sgst_canonicalization_fix.py::test_original_77_flip_vouchers_now_resolved_by_v3_9).

See BASELINE_CHECKPOINT_V3.9.md for the full investigation, evidence, and
before/after validation.
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
RECON_FILE = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_entries.csv")

# The 2 pairs re-investigated and added to _ACCOUNT_NAME_CANONICALIZATION.
HEADER_CANONICALIZED_PAIRS = [
    ("JANDIYAL ELECTRONICS", "JANDIYAL ELECTRONICS PARTNER"),
    ("KC EDU.SOCIETY( GENSET AC", "KC EDU.SOCIETY(GENSET AC"),
]

# The 6 pairs handled by the new per-voucher mechanism.
PER_VOUCHER_PAIRS = [
    ("ARO OFFICE B-1204", "ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI"),
    ("CAMBRIDGE", "CAMBRIDGE INTERNATIONAL (GENSET A/C)"),
    ("PRINCIPAL MHAC VEH. ACCOUNT", "PRINCIPAL MHAC VEH.ACCOUNT"),
    ("Principal MHAC School", "Principal MHAC School Nagbani"),
    ("SGF INFRA PVT LTD", "SGF INFRA PVT LTD GURHA BAKSHI NAGAR"),
    ("Veh. Running &", "Veh. Running & Maintenance Exp"),
    # Added investigating the remaining 29 unbalanced vouchers (post-V3.9).
    # See the updated _PER_VOUCHER_NAME_ALIASES docstring in
    # reconstruct_ledger_entries.py and the next checkpoint doc.
    ("Tr. A/c", "Ledger Tr. A/c"),
]

FLIP77_FIXTURE = os.path.join(TESTS_DIR, "fixtures_flip77_entry_ids.txt")


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def real_recon_rows():
    if not os.path.exists(RECON_FILE):
        pytest.skip(f"reconstructed entries not present at {RECON_FILE}")
    with open(RECON_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. FEES/COMPANY narration-split bug ----------

# The 9 real occurrences found by an exhaustive, body-line-only (post
# body_start_idx) scan of all 926 pages: 6 "FEES" continuations + 3
# "COMPANY" continuations, each immediately following a narration line.
_FEES_CORRUPTED_STRINGS = {
    "Caliberation Charges FEES", "J & K Bank Ltd FEES", "Cash FEES", "Telephone Expenses FEES",
}
_FEES_FIXED_VOUCHERS = {
    "Payment_293": "WEIGHT AND MEASUREMENT FEES",
    "Payment_446": "WEIGHT AND MEASUREMENT FEES",
    "Payment_436": "BSNL WIFI INSTALLITION FEES",
    "Receipt_39": "AGGARWAL AND COMPANY",
    "Receipt_805": "AGGARWAL AND COMPANY",
    "Journal_568": "AGGARWAL AND COMPANY",
}


def test_fees_bug_no_longer_corrupts_any_column(real_recon_rows):
    """The old bug's particulars-corruption pattern must never reappear --
    confined-but-still-broken was the pre-fix state; now it must not occur
    in particulars at all."""
    corrupted = {r["particulars"] for r in real_recon_rows if r["particulars"] in _FEES_CORRUPTED_STRINGS}
    assert not corrupted, f"FEES bug corrupted particulars column: {corrupted}"


def test_fees_narration_now_reads_as_one_clean_phrase(real_recon_rows):
    """Each of the 6 vouchers driving the 9 real FEES/COMPANY occurrences
    must now show its narration as a single clean phrase (e.g. "WEIGHT AND
    MEASUREMENT FEES"), not split with the continuation token glued onto a
    leg's particulars/counterparty instead."""
    by_eid = {}
    for r in real_recon_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    for eid, expected_phrase in _FEES_FIXED_VOUCHERS.items():
        rows = by_eid.get(eid)
        assert rows, f"{eid} missing from reconstructed entries"
        narrations = {r["narration"] for r in rows}
        assert len(narrations) == 1, f"{eid} has inconsistent narration across legs: {narrations}"
        narration = next(iter(narrations))
        assert expected_phrase in narration, (
            f"{eid} narration {narration!r} does not contain expected clean "
            f"phrase {expected_phrase!r}"
        )
        # No standalone orphaned continuation-token entry left over.
        parts = [p.strip() for p in narration.split(" | ")]
        assert "FEES" not in parts and "COMPANY" not in parts, (
            f"{eid} still has a standalone orphaned continuation token in "
            f"narration: {parts}"
        )


def test_no_standalone_fees_or_company_narration_entries_anywhere(real_recon_rows):
    """Exhaustive guard: no row anywhere in the whole dataset should have a
    bare 'FEES' or 'COMPANY' narration entry (the bug's symptom), confirming
    the fix is complete, not just patched for the 6 known vouchers."""
    offenders = []
    for r in real_recon_rows:
        parts = [p.strip() for p in r["narration"].split(" | ")]
        if "FEES" in parts or "COMPANY" in parts:
            offenders.append((r["entry_id"], r["narration"]))
    assert not offenders, f"Standalone FEES/COMPANY narration entries remain: {offenders[:10]}"


# ---------- 2. Header-leg-only canonicalization (JANDIYAL, KC EDU.SOCIETY) ----------

def test_header_canonicalized_pairs_present_in_dict():
    from reconstruct_ledger_entries import _ACCOUNT_NAME_CANONICALIZATION

    for short, long in HEADER_CANONICALIZED_PAIRS:
        assert _ACCOUNT_NAME_CANONICALIZATION.get(short) == long, (
            f"{short!r} -> {long!r} missing from _ACCOUNT_NAME_CANONICALIZATION"
        )


def test_no_jandiyal_or_kc_edusociety_variant_survives(real_gl_rows):
    offenders = [
        r for r in real_gl_rows
        if r["account"] in {"JANDIYAL ELECTRONICS", "KC EDU.SOCIETY( GENSET AC"}
    ]
    assert not offenders, (
        f"Found {len(offenders)} rows still carrying the pre-canonicalization "
        f"variant spelling: {[(r['entry_id'], r['account']) for r in offenders[:10]]}"
    )


def test_jandiyal_carried_over_brought_forward_continuity():
    """Pins the exact evidence that proved pages 382/383 are ONE continuous
    ledger, not two different real accounts (reversing the V3.8 read).
    Re-extracts the raw PDF text directly, independent of the reconstruction
    pipeline, so this test fails loudly if the source document ever changes
    in a way that would invalidate the continuity claim."""
    import pdfplumber

    pdf_path = os.path.join(DATA_DIR, "raw_pdfs", "ledgers_redacted.pdf")
    if not os.path.exists(pdf_path):
        pytest.skip("raw PDF not present in this environment")
    with pdfplumber.open(pdf_path) as pdf:
        page382 = pdf.pages[381].extract_text(x_tolerance=1.5)
        page383 = pdf.pages[382].extract_text(x_tolerance=1.5)
    assert "Carried Over" in page382 and "7,34,963.00" in page382 and "6,46,342.00" in page382
    assert "Brought Forward" in page383 and "7,34,963.00" in page383 and "6,46,342.00" in page383


def test_kc_edusociety_carried_over_brought_forward_continuity():
    import pdfplumber

    pdf_path = os.path.join(DATA_DIR, "raw_pdfs", "ledgers_redacted.pdf")
    if not os.path.exists(pdf_path):
        pytest.skip("raw PDF not present in this environment")
    with pdfplumber.open(pdf_path) as pdf:
        page469 = pdf.pages[468].extract_text(x_tolerance=1.5)
        page470 = pdf.pages[469].extract_text(x_tolerance=1.5)
    assert "Carried Over" in page469 and "2,92,702.00" in page469
    assert "Brought Forward" in page470 and "2,92,702.00" in page470


# ---------- 3. Per-voucher cross-spelling consolidation ----------

def test_per_voucher_aliases_dict_contents():
    """_PER_VOUCHER_NAME_ALIASES is a LIST of (short, long) pairs, not a
    dict -- the FY2025-26 ledger investigation found a short form ("CAMBRIDGE")
    that is legitimately shorthand for two DIFFERENT accounts in the two
    different documents, which a dict keyed by short-form string cannot
    represent. This test pins that these 7 FY2024-25 pairs are still present
    unchanged (as a set, since order is not semantically meaningful), rather
    than asserting exact list equality, so FY2025-26-specific pairs can be
    added without this test needing to enumerate them too."""
    from reconstruct_ledger_entries import _PER_VOUCHER_NAME_ALIASES

    assert isinstance(_PER_VOUCHER_NAME_ALIASES, list)
    assert set(PER_VOUCHER_PAIRS).issubset(set(_PER_VOUCHER_NAME_ALIASES))


def test_per_voucher_aliases_not_in_header_canonicalization_dict():
    """These 6 pairs must never be added to _ACCOUNT_NAME_CANONICALIZATION
    -- their short form has no header page anywhere, so that mechanism
    cannot reach them at all (guards against someone "completing the
    pattern" there instead of via _PER_VOUCHER_NAME_ALIASES)."""
    from reconstruct_ledger_entries import _ACCOUNT_NAME_CANONICALIZATION

    for short, long in PER_VOUCHER_PAIRS:
        assert short not in _ACCOUNT_NAME_CANONICALIZATION
        assert long not in _ACCOUNT_NAME_CANONICALIZATION


def test_per_voucher_short_forms_no_longer_survive(real_gl_rows):
    """Every single short-form occurrence of these 6 pairs, across the
    whole 926-page population, had a matching long-form counterpart in the
    same voucher -- so none of the short forms should remain at all."""
    for short, long in PER_VOUCHER_PAIRS:
        offenders = [r for r in real_gl_rows if r["account"] == short]
        assert not offenders, (
            f"{short!r} should have been fully merged into {long!r}; found "
            f"{len(offenders)} remaining rows: "
            f"{[(r['entry_id'], r['amount']) for r in offenders[:5]]}"
        )


def test_aro_office_sample_voucher_merged_correctly(real_gl_rows):
    """Sales GST_MPSS/24-25/135: short form reprinted on pages 618/675
    (detail legs on OTHER ledgers), long form native header leg on page 12.
    Must now be ONE leg, amount unchanged, all 3 source pages preserved."""
    legs = [r for r in real_gl_rows if r["entry_id"] == "Sales GST_MPSS/24-25/135"
            and r["account"] == "ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI"]
    assert len(legs) == 1, f"Expected 1 consolidated leg, found {len(legs)}"
    assert float(legs[0]["amount"]) == pytest.approx(6965.0, abs=0.01)
    assert legs[0]["dr_cr"] == "Dr"
    assert set(legs[0]["source_pages"].split(";")) == {"12", "618", "675"}


def test_per_voucher_merge_never_combines_same_page_occurrences():
    """Structural safety guard, independent of the real data: the merge
    logic in merge_occurrences() must refuse to merge when the short and
    long forms' source_pages overlap (same-page coincidence, not a genuine
    cross-ledger reprint)."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        {
            "entry_id": "FakeVoucher_1", "date": "1-Apr-24", "vch_type": "Journal",
            "vch_no": "1", "page": 100, "account": "Some Account", "narration": [],
            "legs": [
                {"particulars": "ARO OFFICE B-1204", "amount": 500.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": False},
                {"particulars": "ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI", "amount": 500.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
            ],
        },
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    named_legs = entries["FakeVoucher_1"]["named_legs"]
    # Both forms were printed on the SAME page (100) in this synthetic
    # scenario -- must NOT be merged.
    assert ("ARO OFFICE B-1204", "Dr") in named_legs
    assert ("ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI", "Dr") in named_legs


def test_per_voucher_merge_respects_amount_mismatch():
    """If the two spellings appear on different pages but with DIFFERENT
    amounts, they must not be merged (not the same leg)."""
    from reconstruct_ledger_entries import merge_occurrences

    fake_occurrences = [
        {
            "entry_id": "FakeVoucher_2", "date": "1-Apr-24", "vch_type": "Journal",
            "vch_no": "2", "page": 100, "account": "Some Account", "narration": [],
            "legs": [
                {"particulars": "CAMBRIDGE", "amount": 500.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": False},
            ],
        },
        {
            "entry_id": "FakeVoucher_2", "date": "1-Apr-24", "vch_type": "Journal",
            "vch_no": "2", "page": 200, "account": "CAMBRIDGE INTERNATIONAL (GENSET A/C)", "narration": [],
            "legs": [
                {"particulars": "CAMBRIDGE INTERNATIONAL (GENSET A/C)", "amount": 999.0, "dr_cr": "Dr", "resolved": True, "is_header_leg": True},
            ],
        },
    ]
    entries, entry_order, conflicts = merge_occurrences(fake_occurrences)
    named_legs = entries["FakeVoucher_2"]["named_legs"]
    assert ("CAMBRIDGE", "Dr") in named_legs
    assert ("CAMBRIDGE INTERNATIONAL (GENSET A/C)", "Dr") in named_legs


# ---------- 4. The original 77-voucher issue: fully resolved ----------

def test_all_77_originally_flagged_vouchers_now_balanced(real_gl_rows):
    if not os.path.exists(FLIP77_FIXTURE):
        pytest.skip("flip-77 fixture list not present in this environment")
    flip77 = set(open(FLIP77_FIXTURE).read().splitlines())

    by_eid = {}
    for r in real_gl_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)

    unresolved = []
    for eid in flip77:
        legs = by_eid.get(eid)
        if not legs:
            continue
        if legs[0]["entry_balanced"] != "True":
            unresolved.append(eid)
    assert not unresolved, f"Expected all 77 to be balanced; still unbalanced: {unresolved}"


def test_no_new_unbalanced_vouchers_introduced(real_gl_rows):
    """Structural regression guard: these fixes must only ever consolidate
    duplicate legs (reducing or preserving balance), never introduce a NEW
    imbalance in a voucher that wasn't already flagged pre-fix. This is a
    coarse sanity check -- the full before/after diff against the V3.8
    baseline is in BASELINE_CHECKPOINT_V3.9.md."""
    by_eid = {}
    for r in real_gl_rows:
        by_eid.setdefault(r["entry_id"], []).append(r)
    unbalanced = {eid for eid, rows in by_eid.items() if rows[0]["entry_balanced"] == "False"}
    # Post-V3.9: the V3.9 baseline left 29 unbalanced vouchers. Investigating
    # those 29 found 5 distinct root-cause patterns.
    #
    # Patterns 1-3 (two employee salary A/c / EDLI header
    # canonicalization, DISCOUNT Account canonicalization + existing Pass-1
    # dedup, and the Tr. A/c <-> Ledger Tr. A/c per-voucher alias) resolve
    # 21 of the 29, using only the existing, already-proven
    # canonicalization/alias mechanisms -- no new logic.
    #
    # Pattern 4 (redacted-account orphan absorption in merge_occurrences())
    # resolves the remaining 6 (Journal_604/671/672/684/687/690): each
    # voucher's orphan legs (blanked-counterparty reprints on OTHER
    # accounts' pages) are folded into the ALREADY-EXISTING named
    # "[Redacted account -- TB opening ...]" leg of the same amount and
    # dr_cr, removing a double-counted duplicate rather than any real leg.
    # This was gated on an exhaustive, read-only, whole-document bounded
    # simulation BEFORE the code was written: every one of the 22 orphan
    # legs in the full population resolved to exactly 1 eligible candidate,
    # 0 ambiguous, 0 unmatched, exactly these 6 vouchers -- no unexpected
    # candidates. See the checkpoint doc for the full simulation report.
    #
    # Pattern 5 (Payment_2/Sales_Payment header-regex ambiguity) is left
    # deliberately unchanged and deferred: a single occurrence in the full
    # 926-page population, not worth the blast radius of touching the
    # shared header regexes (_HEADER_RE/_FUSED_HEADER_RE/
    # _WRAPPED_PARTICULARS_HEADER_RE) that every other voucher also parses
    # through.
    assert len(unbalanced) == 2, (
        f"Expected 2 unbalanced vouchers after Patterns 1-4 (29 - 27 resolved); "
        f"found {len(unbalanced)}"
    )
    expected_remaining = {
        "Payment_2", "Sales_Payment",  # Pattern 5 (deferred regex ambiguity)
    }
    assert unbalanced == expected_remaining, (
        f"Unexpected set of remaining unbalanced vouchers: {unbalanced} "
        f"(expected exactly {expected_remaining})"
    )


# ---------- 5. Trial balance still clean ----------

def test_trial_balance_still_clean_after_v3_9():
    from reconcile_trial_balance import reconcile

    result = reconcile()
    assert len(result["clean"]) == 82
    assert len(result.get("mismatched", [])) == 0
