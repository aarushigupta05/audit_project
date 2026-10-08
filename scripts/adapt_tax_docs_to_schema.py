"""
adapt_tax_docs_to_schema.py
---------------------------
Adapts real tax documents into the schemas expected by combined_report.py:
  - data/form26as.csv
  - data/itr_summary.csv

Real source documents (all in data/raw_pdfs/):
  - annual_tax_statement_redacted.pdf  -- the real Form 26AS ("Annual Tax
    Statement"), PART-I deductor-wise TDS table.
  - income_computation_redacted.pdf    -- the Computation of Income (COI),
    including its own TDS-claimed schedule (TAN/Name/Income/TDS/Sec/Head).
  - income_tax_acknowledgement_redacted.pdf -- the filed ITR-V acknowledgement,
    carrying the headline Total Income / Taxes Paid figures.

HISTORY (2026-10-02): this script previously pointed at
data/26AS__table_group_2.csv (a leftover fragment from an early PDF
table-extraction pass -- never synced into every copy of this project,
which is how it went unnoticed) and a misnamed
data/raw_pdfs/Computation_of_Income.pdf. Its output was 4 deductors
totalling Rs.1,24,904 TDS, financial_year "2025-26"/AY "2026-27" -- real
data, but traced to a DIFFERENT taxpayer's filing (an individual, shown by
a property-sale TDS entry in it), not the petrol pump firm's. Those
documents had ended up in this project's data/ folder alongside the
firm's -- the firm (general_ledger.csv's subject, FY2024-25) and that
individual (FY2025-26) were never the same filing. That data was set aside
in a local backup folder (kept out of the repository) rather than
deleted, but it has no place being compared against this project's own
ledger, so this script now
extracts the FIRM's own ITR/26AS instead, from the three real PDFs above,
verified to cross-check against each other and against general_ledger.csv's
own financial year (see the extraction functions' docstrings for the exact
cross-checks).

TAN join logic: deductor records from the real Form 26AS and the COI's own
TDS schedule are joined on normalized TAN. If a TAN is present in one
source but not the other, it is retained with the missing figure set to
0.00 so Rule 4 can detect the discrepancy without throwing a float error.

FINAL SCOPE (v1 finalization, 2026-10-04; corrected 2026-10-06): the AIS
(Annual Information Statement) was an original target document for this
project alongside Form 26AS, but no AIS for THIS FIRM has ever been
supplied. (An earlier version of this note said data/raw_pdfs/ had never
contained an AIS PDF at all -- inaccurate: data/raw_pdfs/AIS.pdf exists,
but it is an individual's FY2025-26 statement from the same batch of
unrelated-person documents described in the HISTORY note above, and it
does not involve the firm's deductor, so it is not usable against the
firm's ledger.) This is a deliberate, final scope decision, not an open
TODO: ITR vs. Form 26AS (Rule 4) already provides the equivalent
direct TDS cross-check this project needs, and building an AIS adapter by
guessing its layout without a real sample to verify against would violate
this project's "never guess" discipline. Revisit only if a real AIS PDF
for this firm becomes available.
"""

import csv
import os
import re
import sys
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

INPUT_26AS = os.path.join(RAW_PDFS_DIR, "annual_tax_statement_redacted.pdf")
INPUT_COI = os.path.join(RAW_PDFS_DIR, "income_computation_redacted.pdf")
INPUT_ACK = os.path.join(RAW_PDFS_DIR, "income_tax_acknowledgement_redacted.pdf")

OUTPUT_26AS = os.path.join(DATA_DIR, "form26as.csv")
OUTPUT_ITR = os.path.join(DATA_DIR, "itr_summary.csv")

TAN_PATTERN = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")


