"""
classify_document.py
---------------------
Content-based document-type classifier, built 2026-10-04 for the
dashboard's generic upload flow: "upload a document, it gets tested" has
to work for ANY document someone drops in, not just the exact filenames
this project's own adapters happen to glob-match
(adapt_gstr_to_schema.GSTR1_GLOB, adapt_tax_docs_to_schema.INPUT_26AS,
etc.). Those adapters select a file by its NAME; this module looks at
its actual CONTENT and says what kind of document it is, so the dashboard
can route an upload to the right pipeline (or honestly say it doesn't
recognize the document) regardless of what it's called.

Every marker here is a DIRECTLY VERIFIED string or pattern pulled from
this project's own real documents (see each check's comment for which
file it was confirmed against) -- not a guess at what a generic Indian
tax/GST document "probably" looks like. That keeps this consistent with
the project's standing "never guess, verify against real data" rule.

Returns one of:
    "ledger"              -- Date Particulars / Vch Type ledger export
                              (reconstruct_ledger_entries.py's domain)
    "trial_balance"       -- Tally's own Trial Balance export
    "form_26as"           -- Annual Tax Statement (Form 26AS)
    "itr_computation"     -- Computation of Income/Tax (COI)
    "itr_acknowledgement" -- Filed ITR-V acknowledgement
    "gstr1"               -- Form GSTR-1 (single month or an All_Months bundle)
    "gstr3b"              -- Form GSTR-3B (single month or an All_Months bundle)
    "unknown"             -- didn't match any of the above with confidence

classify_document() NEVER raises -- a PDF it can't even open (corrupt,
password-protected, zero pages, not actually a PDF despite the
extension) comes back "unknown" with the failure recorded as evidence,
not an exception the caller has to guard against. This is the single
most important property for the dashboard's "don't fail on any
document" requirement: every other safety behavior (quality gating,
graceful per-document error handling) builds on this function never
being the thing that crashes.
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import pdfplumber

from pdf_to_csv_converter import check_is_ledger_document

DOCTYPES = (
    "ledger", "trial_balance", "form_26as", "itr_computation",
    "itr_acknowledgement", "gstr1", "gstr3b", "unknown",
)


def _page_text(pdf, index):
    """pdfplumber page text, or '' for a page that extracts nothing (a
    blank/image-only page) -- never None, so every caller can do a plain
    'in text' check without a None-guard."""
    if index >= len(pdf.pages):
        return ""
    return pdf.pages[index].extract_text() or ""


def classify_document(pdf_path):
    """
    Returns {"doctype": one of DOCTYPES, "evidence": str, "error": str|None,
    "issue": None | "scanned" | "unreadable"}.

    `issue` says WHY an "unknown" document could not be read, so the caller
    can tell the person what to do instead of just "not recognised":
    "scanned" -- the PDF opens but its first pages hold no selectable text
    (a scan or photo: nothing here can read it, the digital export is
    needed); "unreadable" -- the file would not open at all (corrupt,
    password-protected, not really a PDF, zero pages). None otherwise.

    `evidence` is a short, human-readable note on what was matched (or,
    for "unknown", what was checked and found absent) -- shown in the
    dashboard's Document Library so a person can see WHY something was
    or wasn't recognized, not just the bare label. `error` is set only
    when the PDF itself couldn't be opened/read at all; doctype is
    "unknown" in that case too, but `error` lets the caller show "this
    file couldn't be read" instead of "this file wasn't recognized" --
    different problems, different fixes.

    Checked in this order (most distinctive, least collision-prone
    first):
      1. ledger             -- check_is_ledger_document() (its own
                                 established, independently-tested logic)
      2. itr_acknowledgement -- "INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT"
                                 verified verbatim on page 1 of
                                 income_tax_acknowledgement_redacted.pdf
      3. gstr1 / gstr3b      -- "FORM GSTR-1" / "Form GSTR-3B" verified
                                 verbatim on page 1 of both the single-
                                 month and the All_Months bundle PDFs
      4. form_26as           -- "Annual Tax Statement" + a "Financial
                                 Year ... Assessment Year ..." line,
                                 verified verbatim on page 1 of
                                 annual_tax_statement_redacted.pdf
      5. trial_balance       -- a page-1 line starting "Trial Balance",
                                 the same marker parse_trial_balance.py
                                 already relies on to skip the title row
      6. itr_computation     -- "Asstt. Year" combined with either
                                 "Residential Status" or "Due Date of
                                 Filing" on page 1, verified against
                                 income_computation_redacted.pdf (this
                                 document has no single standalone title
                                 line the way the others do, so it needs
                                 two markers together to be distinctive)
    """
    result = _classify(pdf_path)
    result.setdefault("issue", None)
    return result


def _classify(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) == 0:
                return {"doctype": "unknown", "evidence": "PDF has zero pages", "error": None,
                        "issue": "unreadable"}

            # Ledger check first: it already scans up to 5 pages with its
            # own weighted-evidence logic, independently tested since
            # before this classifier existed.
            if check_is_ledger_document(pdf_path):
                return {
                    "doctype": "ledger",
                    "evidence": "Matched ledger column/account headers (Date Particulars / Vch Type "
                                "or Ledger Account) on multiple initial pages.",
                    "error": None,
                }

            page1 = _page_text(pdf, 0)

            if "INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT" in page1:
                return {
                    "doctype": "itr_acknowledgement",
                    "evidence": "Page 1 contains \"INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT\".",
                    "error": None,
                }

            if "FORM GSTR-1" in page1:
                return {
                    "doctype": "gstr1",
                    "evidence": "Page 1 contains \"FORM GSTR-1\".",
                    "error": None,
                }

            if "Form GSTR-3B" in page1:
                return {
                    "doctype": "gstr3b",
                    "evidence": "Page 1 contains \"Form GSTR-3B\".",
                    "error": None,
                }

            import re
            if "Annual Tax Statement" in page1 and re.search(
                r"Financial Year\s+\d{4}-\d{2}.*?Assessment Year\s+\d{4}-\d{2}", page1
            ):
                return {
                    "doctype": "form_26as",
                    "evidence": "Page 1 contains \"Annual Tax Statement\" and a "
                                "\"Financial Year ... Assessment Year ...\" header line.",
                    "error": None,
                }

            if any(line.strip().startswith("Trial Balance") for line in page1.split("\n")):
                return {
                    "doctype": "trial_balance",
                    "evidence": "Page 1 has a line starting with \"Trial Balance\".",
                    "error": None,
                }

            if "Asstt. Year" in page1 and (
                "Residential Status" in page1 or "Due Date of Filing" in page1
            ):
                return {
                    "doctype": "itr_computation",
                    "evidence": "Page 1 contains \"Asstt. Year\" together with "
                                "\"Residential Status\"/\"Due Date of Filing\".",
                    "error": None,
                }

            sample = " ".join(_page_text(pdf, i) for i in range(min(3, len(pdf.pages)))).strip()
            if not sample:
                return {
                    "doctype": "unknown",
                    "evidence": "This PDF has no selectable text -- it looks like a scan or a "
                                "photo of a document, which cannot be read here. Upload the "
                                "digital PDF exported from Tally or downloaded from the tax "
                                "portal instead.",
                    "error": None,
                    "issue": "scanned",
                }
            return {
                "doctype": "unknown",
                "evidence": "No recognized marker (ledger/trial-balance/26AS/ITR/GSTR-1/GSTR-3B) "
                            "found on the first page(s).",
                "error": None,
            }
    except Exception as e:
        return {
            "doctype": "unknown",
            "evidence": f"Could not read this PDF: {e}",
            "error": str(e),
            "issue": "unreadable",
        }


if __name__ == "__main__":
    import glob

    RAW_PDFS_DIR = os.path.join(SCRIPT_DIR, "..", "data", "raw_pdfs")
    for path in sorted(glob.glob(os.path.join(RAW_PDFS_DIR, "*.pdf"))):
        result = classify_document(path)
        print(f"{os.path.basename(path):45s} -> {result['doctype']:20s} {result['evidence']}")
