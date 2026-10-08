"""
adapt_gstr_to_schema.py
-----------------------
Adapts real monthly GST return filings into the schemas used by
rule_gstr_reconciliation.py:
  - data/gstr1_summary.csv
  - data/gstr3b_summary.csv

This is a NEW, separate stream from the annual ITR/26AS work in
adapt_tax_docs_to_schema.py -- GSTR-1/GSTR-3B are monthly GST compliance
filings, unconnected to income tax. As of this writing they cover
FY2025-26 and FY2026-27 (16 months, April 2025-July 2026), which still
has no period overlap with general_ledger.csv's FY2024-25 ledger -- so
what's directly checkable today is still only GSTR-1 against GSTR-3B for
the SAME period (rule_gstr_reconciliation.py). A genuine ledger-vs-GSTR
cross-check is NOT implemented anywhere in this project yet; it would be
new code, written once a ledger period overlapping a filed GSTR period
exists to verify it against.

Real source documents (in data/raw_pdfs/), in either of two shapes --
both are read by adapt_gstr_docs() and merged into the same output CSVs:
  - gstr1_<month>_<year>_redacted.pdf / gstr3b_<month>_<year>_redacted.pdf
    -- one filing's PDF per month, the original shape this script was
    built for.
  - GSTR1_All_Months*.pdf / GSTR3B_All_Months*.pdf -- a single PDF
    covering many months back-to-back (one GSTR1/GSTR3B filing's pages
    per month, each new month starting on a page carrying "Tax period"
    (GSTR1) or "Year ... Period ..." (GSTR3B)). Each month's block is
    sliced out by page range and run through the exact same field-
    extraction regexes as the single-month path (_extract_gstr1_fields /
    _extract_gstr3b_fields) -- there is no separate parsing logic for
    this shape, only a different way of getting "the text of one month's
    filing" into the same extractor.

If the same period appears in both an All_Months PDF and a separate
single-month PDF (seen in practice: a one-off June 2026 filing PDF that
also appears inside the All_Months upload), the All_Months copy wins and
the duplicate is skipped with a printed note -- never silently merged or
double-counted.

Adding a future month's PDF (either shape) to data/raw_pdfs/ and
re-running this script is all that's needed to pick it up -- no code
change required for additional months.

Every figure extracted here is cross-checked within this module's own
functions against the source PDF's structure (see each extractor's
docstring), the same discipline adapt_tax_docs_to_schema.py follows for
the ITR/26AS data.
"""

import csv
import glob
import os
import re
import pdfplumber

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Removed 2026-10-04 (generalized-product architecture correction): see
# adapt_ledger_to_schema.py's own comment on this same change -- a
# hardcoded single-machine path fallback has no place in a product meant
# to run on any future user's setup, and the SCRIPT_DIR-relative
# computation below is already correct everywhere.
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
RAW_PDFS_DIR = os.path.join(DATA_DIR, "raw_pdfs")

GSTR1_GLOB = os.path.join(RAW_PDFS_DIR, "gstr1_*_redacted.pdf")
GSTR3B_GLOB = os.path.join(RAW_PDFS_DIR, "gstr3b_*_redacted.pdf")
GSTR1_ALL_MONTHS_GLOB = os.path.join(RAW_PDFS_DIR, "GSTR1_All_Months*.pdf")
GSTR3B_ALL_MONTHS_GLOB = os.path.join(RAW_PDFS_DIR, "GSTR3B_All_Months*.pdf")

OUTPUT_GSTR1 = os.path.join(DATA_DIR, "gstr1_summary.csv")
OUTPUT_GSTR3B = os.path.join(DATA_DIR, "gstr3b_summary.csv")

AMOUNT = r"([\d,]+\.\d{2})"       # a plain decimal amount, e.g. "8,367.35"
AMOUNT_OR_DASH = r"([\d,]+\.\d{2}|-)"  # same, or "-" where a cell doesn't apply