def extract_period(pdf_path=INPUT_26AS):
    """Extracts (financial_year, assessment_year) from the Annual Tax
    Statement's own header line, e.g. 'Financial Year 2024-25 Assessment
    Year 2025-26', instead of hardcoding a year label -- the previous
    version's hardcoded "2025-26"/"2026-27" labels were a full year ahead
    of every real document and never caught."""
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    m = re.search(r"Financial Year (\d{4}-\d{2}).*?Assessment Year (\d{4}-\d{2})", text)
    if not m:
        raise ValueError(f"Could not find Financial Year/Assessment Year header in {pdf_path}")
    return m.group(1), m.group(2)


def extract_26as_deductors(pdf_path=INPUT_26AS):
    """Parses PART-I of the real Annual Tax Statement (Form 26AS) directly
    from the PDF: 'Sr.No. Name of Deductor TAN ... Total TDS Deposited'.

    Replaces the old extract_26as_deductors(csv_path) that read a
    data/26AS__table_group_2.csv which does not exist anywhere in this
    project -- there was never a script that produced it, so the old
    function could not have run against real data as committed.
    """
    row_pattern = re.compile(
        r"^(\d+)\s+(.+?)\s+([A-Z]{4}\d{5}[A-Z])\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
    )
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Annual Tax Statement not found at {pdf_path}")

    deductors = {}
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""

    in_part_i = False
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("PART-I"):
            in_part_i = True
            continue
        if line.startswith("PART-II"):
            break
        if not in_part_i:
            continue
        m = row_pattern.match(line)
        if m:
            _, name, tan, _amount_paid, _tax_deducted, tds_deposited = m.groups()
            deductors[tan] = {
                "tan": tan,
                "deductor_name": name.strip(),
                "tds_reported_by_deductor": float(tds_deposited.replace(",", "")),
            }
    return deductors


