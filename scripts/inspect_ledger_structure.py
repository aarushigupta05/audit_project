"""
inspect_ledger_structure.py

READ-ONLY diagnostic. Does not modify pdf_to_csv_converter.py or write
any CSV. Purpose: see the ledger's RAW physical structure before
designing reconstruct_ledger_entries(), instead of guessing a
voucher-header regex blind.

For each page in the given range, prints THREE views side by side:

  1. RAW TEXT LINES  -- page.extract_text(), exactly as pdfplumber
     sees it, completely untouched by any of our classification logic.
     This answers: does the date/voucher-type/voucher-number actually
     appear on its own line, on the same line as the first leg, or
     not at all on some pages?

  2. RAW WORDS WITH POSITION -- page.extract_words(), so we can see
     the x0/top coordinates behind each token. Useful for checking
     whether a voucher header and its first leg share a "top" (i.e.
     are on the physical same line) or are just visually close.

  3. WHAT extract_tables() CURRENTLY PRODUCES for the same page --
     run through the *actual* production function from
     pdf_to_csv_converter.py, unmodified. This shows exactly where
     information present in view 1 disappears (or doesn't) once it
     goes through table detection -- answering whether date/voucher
     info is lost DURING extraction or was never reliably there.

Usage:
    python inspect_ledger_structure.py ../data/raw_pdfs/ledgers_redacted.pdf 1 10
    (inspects pages 1-10, 1-indexed, inclusive)
"""

import sys
import os
import pdfplumber

sys.path.append(os.path.dirname(__file__))
from pdf_to_csv_converter import PDFToCSVConverter


def dump_raw_text(pdf, start_page, end_page):
    print("\n" + "=" * 70)
    print("VIEW 1: RAW TEXT LINES (page.extract_text())")
    print("=" * 70)
    for page_num in range(start_page, end_page + 1):
        if page_num > len(pdf.pages):
            break
        page = pdf.pages[page_num - 1]
        text = page.extract_text()
        print(f"\n--- Page {page_num} ---")
        if not text:
            print("  (no extractable text on this page)")
            continue
        for line in text.split("\n"):
            print(f"  {repr(line)}")


def dump_raw_words(pdf, start_page, end_page, max_words_per_page=60):
    print("\n" + "=" * 70)
    print("VIEW 2: RAW WORDS WITH POSITION (page.extract_words())")
    print("=" * 70)
    for page_num in range(start_page, end_page + 1):
        if page_num > len(pdf.pages):
            break
        page = pdf.pages[page_num - 1]
        words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
        print(f"\n--- Page {page_num} ({len(words)} words total, "
              f"showing first {max_words_per_page}) ---")
        for w in words[:max_words_per_page]:
            print(f"  top={w['top']:.1f}  x0={w['x0']:.1f}  text={w['text']!r}")


def dump_current_extract_tables_output(pdf_path, start_page, end_page):
    print("\n" + "=" * 70)
    print("VIEW 3: CURRENT extract_tables() OUTPUT (production code, unmodified)")
    print("=" * 70)
    converter = PDFToCSVConverter(pdf_path, output_dir="/tmp/ledger_inspect_scratch")
    all_tables = converter.extract_tables()
    if not all_tables:
        print("  extract_tables() returned nothing for this document.")
        return
    shown = 0
    for df in all_tables:
        page = df["__source_page"].iloc[0] if "__source_page" in df.columns else None
        if page is None or not (start_page <= int(page) <= end_page):
            continue
        print(f"\n--- Fragment from page {page} (table_index={df['__table_index'].iloc[0]}) ---")
        print(f"  Columns: {list(df.columns)}")
        for _, row in df.head(6).iterrows():
            print(f"    {dict(row)}")
        shown += 1
        if shown >= 20:
            print("\n  (stopping after 20 fragments to keep output readable)")
            break
    if shown == 0:
        print(f"  No fragments found with __source_page in [{start_page}, {end_page}].")


def main():
    if len(sys.argv) < 4:
        print("Usage: python inspect_ledger_structure.py <pdf_path> <start_page> <end_page>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    start_page = int(sys.argv[2])
    end_page = int(sys.argv[3])

    with pdfplumber.open(pdf_path) as pdf:
        print(f"PDF has {len(pdf.pages)} total pages. Inspecting pages {start_page}-{end_page}.")
        dump_raw_text(pdf, start_page, end_page)
        dump_raw_words(pdf, start_page, end_page)

    dump_current_extract_tables_output(pdf_path, start_page, end_page)


if __name__ == "__main__":
    main()