# A multi-month PDF's first page of each month's filing is identified by
# one of these; everything up to (not including) the next match is that
# month's block.
_GSTR1_MONTH_MARKER = re.compile(r"Tax period\s+\w+")
_GSTR3B_MONTH_MARKER = re.compile(r"Year\s+\d{4}-\d{2}")


def _parse_amount(token):
    """Indian comma-grouped amount ('4,09,90,353.95') or a literal '-'
    (meaning "not applicable" on these forms, e.g. an ITC head a tax type
    can't draw from) -> float. Python's float()/replace(',','') handles
    lakh/crore comma grouping fine regardless of where the commas fall."""
    token = token.strip()
    if token in ("-", ""):
        return 0.0
    return float(token.replace(",", ""))


def _extract_gstr1_fields(text, source_label):
    """Extracts the figures from a filed GSTR-1 that are directly
    comparable against the same period's GSTR-3B:
      - Table 7 (B2CS -- taxable supplies to unregistered persons): the
        taxable-supply table this filer populates every month.
      - Table 8 (non-GST outward supplies, i.e. fuel -- petrol/diesel are
        outside GST): must equal GSTR-3B's Table 3.1(e).
      - The return's own "Total Liability" line: the form's own
        already-computed sum across EVERY taxable-supply table (Table 7
        plus Table 4/B2B, 5, 6 -- whichever are populated that month),
        which is what "taxable_value"/"igst"/"cgst"/"sgst"/"cess" below
        are actually derived from (see "Which total to report" below) --
        NOT Table 7 alone.

    Why not just use Table 7: on 15 of the 16 real monthly filings seen so
    far, Table 7 (B2CS) is this filer's only nonzero taxable-supply table,
    so it happens to equal the full taxable total. One real month (June
    2025-26) also has a nonzero Table 4 (B2B) entry, and on that month
    Table 7 alone under-counts GSTR-3B's Table 3.1(a) by exactly the B2B
    amount (quantified directly against the PDF: Table 7 taxable value
    Rs.90,813.99 + B2B Rs.76,271.18 = Rs.167,085.17, matching GSTR-3B's
    3.1(a) to the rupee) -- i.e. relying on Table 7 alone is a real, not
    hypothetical, under-count the moment any other taxable-supply table
    has an entry.

    Which total to report: the "Total Liability" line's own VALUE column
    is not directly usable either -- it turns out to sum taxable supply
    AND Table 8's non-GST value together (confirmed: Total Liability
    value minus Table 8 non-GST value equals GSTR-3B's taxable value
    exactly, on every one of the 16 real months checked, including the
    B2B month). So the taxable total reported here is derived as
    (Total Liability value - Table 8 non-GST value) for the value column,
    and the Total Liability line's tax columns (IGST/CGST/SGST/Cess)
    directly -- those already exclude non-GST, since non-GST supplies
    carry no GST by definition. This was verified against all 16 real
    months before being adopted: identical to the old Table-7-only figure
    on the 15 months where they already agreed, and correct (matching
    GSTR-3B) on the one month where Table 7 alone was wrong. Table 7's own
    raw B2CS-only figures are kept too, under the b2cs_* fields below, for
    anyone who wants that narrower breakdown.

    `text` is the full text of one filing -- either a whole single-month
    PDF, or one month's page-range slice of a multi-month PDF; this
    function has no notion of which. `source_label` is only used in error
    messages, to say where to look.
    """
    fy_m = re.search(r"Financial year\s+(\d{4}-\d{2})", text)
    period_m = re.search(r"Tax period\s+(\w+)", text)
    arn_date_m = re.search(r"ARN date\s+(\d{2}/\d{2}/\d{4})", text)
    if not fy_m or not period_m:
        raise ValueError(f"Could not find Financial year/Tax period header in {source_label}")

    table7_m = re.search(
        r"Total\s+1\s+Net Value\s+" + AMOUNT + r"\s+" + AMOUNT + r"\s+" +
        AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT,
        text,
    )
    if not table7_m:
        raise ValueError(
            f"Could not find Table 7 (B2CS) summary line in {source_label} -- "
            f"if this filing has a different record count or layout in "
            f"Table 7, this regex needs updating, not a guessed value."
        )
    b2cs_value, b2cs_igst, b2cs_cgst, b2cs_sgst, b2cs_cess = (
        _parse_amount(g) for g in table7_m.groups()
    )

    non_gst_m = re.search(r"-\s*Non-GST\s+" + AMOUNT, text)
    if not non_gst_m:
        raise ValueError(f"Could not find Table 8 Non-GST line in {source_label}")
    non_gst_value = _parse_amount(non_gst_m.group(1))

    total_liability_m = re.search(
        r"Total Liability \(Outward supplies other than Reverse charge\)\s+" +
        AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT,
        text,
    )
    if not total_liability_m:
        raise ValueError(f"Could not find the 'Total Liability' line in {source_label}")
    tl_value, tl_igst, tl_cgst, tl_sgst, tl_cess = (
        _parse_amount(g) for g in total_liability_m.groups()
    )

    # The GSTR-3B-comparable taxable total: every taxable-supply table,
    # with Table 8's non-GST value backed out of the value column (see
    # docstring). Tax columns need no such adjustment -- non-GST supplies
    # carry no GST, so Total Liability's tax columns are already pure.
    taxable_value = tl_value - non_gst_value
    igst, cgst, sgst, cess = tl_igst, tl_cgst, tl_sgst, tl_cess

    return {
        "financial_year": fy_m.group(1),
        "period": period_m.group(1),
        "arn_date": arn_date_m.group(1) if arn_date_m else "",
        "taxable_value": f"{taxable_value:.2f}",
        "igst": f"{igst:.2f}",
        "cgst": f"{cgst:.2f}",
        "sgst": f"{sgst:.2f}",
        "cess": f"{cess:.2f}",
        "non_gst_value": f"{non_gst_value:.2f}",
        "b2cs_taxable_value": f"{b2cs_value:.2f}",
        "b2cs_igst": f"{b2cs_igst:.2f}",
        "b2cs_cgst": f"{b2cs_cgst:.2f}",
        "b2cs_sgst": f"{b2cs_sgst:.2f}",
        "b2cs_cess": f"{b2cs_cess:.2f}",
        "total_liability_value": f"{tl_value:.2f}",
        "total_liability_igst": f"{tl_igst:.2f}",
        "total_liability_cgst": f"{tl_cgst:.2f}",
        "total_liability_sgst": f"{tl_sgst:.2f}",
        "total_liability_cess": f"{tl_cess:.2f}",
    }


