"""
parse_daybook_lines.py (v2)

table_group_1.csv has account name, amount, and Dr/Cr all squashed into
one text cell per row, e.g.:
    "Employer Contribution to PF 7,800.00 Dr"

Two real-world complications found by testing against actual data:

1. Wrapped entity names: when an entity name spans two physical PDF
   lines (e.g. "Delhi Public School" / "Nagbani Jammu"), the amount
   ends up in the MIDDLE of the merged text, not at the end:
       "Delhi Public School 3,000.00 Dr Nagbani Jammu"
   Fixed by searching for the amount pattern anywhere in the line, then
   stitching the text before + after it back into one particulars field.

2. Month/period header lines ("May 2024", "December") are not
   transactions at all -- they're section dividers. These are tagged
   as 'period_marker' and reported separately, not counted as parse
   failures, since they were never supposed to parse as amounts.

Usage:
    python parse_daybook_lines.py ../data/table_group_1.csv
"""

import sys
import os
import re
import csv

sys.path.append(os.path.dirname(__file__))
from pdf_to_csv_converter import normalize_indian_number

_AMOUNT_PATTERN = re.compile(
    r"""
    (?:Rs\.?\s*)?
    (
        (?:\d{1,3}(?:,\d{2,3})+|\d+)
        (?:\.\d+)?
    )
    \s*
    (Dr|Cr)
    \b
    """,
    re.IGNORECASE | re.VERBOSE
)

_MONTH_HEADER_PATTERN = re.compile(
    r"^(January|February|March|Apri[l;]?|May|June|July|August|September"
    r"|October|November|December)(\s+\d{4})?$",
    re.IGNORECASE,
)


def parse_line(text):
    """
    Returns (particulars, amount, dr_cr, status).
    status is one of: 'parsed', 'period_marker', 'unparsed', 'empty'
    """
    if not text or not text.strip():
        return "", None, None, "empty"

    stripped = text.strip()

    if _MONTH_HEADER_PATTERN.match(stripped):
        return stripped, None, None, "period_marker"

    match = _AMOUNT_PATTERN.search(stripped)
    if not match:
        return stripped, None, None, "unparsed"

    amount_str, dr_cr = match.groups()
    amount = normalize_indian_number(amount_str)

    before = stripped[:match.start()].strip()
    after = stripped[match.end():].strip()
    particulars = f"{before} {after}".strip() if after else before

    return particulars, amount, dr_cr.title(), "parsed"


def parse_file(input_path, output_path=None):
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_parsed{ext}"

    with open(input_path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("Input file is empty.")
        return

    text_col = "col_1"
    parsed_rows = []
    counts = {"parsed": 0, "period_marker": 0, "unparsed": 0, "empty": 0}

    for row in rows:
        particulars, amount, dr_cr, status = parse_line(row.get(text_col, ""))
        counts[status] += 1
        parsed_rows.append({
            "particulars": particulars,
            "amount": amount,
            "dr_cr": dr_cr,
            "status": status,
            "source_page": row.get("__source_page", ""),
            "table_index": row.get("__table_index", ""),
        })

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["particulars", "amount", "dr_cr", "status", "source_page", "table_index"]
        )
        writer.writeheader()
        writer.writerows(parsed_rows)

    total = len(rows)
    print(f"Parsed {total} rows -> {output_path}")
    print(f"  parsed (real transactions):  {counts['parsed']} ({round(100*counts['parsed']/total,1)}%)")
    print(f"  period markers (not txns):   {counts['period_marker']} ({round(100*counts['period_marker']/total,1)}%)")
    print(f"  empty rows:                  {counts['empty']} ({round(100*counts['empty']/total,1)}%)")
    print(f"  still unparsed (real fails): {counts['unparsed']} ({round(100*counts['unparsed']/total,1)}%)")

    print("\nSample of parsed transactions:")
    shown = 0
    for r in parsed_rows:
        if r["status"] == "parsed":
            print(f"  {r}")
            shown += 1
        if shown >= 8:
            break


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_daybook_lines.py <table_group_1.csv path>")
        sys.exit(1)
    parse_file(sys.argv[1])