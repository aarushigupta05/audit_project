"""
inspect_unparsed.py

Shows the actual text of rows that parse_daybook_lines.py could NOT
parse (status == 'unparsed', excluding period markers), so we can see
the real failure pattern instead of guessing at a regex fix blind.

Usage:
    python inspect_unparsed.py ../data/table_group_1.csv
"""

import sys
import os
import csv

sys.path.append(os.path.dirname(__file__))
from parse_daybook_lines import parse_line


def main(input_path, n_samples=30):
    with open(input_path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    unparsed = []
    for row in rows:
        text = row.get("col_1", "")
        particulars, amount, dr_cr, status = parse_line(text)
        if status == "unparsed":
            unparsed.append(text)

    print(f"Total genuinely unparsed: {len(unparsed)} / {len(rows)}")
    print(f"\nShowing up to {n_samples} examples (repr'd to reveal hidden characters):\n")
    for text in unparsed[:n_samples]:
        print(f"  {repr(text)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_unparsed.py <table_group_1.csv path>")
        sys.exit(1)
    main(sys.argv[1])