def extract_coi_claimed(pdf_path=INPUT_COI):
    """Parses the COI's own TDS-claimed schedule (page 2): rows shaped
    'TAN  Name  Income  TDS  Section  Head'.

    FIX (2026-10-02): the real schedule for this filer has TWO rows under
    the SAME TAN (one per section claimed under, e.g. 194C and 194R) --
    'MUMH09973F HINDUSTAN PETROLEUM CORPORAT 1100395 22018 194C BP' and
    'MUMH09973F HINDUSTAN PETROLEUM CORPORAT 100000 10000 194R BP'. The
    previous version assigned `claimed[tan] = {...}` unconditionally per
    line, so the second row silently overwrote the first -- losing
    Rs.22,018 of the Rs.32,018 actually claimed and leaving only
    Rs.10,000. This is why the COI's own 'Less: TDS (-) 32018' total, and
    the real Form 26AS's Rs.32,018 TDS Deposited for this exact deductor,
    never matched the old extraction. Fixed to ACCUMULATE per TAN.
    """
    tan_line_pattern = re.compile(
        r"^([A-Z]{4}\d{5}[A-Z])\s+(.+?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+\S+\s+\S+\s*$"
    )
    claimed = {}
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Computation of Income PDF not found at {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        # Page 2 contains the TDS breakdown for this filer.
        p2 = pdf.pages[1]
        text = p2.extract_text() or ""
        for line in text.split("\n"):
            line = line.strip()
            m = tan_line_pattern.match(line)
            if m:
                tan, name, _income_str, tds_str = m.groups()
                tan = tan.strip().upper()
                tds_amount = float(tds_str.strip())
                if tan in claimed:
                    claimed[tan]["tds_claimed_in_itr_share"] += tds_amount
                else:
                    claimed[tan] = {
                        "tan": tan,
                        "deductor_name": name.strip(),
                        "tds_claimed_in_itr_share": tds_amount,
                    }
    return claimed


def _parse_indian_amount(amount_str):
    """Parses an Indian comma-grouped amount string (e.g. '43,73,040') to a
    float, handling the lakh/crore grouping (which plain float()/replace(',','')
    handles fine since Python doesn't care where the commas fall)."""
    return float(amount_str.replace(",", ""))


def extract_itr_acknowledgement_summary(pdf_path=INPUT_ACK):
    """Extracts the headline Total Income and Taxes Paid figures directly
    from the filed ITR-V acknowledgement, instead of hand-transcribing them
    into hardcoded literals. Cross-checked (see adapt_tax_docs docstring)
    against the COI's own detailed tax computation and per-challan payment
    schedule -- both independently sum to the same Taxes Paid figure this
    function extracts."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"ITR acknowledgement not found at {pdf_path}")

    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""

    total_income_m = re.search(r"Total Income\s+1A\s+([\d,]+)", text)
    taxes_paid_m = re.search(r"Taxes Paid\s+7\s+([\d,]+)", text)
    if not total_income_m or not taxes_paid_m:
        raise ValueError(f"Could not find Total Income / Taxes Paid lines in {pdf_path}")

    return {
        "total_income": _parse_indian_amount(total_income_m.group(1)),
        "taxes_paid": _parse_indian_amount(taxes_paid_m.group(1)),
    }


def adapt_tax_docs(as26_path=INPUT_26AS, coi_path=INPUT_COI, ack_path=INPUT_ACK,
                   out_26as_path=OUTPUT_26AS, out_itr_path=OUTPUT_ITR):
    financial_year, assessment_year = extract_period(as26_path)
    as26_deductors = extract_26as_deductors(as26_path)
    coi_claimed = extract_coi_claimed(coi_path)
    ack_summary = extract_itr_acknowledgement_summary(ack_path)

    all_tans = sorted(list(set(as26_deductors.keys()) | set(coi_claimed.keys())))

    # The assessee's own PAN is redacted in every real source document
    # available in this project -- left empty rather than guessed. (The
    # old hardcoded PAN traced to no real document either; see the module
    # docstring.)
    pan = ""

    # Form 26AS output
    form26as_rows = []
    for tan in all_tans:
        as26_item = as26_deductors.get(tan, {})
        coi_item = coi_claimed.get(tan, {})

        name = as26_item.get("deductor_name") or coi_item.get("deductor_name") or tan
        reported = as26_item.get("tds_reported_by_deductor", 0.0)
        claimed_val = coi_item.get("tds_claimed_in_itr_share", 0.0)

        form26as_rows.append({
            "deductor_name": name,
            "pan": pan,
            "financial_year": financial_year,
            "tds_reported_by_deductor": f"{reported:.2f}",
            "tds_claimed_in_itr_share": f"{claimed_val:.2f}",
            "tan": tan,
        })

    with open(out_26as_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["deductor_name", "pan", "financial_year",
                      "tds_reported_by_deductor", "tds_claimed_in_itr_share", "tan"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(form26as_rows)

    print(f"Adapted {len(form26as_rows)} deductor rows -> {out_26as_path}")

    # ITR Summary output. This filer's COI has no Chapter VI-A deduction
    # schedule at all (confirmed by reading all 5 pages of
    # income_computation_redacted.pdf) -- Total Income is reached directly
    # from Business Income + Income from Other Sources, with nothing
    # deducted afterward. So gross_total_income == taxable_income here,
    # and total_deductions is genuinely 0.00, not an assumption.
    total_tds_claimed = sum(d["tds_claimed_in_itr_share"] for d in coi_claimed.values())
    itr_row = {
        "assessment_year": assessment_year,
        "financial_year": financial_year,
        "pan": pan,
        "gross_total_income": f"{ack_summary['total_income']:.2f}",
        "total_deductions": "0.00",
        "taxable_income": f"{ack_summary['total_income']:.2f}",
        "tds_claimed": f"{total_tds_claimed:.2f}",
        "tax_paid": f"{ack_summary['taxes_paid']:.2f}",
    }

    with open(out_itr_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(itr_row.keys()))
        writer.writeheader()
        writer.writerow(itr_row)

    print(f"Adapted ITR summary -> {out_itr_path}")
    return form26as_rows, itr_row


if __name__ == "__main__":
    adapt_tax_docs()
