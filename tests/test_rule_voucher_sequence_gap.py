"""
Regression tests for Rule 7 (scripts/rule_voucher_sequence_gap.py), added
alongside the V3.11 FY2025-26 ledger fixes. See that module's docstring for
the full rationale: gaps are only checked within one (vch_type, prefix)
series, a series needs at least MIN_GROUP_SIZE distinct numbers before a
gap means anything, and a series whose numbers exceed MAX_SEQUENCE_DIGITS
digits is treated as externally-assigned (e.g. a vendor's own invoice
number) and skipped entirely.
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


def _row(vch_type, vch_no, date="1-Apr-24", entry_id=None):
    return {
        "vch_type": vch_type, "vch_no": vch_no, "date": date,
        "entry_id": entry_id or f"{vch_type}_{vch_no}",
    }


# ---------- Core splitting behavior ----------

def test_split_vch_no_pure_numeric():
    from rule_voucher_sequence_gap import _split_vch_no
    assert _split_vch_no("699") == ("", 699)


def test_split_vch_no_slash_joined():
    from rule_voucher_sequence_gap import _split_vch_no
    assert _split_vch_no("MPSS/24-25/507") == ("MPSS/24-25/", 507)


def test_split_vch_no_space_before_slash():
    from rule_voucher_sequence_gap import _split_vch_no
    assert _split_vch_no("INV /25-26/166") == ("INV /25-26/", 166)


def test_split_vch_no_no_trailing_digits_returns_none():
    from rule_voucher_sequence_gap import _split_vch_no
    assert _split_vch_no("Payment") == (None, None)


# ---------- Gap detection ----------

def test_detects_single_gap_in_simple_series():
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = [_row("Journal", str(n)) for n in [1, 2, 3, 5, 6]]
    gaps = find_sequence_gaps(rows)
    assert len(gaps) == 1
    assert gaps[0]["missing_number"] == 4
    assert gaps[0]["nearest_before"]["vch_no"] == "3"
    assert gaps[0]["nearest_after"]["vch_no"] == "5"


def test_no_gap_in_contiguous_series():
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = [_row("Contra", str(n)) for n in range(1, 11)]
    assert find_sequence_gaps(rows) == []


def test_series_below_min_group_size_is_skipped():
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = [_row("Credit Note", "2")]  # only 1 distinct number
    assert find_sequence_gaps(rows) == []


def test_externally_assigned_long_series_is_skipped():
    """A vendor invoice number series (10 digits) must never be gap-tested
    -- the whole point of MAX_SEQUENCE_DIGITS."""
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = [_row("Purchase", str(9011223794 + n)) for n in range(0, 100, 7)]
    assert find_sequence_gaps(rows) == []


def test_different_prefixes_are_gap_tested_independently():
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = (
        [_row("Sales GST", f"MPSS/24-25/{n}") for n in [1, 2, 4]] +
        [_row("Sales GST", f"INV /25-26/{n}") for n in [1, 2, 3]]
    )
    gaps = find_sequence_gaps(rows)
    assert len(gaps) == 1
    assert gaps[0]["prefix"] == "MPSS/24-25/"
    assert gaps[0]["missing_number"] == 3


def test_different_vch_types_are_gap_tested_independently():
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = (
        [_row("Journal", str(n)) for n in [1, 2, 4]] +
        [_row("Payment", str(n)) for n in [1, 2, 3]]
    )
    gaps = find_sequence_gaps(rows)
    assert len(gaps) == 1
    assert gaps[0]["vch_type"] == "Journal"


def test_repeated_vch_no_across_legs_counts_once():
    """The same voucher number appears on many legs/pages of one voucher
    -- must not be treated as extra distinct numbers or break gap math."""
    from rule_voucher_sequence_gap import find_sequence_gaps
    rows = (
        [_row("Journal", "1"), _row("Journal", "1"), _row("Journal", "1")] +
        [_row("Journal", "2")] +
        [_row("Journal", "4")]
    )
    gaps = find_sequence_gaps(rows)
    assert len(gaps) == 1
    assert gaps[0]["missing_number"] == 3
    assert gaps[0]["group_size"] == 3  # distinct numbers: 1, 2, 4


def test_check_voucher_sequence_gaps_matches_find_sequence_gaps():
    from rule_voucher_sequence_gap import check_voucher_sequence_gaps, find_sequence_gaps
    rows = [_row("Journal", str(n)) for n in [1, 2, 3, 5]]
    assert check_voucher_sequence_gaps(rows) == find_sequence_gaps(rows)


# ---------- Against the real (regenerated) ledger ----------

@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_real_ledger_purchase_series_excluded(real_gl_rows):
    """Confirms the digit-length heuristic actually fires on the real
    data: Purchase's own 10-digit vendor invoice numbers must produce zero
    gap candidates."""
    from rule_voucher_sequence_gap import find_sequence_gaps
    gaps = find_sequence_gaps(real_gl_rows)
    assert not any(g["vch_type"] == "Purchase" for g in gaps)


def test_real_ledger_finds_known_series_with_gaps(real_gl_rows):
    """Pinned from the V3.11 investigation: exactly 5 of this ledger's own
    series have gaps (Journal, Payment, Receipt, Sales Cash, Sales GST),
    and Contra/Debit Note -- also Tally-assigned, qualifying series --
    have none (fully contiguous)."""
    from rule_voucher_sequence_gap import find_sequence_gaps
    gaps = find_sequence_gaps(real_gl_rows)
    series_with_gaps = {g["vch_type"] for g in gaps}
    assert series_with_gaps == {"Journal", "Payment", "Receipt", "Sales Cash", "Sales GST"}
