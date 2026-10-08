"""
RULE 7: Voucher-Number Sequence Gap Detection
-----------------------------------------------
Tally (like most ERPs) auto-assigns the next voucher number in a series as
each voucher is entered, so a series should run with no gaps: 1, 2, 3, 4...
A missing number inside an otherwise-continuous run is a standard forensic-
accounting red flag: it means a voucher was deleted, entered and discarded,
or removed from the system some other way, and the ledger as it stands
today carries no record of what happened to it. This rule does not decide
WHY a number is missing -- a legitimately voided/cancelled voucher looks
identical, from the ledger alone, to a suppressed one. Same candidate-for-
review philosophy as rule_duplicate_transaction.py: flagged, not accused.

Grouping into series:
A voucher type doesn't share one global number line. Tally restarts
numbering per financial year, and a company may run more than one printed
series under the same vch_type (e.g. "MPSS/24-25/507" vs a bare "507" for
Journal/Payment/Receipt/Contra). Each voucher's vch_no is split into
(prefix, trailing integer) -- "MPSS/24-25/507" -> ("MPSS/24-25/", 507),
"507" -> ("", 507) -- and gaps are only ever checked WITHIN one
(vch_type, prefix) group, never across them.

Excluding externally-assigned number series:
Not every vch_no is this company's own sequence. A vendor's Purchase-bill
number (e.g. "9011223794") is assigned by the VENDOR, not by Tally, so it
has no reason to run gap-free from this company's side -- "gaps" there
would be 100% noise. Verified directly against this ledger: the company's
own series (Journal, Payment, Receipt, Contra, Sales GST/Cash, Debit/Credit
Note) all stay under 6 digits; Purchase vch_nos are 10-digit vendor invoice
numbers. Rather than hardcode "Purchase" by name (which wouldn't generalize
to a different company's chart of accounts -- see the FY2025-26 ledger
investigation on this same project for why per-document hardcoding doesn't
travel), a group is skipped as a non-sequential series whenever its own
numbers exceed MAX_SEQUENCE_DIGITS digits -- a general, portable proxy for
"this isn't a number Tally assigned here," not a one-off exception.

Minimum group size:
A group needs at least MIN_GROUP_SIZE distinct numbers before gap-testing
means anything -- one or two vouchers in a brand-new series are not
evidence of anything, gap or no gap.
"""

import csv
import os
import re
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
INPUT_FILE = os.path.join(DATA_DIR, "general_ledger.csv")

# Numbers longer than this are treated as externally-assigned (e.g. a
# vendor's own invoice number), not a series Tally itself incremented.
MAX_SEQUENCE_DIGITS = 6
# Fewer distinct numbers than this in a (vch_type, prefix) group -- not
# enough evidence to call anything a gap either way.
MIN_GROUP_SIZE = 3

# Non-greedy prefix + anchored trailing digit run: finds the shortest
# prefix for which everything after it, to the end of the string, is
# digits -- i.e. the trailing number, however many non-digit separators
# ("/", "-", a space) appear earlier in the string.
_TRAILING_NUMBER_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")


def _split_vch_no(vch_no):
    """Returns (prefix, number) for a vch_no with a trailing digit run, or
    (None, None) if it has none at all (left untested rather than guessed
    at -- e.g. a stray non-numeric value)."""
    if not vch_no:
        return None, None
    m = _TRAILING_NUMBER_RE.match(vch_no.strip())
    if not m:
        return None, None
    return m.group("prefix"), int(m.group("num"))


def find_sequence_gaps(rows):
    """
    Returns a list of gap-candidate dicts, one per missing integer found
    inside a qualifying (vch_type, prefix) group's observed range:
      {vch_type, prefix, missing_number, group_min, group_max, group_size,
       nearest_before: {vch_no, date, entry_id} or None,
       nearest_after: {vch_no, date, entry_id} or None}
    """
    # (vch_type, prefix) -> {number: (vch_no, date, entry_id)}
    groups = defaultdict(dict)

    for row in rows:
        vch_type = (row.get("vch_type") or "").strip()
        vch_no = (row.get("vch_no") or "").strip()
        if not vch_type or not vch_no:
            continue
        prefix, num = _split_vch_no(vch_no)
        if num is None:
            continue
        key = (vch_type, prefix)
        # A number can repeat across several legs/pages of the same
        # voucher -- keep the first sighting, don't overwrite it.
        if num not in groups[key]:
            groups[key][num] = (vch_no, row.get("date", ""), row.get("entry_id", ""))

    gaps = []
    for (vch_type, prefix), numbers in groups.items():
        distinct = sorted(numbers)
        if len(distinct) < MIN_GROUP_SIZE:
            continue
        if len(str(distinct[-1])) > MAX_SEQUENCE_DIGITS:
            continue  # externally-assigned series -- not ours to gap-test

        present = set(distinct)
        lo, hi = distinct[0], distinct[-1]
        for n in range(lo, hi + 1):
            if n in present:
                continue
            before = max((x for x in present if x < n), default=None)
            after = min((x for x in present if x > n), default=None)
            gaps.append({
                "vch_type": vch_type,
                "prefix": prefix,
                "missing_number": n,
                "group_min": lo,
                "group_max": hi,
                "group_size": len(distinct),
                "nearest_before": dict(zip(
                    ("vch_no", "date", "entry_id"), numbers[before]
                )) if before is not None else None,
                "nearest_after": dict(zip(
                    ("vch_no", "date", "entry_id"), numbers[after]
                )) if after is not None else None,
            })

    return gaps


def check_voucher_sequence_gaps(rows):
    """Convenience function matching the other rule_*.py modules' naming."""
    return find_sequence_gaps(rows)


if __name__ == "__main__":
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Total legs in ledger: {len(rows)}")

    gaps = find_sequence_gaps(rows)
    print(f"Voucher-number gaps found: {len(gaps)}")
    print()

    by_series = defaultdict(list)
    for g in gaps:
        by_series[(g["vch_type"], g["prefix"])].append(g)

    for (vch_type, prefix), series_gaps in sorted(by_series.items()):
        label = f"{vch_type} {prefix}".strip()
        g0 = series_gaps[0]
        print(f"  {label}: {len(series_gaps)} missing number(s) in range "
              f"{g0['group_min']}-{g0['group_max']} (series size {g0['group_size']})")
        for g in series_gaps[:5]:
            before, after = g["nearest_before"], g["nearest_after"]
            print(f"    missing #{g['missing_number']}"
                  f" (between {before['vch_no'] if before else 'series start'}"
                  f" on {before['date'] if before else '?'}"
                  f" and {after['vch_no'] if after else 'series end'}"
                  f" on {after['date'] if after else '?'})")
        if len(series_gaps) > 5:
            print(f"    ... and {len(series_gaps) - 5} more")
