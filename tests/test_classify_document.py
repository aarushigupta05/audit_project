"""
Tests for scripts/classify_document.py -- the content-based document-type
classifier built 2026-10-04 for the dashboard's generic upload flow (see
that module's own docstring for the full rationale: this project's
existing adapters all select a file by NAME; this classifier looks at its
CONTENT instead, so "upload a document and it gets tested" works for any
filename).

Two kinds of coverage, matching this project's standing "never guess,
verify against real data" discipline:
  1. Every REAL document in data/raw_pdfs/ classifies to the expected
     doctype -- pinned, not just "doesn't crash".
  2. classify_document() NEVER RAISES, on several distinct real failure
     modes (not a PDF at all, an empty file, a missing path, a valid PDF
     with unrelated content) -- the single most important property for
     the dashboard's "don't fail on any document" requirement, since
     every other safety behavior in dashboard/data.py's
     process_ledger_document() builds on this function never being the
     thing that crashes.

Also covers check_is_ledger_document() (scripts/pdf_to_csv_converter.py),
which classify_document() delegates ledger detection to and which had
ZERO test coverage before this phase despite being relied on here and (per
the user's own request) by the dashboard's upload flow.
"""
import os

import pytest

import classify_document as cd
from pdf_to_csv_converter import check_is_ledger_document

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(TESTS_DIR, "..")
RAW_PDFS_DIR = os.path.join(PROJECT_ROOT, "data", "raw_pdfs")


# ---------- Real documents: pinned classifications ----------
# One entry per real PDF this project has (see README's "What this project
# covers" list) -- if a new real document is ever added to data/raw_pdfs/
# without a corresponding entry here, test_all_real_pdfs_are_covered below
# fails loudly instead of this suite silently not checking it.
REAL_DOCUMENT_DOCTYPES = {
    "ledgers_redacted.pdf": "ledger",
    "Ledgers_Anonymised.pdf": "ledger",
    "trial_balance_redacted.pdf": "trial_balance",
    "annual_tax_statement_redacted.pdf": "form_26as",
    "income_computation_redacted.pdf": "itr_computation",
    "income_tax_acknowledgement_redacted.pdf": "itr_acknowledgement",
    "gstr1_june_2026_redacted.pdf": "gstr1",
    "gstr3b_june_2026_redacted.pdf": "gstr3b",
    "GSTR1_All_Months_Anonymised.pdf": "gstr1",
    "GSTR3B_All_Months_Anonymised.pdf": "gstr3b",
    "Index_Anonymised.pdf": "unknown",  # Tally's own account index/TOC page, not ledger content
    # Added 2026-10-06 (directory audit). These five were already in
    # data/raw_pdfs/ on the developer's machine but had no pinned
    # expectation; they are gitignored, so no cloud/CI copy ever had them
    # and this guard never fired there. Each value below was verified by
    # running classify_document() on the actual file, not assumed.
    #   26AS.pdf, Computation_of_Income.pdf: FY2025-26 documents from the
    #     batch scripts/adapt_tax_docs_to_schema.py's HISTORY note identifies
    #     as a DIFFERENT person's filing -- deliberately not pipeline inputs.
    #   ITR_V.PDF: an ITR acknowledgement from that same 2026-08-18 batch.
    #   AIS.pdf: a real Annual Information Statement (an individual's
    #     FY2025-26, not the firm's) that classify_document() does not
    #     recognise yet. Pinned as "unknown" on purpose: if AIS support is
    #     ever added, this entry fails loudly and gets updated deliberately.
    "26AS.pdf": "form_26as",
    "Computation_of_Income.pdf": "itr_computation",
    "ITR_V.PDF": "itr_acknowledgement",
    "AIS.pdf": "unknown",
    "ledger_index_redacted.pdf": "unknown",  # Tally's own account index/TOC page for the FY2024-25 ledger, same shape as Index_Anonymised.pdf
}


