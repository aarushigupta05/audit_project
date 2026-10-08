"""
prepare_input.py

Checkpointed PDF -> CSV preview tool.

This is INTENTIONALLY a separate manual step from combined_report.py.
It converts a PDF (or Excel) into CSV using pdf_to_csv_converter.py,
then shows you exactly what came out -- column names, row count, and
a data sample -- so YOU decide whether/how to map it into the real
pipeline. Nothing here auto-feeds into general_ledger.csv/form26as.csv.

Usage:
    python prepare_input.py ../data/raw_pdfs/your_file.pdf
"""

import sys
import os
import csv

sys.path.append(os.path.dirname(__file__))
from pdf_to_csv_converter import safe_ensure_csv_input

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")


def preview_csv(path, n_rows=8):
    with open(path, "r", newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        print(f"  [EMPTY FILE] {path}")
        return

    header = rows[0]
    data_rows = rows[1:]

    print(f"\n  File: {path}")
    print(f"  Columns ({len(header)}): {header}")
    print(f"  Row count: {len(data_rows)}")
    print(f"  Sample rows:")
    for row in data_rows[:n_rows]:
        print(f"    {row}")

    if data_rows:
        for col_idx, col_name in enumerate(header):
            sample_vals = [r[col_idx] for r in data_rows[:50] if col_idx < len(r)]
            numeric_like = sum(
                1 for v in sample_vals
                if v.replace(",", "").replace(".", "").replace("-", "").replace("(", "").replace(")", "").strip().isdigit()
            )
            if sample_vals:
                pct = round(100 * numeric_like / len(sample_vals))
                if pct > 50:
                    print(f"      -> '{col_name}' looks numeric ({pct}% of sample)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python prepare_input.py <path_to_pdf_or_excel>")
        return

    input_file = sys.argv[1]
    print(f"Converting: {input_file}")

    result, error = safe_ensure_csv_input(input_file, output_dir=DATA_DIR)

    if error:
        print(f"\nCONVERSION FAILED: {error}")
        print("Nothing was written. Main pipeline is unaffected.")
        return

    paths = result if isinstance(result, list) else [result]

    print(f"\n{len(paths)} CSV file(s) produced. Review each before mapping into the pipeline:")
    for p in paths:
        preview_csv(p)

    print("\n" + "=" * 70)
    print("NEXT STEP (manual): decide which columns map to the schema")
    print("combined_report.py expects, e.g.:")
    print("  general_ledger.csv needs: entry_id, date, account, amount,")
    print("                            entered_by, approved_by")
    print("  form26as.csv needs:       deductor_name, tds_reported_by_deductor,")
    print("                            tds_claimed_in_itr_share")
    print("Nothing has been overwritten in data/. Run this again on other")
    print("files, or note the column names you see so a mapping")
    print("script can be written for your specific document.")
    print("=" * 70)


if __name__ == "__main__":
    main()