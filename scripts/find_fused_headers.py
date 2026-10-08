"""
find_fused_headers.py

Scans the WHOLE document for lines that look like a voucher header
where the voucher number and the following amount got merged with no
space between them (e.g. "Payment 8419,93,000.00 ..." instead of
"Payment 841 9,93,000.00 ..."). This is a suspected pdfplumber text-
extraction quirk (tight kerning collapsing the gap between tokens),
but we have not yet confirmed a single real example -- this script
finds real page numbers so we can inspect them properly instead of
trusting an unverified count.

Also scans for the second suspected shape: a voucher header whose
particulars text is missing (i.e. appears on the FOLLOWING line
instead), e.g.:
    "28-Jul-24 By Payment 204 1,03,272.55 64,22,921.35 Dr"
    "Insurance"

Usage:
    python find_fused_headers.py ../data/raw_pdfs/ledgers_redacted.pdf
"""

import sys
import re
import pdfplumber

_VCH_TYPES = r"Journal|Payment|Receipt|Contra|Sales(?:\s+(?:GST|Cash))?|Purchase(?:\s+GST)?|Debit\s+Note|Credit\s+Note"

# Suspected fused pattern: voucher type, then digits IMMEDIATELY
# followed by a comma (no space) -- e.g. "Payment 8419,93,000.00"
_FUSED_RE = re.compile(
    rf"\b(?:{_VCH_TYPES})\s+(\d+),(\d)"
)

# A normal, correctly-spaced header, for comparison/exclusion
_NORMAL_HEADER_RE = re.compile(
    r"^(?:\d{1,2}-[A-Za-z]{3}-\d{2,4}\s+)?(?:To|By)\s+.+?\s+"
    rf"(?:{_VCH_TYPES})\s+[A-Za-z0-9\/\-_]+\s+[\d,.\sDrC]+$"
)

# A header line ending right after the voucher number + amounts + Dr/Cr,
# where the word immediately after "To"/"By" IS the voucher type itself
# (no particulars text at all before it) -- suggests particulars may be
# on the next line instead.
_NO_PARTICULARS_RE = re.compile(
    rf"^(?:\d{{1,2}}-[A-Za-z]{{3}}-\d{{2,4}}\s+)?(?P<dir>To|By)\s+"
    rf"(?:{_VCH_TYPES})\s+[A-Za-z0-9\/\-_]+\s+[\d,.\sDrC]+$"
)


def main(pdf_path):
    fused_hits = []
    no_particulars_hits = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"Scanning {total} pages for fused-header and missing-particulars patterns...")

        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if not text:
                continue
            for line in text.split("\n"):
                line = line.strip()
                if _FUSED_RE.search(line):
                    fused_hits.append((page_num, line))
                elif _NO_PARTICULARS_RE.match(line):
                    no_particulars_hits.append((page_num, line))

            if page_num % 100 == 0:
                print(f"  ...scanned {page_num}/{total} pages "
                      f"({len(fused_hits)} fused, {len(no_particulars_hits)} no-particulars so far)")

    print(f"\n{'='*70}")
    print(f"FUSED HEADER CANDIDATES: {len(fused_hits)}")
    print(f"{'='*70}")
    for page_num, line in fused_hits[:30]:
        print(f"  Page {page_num}: {line!r}")
    if len(fused_hits) > 30:
        print(f"  ... and {len(fused_hits) - 30} more")

    print(f"\n{'='*70}")
    print(f"POSSIBLE MISSING-PARTICULARS HEADER CANDIDATES: {len(no_particulars_hits)}")
    print(f"{'='*70}")
    for page_num, line in no_particulars_hits[:30]:
        print(f"  Page {page_num}: {line!r}")
    if len(no_particulars_hits) > 30:
        print(f"  ... and {len(no_particulars_hits) - 30} more")

    print(f"\n{'='*70}")
    print("NEXT STEP: pick a few real page numbers above and run:")
    print("  python inspect_ledger_structure.py ../data/raw_pdfs/ledgers_redacted.pdf <page> <page>")
    print("(replace <page> with an actual number from the lists above)")
    print(f"{'='*70}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_fused_headers.py <pdf_path>")
        sys.exit(1)
    main(sys.argv[1])