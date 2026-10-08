"""
Shared pytest setup and fixtures.

scripts/ is not a package (no __init__.py, by design -- each file is also
meant to be run standalone with `python scripts/rule_x.py`), so tests
import from it by adding scripts/ to sys.path here, once, for the whole
test session.

Fixture data below is HAND-BUILT, not sampled from the real ledger. Each
row is there to pin one specific behavior we've verified by hand at some
point in this project (see the comment on each row) -- these are
regression tests for rule LOGIC. test_combined_report_regression.py is
the separate suite that checks the real committed data in data/.
"""
import csv
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(TESTS_DIR, "..")
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

sys.path.insert(0, SCRIPTS_DIR)

import pytest


def _row(entry_id, date, account, amount, entry_type="Dr", entered_by="",
         approved_by="", vendor="", vch_no=""):
    """Build a general_ledger.csv-shaped row dict with sensible defaults,
    so each test only has to specify the fields it actually cares about."""
    return {
        "entry_id": entry_id,
        "date": date,
        "timestamp": "",
        "account": account,
        "vendor": vendor,
        "entry_type": entry_type,
        "amount": str(amount),
        "entered_by": entered_by,
        "approved_by": approved_by,
        "narration": "",
        "vch_type": "",
        "vch_no": vch_no,
        "dr_cr": entry_type,
        "resolved": "True",
        "source_pages": "",
        "corroboration_count": "1",
        "entry_balanced": "True",
    }


@pytest.fixture
def gl_rows():
    """A small, hand-built general-ledger fixture covering the specific
    edge cases this project has hit and fixed in real data:

      - same entered_by/approved_by            -> Rule 1 should fire
      - different entered_by/approved_by        -> Rule 1 should NOT fire
      - BOTH entered_by/approved_by empty        -> Rule 1 must NOT fire
        (this guards the historical bug where "" == "" was vacuously
        True for all 17,195 real rows before the empty-string guard
        was added)
      - round 10,000-multiple amount, non-Rent   -> Rule 2 should fire
      - round 10,000-multiple amount, Rent Exp.  -> Rule 2 must NOT fire
        (explicit exemption)
      - non-round amount                         -> Rule 2 should NOT fire
      - two DIFFERENT vouchers, same date/acct/
        amount/direction                         -> Rule 5 should fire
      - legs WITHIN the same voucher sharing
        date/acct/amount/direction               -> Rule 5 must NOT fire
        (same entry_id is a balancing leg, not a duplicate)
      - Dr leg and Cr leg, same date/acct/amount -> Rule 5 must NOT fire
        (direction is part of the match key; a Dr and its own Cr double
        entry are not "duplicates")
      - a 3-way duplicate (three distinct vouchers, same shape, amount
        1,50,000) -> Rule 5 should fire for all three, and
        combined_report.py's severity escalation (>2 distinct vouchers)
        should bump it from its base HIGH to CRITICAL
    """
    return [
        # Rule 1: same approver -- should fire
        _row("JV_1", "1-Apr-24", "Misc Expense", 5000, entered_by="Aarushi", approved_by="Aarushi"),
        # Rule 1: different approver -- should NOT fire
        _row("JV_2", "1-Apr-24", "Misc Expense", 5000, entered_by="Aarushi", approved_by="Dad"),
        # Rule 1: both empty -- must NOT fire (the historical bug)
        _row("JV_3", "1-Apr-24", "Misc Expense", 5000, entered_by="", approved_by=""),

        # Rule 2: round number, non-exempt account -- should fire
        _row("JV_4", "2-Apr-24", "Office Supplies", 20000),
        # Rule 2: round number, Rent Expense -- exempt, should NOT fire
        _row("JV_5", "2-Apr-24", "Rent Expense", 30000),
        # Rule 2: non-round amount -- should NOT fire
        _row("JV_6", "2-Apr-24", "Office Supplies", 20123.45),

        # Rule 5: cross-voucher duplicate pair -- both should fire
        _row("Receipt_10", "3-Apr-24", "J & K Bank Ltd", 100000, entry_type="Cr"),
        _row("Receipt_11", "3-Apr-24", "J & K Bank Ltd", 100000, entry_type="Cr"),

        # Rule 5: same voucher, two legs, same account/amount/date/direction
        # -- must NOT fire (same entry_id)
        _row("Journal_20", "4-Apr-24", "Sundry Debtors", 7000, entry_type="Dr"),
        _row("Journal_20", "4-Apr-24", "Sundry Debtors", 7000, entry_type="Dr"),

        # Rule 5: Dr leg and Cr leg of a normal double-entry, same
        # account/amount/date -- must NOT fire (different direction)
        _row("Journal_21", "5-Apr-24", "Cash", 15000, entry_type="Dr"),
        _row("Journal_22", "5-Apr-24", "Cash", 15000, entry_type="Cr"),

        # Rule 5: 3-way duplicate across distinct vouchers, amount chosen
        # (1,50,000 -> base severity HIGH) so escalating one tier for
        # spanning >2 vouchers lands on CRITICAL -- a clean, different
        # tier than the 2-way Receipt_10/11 pair above (which stays HIGH,
        # no escalation, at the same base amount band)
        _row("Sales_30", "6-Apr-24", "Sales", 150000, entry_type="Cr"),
        _row("Sales_31", "6-Apr-24", "Sales", 150000, entry_type="Cr"),
        _row("Sales_32", "6-Apr-24", "Sales", 150000, entry_type="Cr"),
    ]


@pytest.fixture
def form26as_rows():
    """Hand-built form26as.csv fixture covering Rule 4's severity bands
    (classify_severity: >=30% CRITICAL, >=10% HIGH, >0% MEDIUM, 0% NONE)
    and the zero-claimed divide-by-zero guard."""
    return [
        # Exact match -- should NOT flag (within Rs.10 tolerance)
        {"deductor_name": "Clean Deductor", "tds_reported_by_deductor": "10000.00",
         "tds_claimed_in_itr_share": "10000.00"},
        # 50% gap -- CRITICAL
        {"deductor_name": "Critical Deductor", "tds_reported_by_deductor": "5000.00",
         "tds_claimed_in_itr_share": "10000.00"},
        # 15% gap -- HIGH
        {"deductor_name": "High Deductor", "tds_reported_by_deductor": "8500.00",
         "tds_claimed_in_itr_share": "10000.00"},
        # 5% gap -- MEDIUM
        {"deductor_name": "Medium Deductor", "tds_reported_by_deductor": "9500.00",
         "tds_claimed_in_itr_share": "10000.00"},
        # claimed = 0 but reported != 0 -- must not divide by zero
        {"deductor_name": "Zero Claimed Deductor", "tds_reported_by_deductor": "500.00",
         "tds_claimed_in_itr_share": "0.00"},
    ]