def _page_texts(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _segment_month_blocks(page_texts, marker_re):
    """Splits a multi-month PDF's already-extracted page texts into one
    joined-text block per month: each page matching marker_re starts a new
    month, running up to (not including) the next match, or to the end of
    the document for the last one. Raises if the marker never matches --
    this is a layout assumption, so a document that doesn't fit it should
    fail loudly, not silently produce zero or one giant "month"."""
    starts = [i for i, t in enumerate(page_texts) if marker_re.search(t)]
    if not starts:
        raise ValueError(
            f"No month boundaries found (pattern {marker_re.pattern!r} "
            f"matched zero pages) -- this PDF's layout doesn't match what "
            f"this segmentation expects; needs a human look, not a guess."
        )
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(page_texts)
        blocks.append("\n".join(page_texts[s:e]))
    return blocks


def extract_gstr1(pdf_path):
    """One single-month GSTR-1 PDF -> one field dict. See
    _extract_gstr1_fields for what's extracted."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _extract_gstr1_fields(text, pdf_path)


def extract_gstr1_all_months(pdf_path):
    """A multi-month GSTR-1 PDF (many filings' pages back-to-back, each
    month starting on a page carrying "Tax period <Month>") -> one field
    dict per month, via the exact same extractor as the single-month
    path -- only how the text is obtained differs."""
    blocks = _segment_month_blocks(_page_texts(pdf_path), _GSTR1_MONTH_MARKER)
    return [
        _extract_gstr1_fields(block, f"{pdf_path} (month block {i + 1}/{len(blocks)})")
        for i, block in enumerate(blocks)
    ]


# Separates two adjacent amount columns in Table 6.1. Normally just
# whitespace -- but pdfplumber occasionally glues a single stray capital
# letter onto an all-zero cell with no surrounding whitespace at all (seen
# in real data as "0.00L0.00", always on the all-zero "Integrated tax"
# row; confirmed in 2 of the 16 real monthly filings checked, and nowhere
# else in either form -- a PDF text-extraction quirk, not a real
# character). Tolerating one stray letter here, and only here, picks up
# those 2 months without loosening what counts as an amount anywhere.
_COL_SEP = r"(?:\s+|[A-Za-z])"


def _extract_payment_row(section_text, label, tax_head):
    """Parses one row of GSTR-3B Table 6.1 ('6.1 Payment of tax') under a
    given section (e.g. the "(A) Other than reverse charge" block), for
    one tax head ("Central", "State/UT" or "Integrated").

    Column order on the real form (10 data columns): Tax payable |
    Adjustment of negative liability of previous period | Net Tax Payable
    | [ITC used: Integrated, Central, State/UT, Cess] | Tax paid in cash |
    Interest paid in cash | Late fee paid in cash. Several ITC columns are
    legitimately "-" rather than "0.00" (a tax head can't draw ITC from
    itself, e.g. Central tax's own "State/UT" ITC column is always "-"),
    so AMOUNT_OR_DASH is used for all four ITC columns and "-" parses to
    0.0 via _parse_amount -- correct either way, since "-" and "0.00" both
    mean "no ITC of this type was used here".
    """
    pattern = re.compile(
        re.escape(label) + _COL_SEP + AMOUNT + _COL_SEP + AMOUNT_OR_DASH + _COL_SEP + AMOUNT +
        _COL_SEP + AMOUNT_OR_DASH + _COL_SEP + AMOUNT_OR_DASH + _COL_SEP + AMOUNT_OR_DASH +
        _COL_SEP + AMOUNT_OR_DASH + _COL_SEP + AMOUNT
    )
    m = pattern.search(section_text)
    if not m:
        raise ValueError(f"Could not find the '{label}' payment row for {tax_head}")
    payable, _adjustment, net_payable, itc_igst, itc_cgst, itc_sgst, itc_cess, cash = (
        _parse_amount(g) for g in m.groups()
    )
    paid_total = itc_igst + itc_cgst + itc_sgst + itc_cess + cash
    return payable, net_payable, paid_total


def _extract_gstr3b_fields(text, source_label):
    """Extracts the figures from a filed GSTR-3B that are directly
    comparable against the same period's GSTR-1 (Table 3.1(a)/(e)), plus
    this return's own internal payment-completeness figures (Table 6.1):
    tax payable vs. tax actually paid (ITC + cash), per tax head.

    `text` is the full text of one filing -- either a whole single-month
    PDF, or one month's page-range slice of a multi-month PDF; this
    function has no notion of which. `source_label` is only used in error
    messages, to say where to look.
    """
    year_m = re.search(r"Year\s+(\d{4}-\d{2})", text)
    period_m = re.search(r"Period\s+(\w+)", text)
    arn_date_m = re.search(r"Date of ARN\s+(\d{2}/\d{2}/\d{4})", text)
    if not year_m or not period_m:
        raise ValueError(f"Could not find Year/Period header in {source_label}")

    taxable_m = re.search(
        r"Outward taxable supplies \(other than zero rated, nil rated and\s*" +
        AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT + r"\s+" + AMOUNT,
        text,
    )
    if not taxable_m:
        raise ValueError(f"Could not find Table 3.1(a) line in {source_label}")
    taxable_value, igst, cgst, sgst, cess = (_parse_amount(g) for g in taxable_m.groups())

    non_gst_m = re.search(r"Non-GST outward supplies\s+" + AMOUNT, text)
    if not non_gst_m:
        raise ValueError(f"Could not find Table 3.1(e) Non-GST line in {source_label}")
    non_gst_value = _parse_amount(non_gst_m.group(1))

    section_m = re.search(
        r"\(A\) Other than reverse charge(.*?)\(B\) Reverse charge", text, re.DOTALL
    )
    if not section_m:
        raise ValueError(f"Could not find the Table 6.1 '(A) Other than reverse charge' block in {source_label}")
    section = section_m.group(1)

    cgst_payable, cgst_net_payable, cgst_paid_total = _extract_payment_row(section, "Central", "CGST")
    sgst_payable, sgst_net_payable, sgst_paid_total = _extract_payment_row(section, "State/UT", "SGST")
    igst_payable, igst_net_payable, igst_paid_total = _extract_payment_row(section, "Integrated", "IGST")

    return {
        "financial_year": year_m.group(1),
        "period": period_m.group(1),
        "arn_date": arn_date_m.group(1) if arn_date_m else "",
        "taxable_value": f"{taxable_value:.2f}",
        "igst": f"{igst:.2f}",
        "cgst": f"{cgst:.2f}",
        "sgst": f"{sgst:.2f}",
        "cess": f"{cess:.2f}",
        "non_gst_value": f"{non_gst_value:.2f}",
        "cgst_payable": f"{cgst_net_payable:.2f}",
        "cgst_paid_total": f"{cgst_paid_total:.2f}",
        "sgst_payable": f"{sgst_net_payable:.2f}",
        "sgst_paid_total": f"{sgst_paid_total:.2f}",
        "igst_payable": f"{igst_net_payable:.2f}",
        "igst_paid_total": f"{igst_paid_total:.2f}",
    }


def extract_gstr3b(pdf_path):
    """One single-month GSTR-3B PDF -> one field dict. See
    _extract_gstr3b_fields for what's extracted."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _extract_gstr3b_fields(text, pdf_path)


def extract_gstr3b_all_months(pdf_path):
    """A multi-month GSTR-3B PDF (many filings' pages back-to-back, each
    month starting on a page carrying "Year <YYYY-YY>") -> one field dict
    per month, via the exact same extractor as the single-month path --
    only how the text is obtained differs."""
    blocks = _segment_month_blocks(_page_texts(pdf_path), _GSTR3B_MONTH_MARKER)
    return [
        _extract_gstr3b_fields(block, f"{pdf_path} (month block {i + 1}/{len(blocks)})")
        for i, block in enumerate(blocks)
    ]


def _dedup_by_period(rows, label):
    """Keeps the first row seen for each (financial_year, period); any
    later row for a period already seen is a duplicate filing (observed
    in practice: a one-off single-month PDF for a period that's also
    inside an All_Months upload) and is dropped with a printed note --
    never silently merged, and never left to double-count a period."""
    seen = {}
    deduped = []
    for row in rows:
        key = (row["financial_year"], row["period"])
        if key in seen:
            print(f"  (skipping duplicate {label} filing for {key[1]} {key[0]} -- "
                  f"already have it from an earlier source)")
            continue
        seen[key] = row
        deduped.append(row)
    return deduped


def _extract_any_shape(path, all_months_fn, single_fn):
    """One GSTR PDF of either shape -> (list of period rows, is_multi_month).
    Tries the multi-month segmentation first (a single-month PDF simply
    yields one block); a document with no month marker at all falls back to
    the single-filing extractor, whose own regexes then decide whether the
    document is usable."""
    try:
        rows = all_months_fn(path)
    except ValueError:
        return [single_fn(path)], False
    return rows, len(rows) > 1


_EXTRACT_CACHE_PATH = os.path.join(PROJECT_DIR, "output", "gstr_extract_cache.json")


def _load_extract_cache(cache_path):
    """Per-file extraction results from earlier runs, keyed by label +
    filename and fingerprinted by (mtime_ns, size). Parsing a whole-year
    GSTR bundle is the slowest part of "Re-run Analysis" (~15s), and an
    unchanged file always yields the same rows -- so an unchanged file is
    never re-parsed. Missing/corrupt cache -> empty (parse fresh)."""
    import json
    try:
        with open(cache_path or _EXTRACT_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == 1 and isinstance(data.get("entries"), dict):
            return data["entries"]
    except Exception:
        pass
    return {}


def _save_extract_cache(cache_path, entries):
    import json
    path = cache_path or _EXTRACT_CACHE_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": entries}, f)
        os.replace(tmp, path)
    except Exception:
        pass  # a convenience cache -- never load-bearing


def _collect_rows(paths, all_months_fn, single_fn, label, cache, warnings):
    """Rows from every file in `paths` (multi-month files first), using and
    updating the per-file extraction `cache`; an unreadable file is skipped
    with a note in `warnings`. Returns (rows, cache_changed)."""
    dirty = False
    multi, single = [], []
    for p in paths:
        key = f"{label}|{os.path.basename(p)}"
        try:
            st = os.stat(p)
            fingerprint = [st.st_mtime_ns, st.st_size]
        except OSError:
            fingerprint = None
        hit = cache.get(key)
        if hit is not None and fingerprint is not None and hit.get("fingerprint") == fingerprint:
            rows, is_multi = hit["rows"], hit["is_multi"]
        else:
            try:
                rows, is_multi = _extract_any_shape(p, all_months_fn, single_fn)
            except Exception as e:
                warnings.append(f"{os.path.basename(p)}: could not read this {label} ({e})")
                continue
            if fingerprint is not None:
                cache[key] = {"fingerprint": fingerprint, "rows": rows, "is_multi": is_multi}
                dirty = True
        (multi if is_multi else single).extend(rows)
    return multi + single, dirty


def extract_gstr1_rows(gstr1_paths, cache_path=None):
    """GSTR-1 period rows from exactly the given PDFs -- nothing is written
    to data/gstr1_summary.csv. Same extractors, cache and dedup as
    adapt_gstr_documents(); used to check a ledger against the returns that
    belong with it (the dashboard passes only the person's own uploads for
    an uploaded ledger, never the built-in sample returns).

    Returns (rows, warnings); `rows` is empty when `gstr1_paths` is empty or
    nothing in it could be read (the reasons are in `warnings`)."""
    warnings = []
    if not gstr1_paths:
        return [], warnings
    cache = _load_extract_cache(cache_path)
    rows, dirty = _collect_rows(gstr1_paths, extract_gstr1_all_months, extract_gstr1,
                                "GSTR-1", cache, warnings)
    if dirty:
        _save_extract_cache(cache_path, cache)
    return _dedup_by_period(rows, "GSTR-1"), warnings


def extract_gstr3b_rows(gstr3b_paths, cache_path=None):
    """GSTR-3B counterpart of extract_gstr1_rows(): rows from exactly the
    given PDFs, nothing written. Returns (rows, warnings)."""
    warnings = []
    if not gstr3b_paths:
        return [], warnings
    cache = _load_extract_cache(cache_path)
    rows, dirty = _collect_rows(gstr3b_paths, extract_gstr3b_all_months, extract_gstr3b,
                                "GSTR-3B", cache, warnings)
    if dirty:
        _save_extract_cache(cache_path, cache)
    return _dedup_by_period(rows, "GSTR-3B"), warnings


def adapt_gstr_documents(gstr1_paths, gstr3b_paths,
                         out_gstr1_path=OUTPUT_GSTR1, out_gstr3b_path=OUTPUT_GSTR3B,
                         cache_path=None):
    """Content-based sibling of adapt_gstr_docs(): the caller supplies the
    exact PDF lists (the dashboard passes every document whose CONTENT was
    classified as GSTR-1 / GSTR-3B, whatever its filename) instead of this
    module's filename globs. Same extractors, same dedup, same output CSVs.

    A file that cannot be read (corrupt, or a layout the extractors have
    never seen) is skipped and reported in the returned `warnings` rather
    than aborting every other month -- the one decision here that differs
    from adapt_gstr_docs(), because with arbitrary uploads one bad file must
    not block the rest. Multi-month files are read first, so when a period
    is in both an all-months file and a single-month file the all-months
    copy wins, exactly as in adapt_gstr_docs().

    Returns (gstr1_rows, gstr3b_rows, warnings). Raises FileNotFoundError
    only when there is no usable GSTR-1 or no usable GSTR-3B at all (the
    dashboard reports that as "skipped", not as a failure)."""
    warnings = []
    cache = _load_extract_cache(cache_path)

    gstr1_rows, dirty1 = _collect_rows(gstr1_paths, extract_gstr1_all_months, extract_gstr1,
                                       "GSTR-1", cache, warnings)
    gstr3b_rows, dirty3 = _collect_rows(gstr3b_paths, extract_gstr3b_all_months, extract_gstr3b,
                                        "GSTR-3B", cache, warnings)
    if dirty1 or dirty3:
        _save_extract_cache(cache_path, cache)

    if not gstr1_rows:
        raise FileNotFoundError("No readable GSTR-1 documents found.")
    if not gstr3b_rows:
        raise FileNotFoundError("No readable GSTR-3B documents found.")

    gstr1_rows = _dedup_by_period(gstr1_rows, "GSTR-1")
    gstr3b_rows = _dedup_by_period(gstr3b_rows, "GSTR-3B")

    for rows, path in ((gstr1_rows, out_gstr1_path), (gstr3b_rows, out_gstr3b_path)):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    print(f"Adapted {len(gstr1_rows)} GSTR-1 period row(s) -> {out_gstr1_path}")
    print(f"Adapted {len(gstr3b_rows)} GSTR-3B period row(s) -> {out_gstr3b_path}")
    return gstr1_rows, gstr3b_rows, warnings


def adapt_gstr_docs(out_gstr1_path=OUTPUT_GSTR1, out_gstr3b_path=OUTPUT_GSTR3B):
    # All_Months sources first so that, when a period appears in both an
    # All_Months upload and a separate single-month PDF, the All_Months
    # copy is the one _dedup_by_period keeps (first-seen wins).
    gstr1_rows = []
    for p in sorted(glob.glob(GSTR1_ALL_MONTHS_GLOB)):
        gstr1_rows.extend(extract_gstr1_all_months(p))
    for p in sorted(glob.glob(GSTR1_GLOB)):
        gstr1_rows.append(extract_gstr1(p))

    gstr3b_rows = []
    for p in sorted(glob.glob(GSTR3B_ALL_MONTHS_GLOB)):
        gstr3b_rows.extend(extract_gstr3b_all_months(p))
    for p in sorted(glob.glob(GSTR3B_GLOB)):
        gstr3b_rows.append(extract_gstr3b(p))

    if not gstr1_rows:
        raise FileNotFoundError(
            f"No GSTR-1 PDFs found matching {GSTR1_GLOB} or {GSTR1_ALL_MONTHS_GLOB}"
        )
    if not gstr3b_rows:
        raise FileNotFoundError(
            f"No GSTR-3B PDFs found matching {GSTR3B_GLOB} or {GSTR3B_ALL_MONTHS_GLOB}"
        )

    gstr1_rows = _dedup_by_period(gstr1_rows, "GSTR-1")
    gstr3b_rows = _dedup_by_period(gstr3b_rows, "GSTR-3B")

    gstr1_fieldnames = list(gstr1_rows[0].keys())
    with open(out_gstr1_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=gstr1_fieldnames)
        writer.writeheader()
        writer.writerows(gstr1_rows)
    print(f"Adapted {len(gstr1_rows)} GSTR-1 period row(s) -> {out_gstr1_path}")

    gstr3b_fieldnames = list(gstr3b_rows[0].keys())
    with open(out_gstr3b_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=gstr3b_fieldnames)
        writer.writeheader()
        writer.writerows(gstr3b_rows)
    print(f"Adapted {len(gstr3b_rows)} GSTR-3B period row(s) -> {out_gstr3b_path}")

    return gstr1_rows, gstr3b_rows


if __name__ == "__main__":
    adapt_gstr_docs()