@pytest.mark.parametrize("filename,expected_doctype", sorted(REAL_DOCUMENT_DOCTYPES.items()))
def test_real_document_classification(filename, expected_doctype):
    path = os.path.join(RAW_PDFS_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not present in this environment")
    result = cd.classify_document(path)
    assert result["doctype"] == expected_doctype, (
        f"{filename} classified as {result['doctype']!r} (evidence: "
        f"{result['evidence']!r}), expected {expected_doctype!r}"
    )
    assert result["error"] is None
    assert result["evidence"]  # always a non-empty explanation, success or not


def test_all_real_pdfs_are_covered():
    """Guards against a new real document being dropped into
    data/raw_pdfs/ without anyone adding a pinned expectation for it
    here -- this project's standing discipline is that every real
    document gets a verified expectation, not an assumed one."""
    if not os.path.isdir(RAW_PDFS_DIR):
        pytest.skip("data/raw_pdfs/ not present in this environment")
    on_disk = {f for f in os.listdir(RAW_PDFS_DIR) if f.lower().endswith(".pdf")}
    uncovered = on_disk - set(REAL_DOCUMENT_DOCTYPES)
    assert not uncovered, (
        f"New real PDF(s) with no pinned expectation in this test: {uncovered}. "
        f"Add them to REAL_DOCUMENT_DOCTYPES above."
    )


def test_doctype_is_always_one_of_doctypes():
    for filename in REAL_DOCUMENT_DOCTYPES:
        path = os.path.join(RAW_PDFS_DIR, filename)
        if not os.path.exists(path):
            continue
        assert cd.classify_document(path)["doctype"] in cd.DOCTYPES


# ---------- Never raises: synthetic failure modes ----------

def test_garbage_bytes_never_raises(tmp_path):
    """A .pdf extension on a file that isn't actually a PDF at all (the
    realistic shape of a mis-saved upload) must come back 'unknown' with
    the read failure recorded, not propagate an exception."""
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"this is not a real pdf file, just plain garbage bytes 1234567890")
    result = cd.classify_document(str(path))
    assert result["doctype"] == "unknown"
    assert result["error"] is not None
    assert "could not" in result["evidence"].lower()


def test_empty_file_never_raises(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    result = cd.classify_document(str(path))
    assert result["doctype"] == "unknown"
    assert result["error"] is not None


def test_nonexistent_path_never_raises():
    result = cd.classify_document("/tmp/this_path_does_not_exist_12345.pdf")
    assert result["doctype"] == "unknown"
    assert result["error"] is not None


def test_valid_unrelated_pdf_classifies_unknown_without_error(tmp_path):
    """A genuinely valid, readable PDF whose content just isn't any of
    this project's known document types -- the realistic shape of
    someone uploading an unrelated document. This is 'unknown' because
    nothing matched, NOT because the file couldn't be read -- error
    must be None here, unlike the failure-mode tests above, so the
    dashboard can tell the two situations apart."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    path = tmp_path / "unrelated.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, "Just some random unrelated text, nothing ledger-shaped here.")
    c.save()

    result = cd.classify_document(str(path))
    assert result["doctype"] == "unknown"
    assert result["error"] is None
    assert "no recognized marker" in result["evidence"].lower()


# ---------- check_is_ledger_document() (pdf_to_csv_converter.py) ----------
# No test referenced this function at all before this phase (see this
# file's own module docstring) -- added alongside classify_document()'s
# own tests since classify_document() delegates ledger detection to it
# directly and the dashboard's upload flow relies on both.

@pytest.mark.parametrize("filename,is_ledger", [
    ("ledgers_redacted.pdf", True),
    ("Ledgers_Anonymised.pdf", True),
    ("trial_balance_redacted.pdf", False),
    ("gstr1_june_2026_redacted.pdf", False),
    ("Index_Anonymised.pdf", False),
])
def test_check_is_ledger_document_real_pdfs(filename, is_ledger):
    path = os.path.join(RAW_PDFS_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not present in this environment")
    assert check_is_ledger_document(path) is is_ledger


def test_check_is_ledger_document_never_raises_on_garbage(tmp_path):
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"not a pdf at all")
    assert check_is_ledger_document(str(path)) is False


def test_check_is_ledger_document_never_raises_on_missing_file():
    assert check_is_ledger_document("/tmp/this_path_does_not_exist_12345.pdf") is False


# ---------- why an unknown document could not be read (issue) ----------

def test_pdf_with_no_text_is_reported_as_scanned(tmp_path):
    """A scan/photo opens fine but has no text layer: it must say so (the
    fix is a different file), not just 'not recognised'."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    path = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(path))
    c.rect(50, 50, 300, 400, fill=1)      # drawn content, no text at all
    c.save()
    result = cd.classify_document(str(path))
    assert result["doctype"] == "unknown" and result["error"] is None
    assert result["issue"] == "scanned"
    assert "no selectable text" in result["evidence"]


def test_unopenable_file_is_reported_as_unreadable(tmp_path):
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"not a pdf at all")
    assert cd.classify_document(str(path))["issue"] == "unreadable"


def test_readable_but_unrelated_pdf_has_no_issue(tmp_path):
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    path = tmp_path / "menu.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, "Lunch menu")
    c.save()
    assert cd.classify_document(str(path))["issue"] is None
