"""
Regression suite for scripts/adapt_tax_docs_to_schema.py, written after the
2026-10-02 discovery that the previously-committed data/form26as.csv and
data/itr_summary.csv were real, but for a DIFFERENT taxpayer than this
project's own ledger: a property-sale TDS entry in the filing identifies
an individual, so the documents belonged to a different taxpayer and had
ended up mixed into this project's data/ folder alongside the petrol pump
firm's. That data's source (data/26AS__table_group_2.csv, a leftover
table-extraction fragment that hadn't synced into every copy of this
project) was set aside in a local backup folder kept out of the
repository, not deleted -- it just doesn't belong compared against
general_ledger.csv, which is the firm's own books.

This suite pins the REAL figures, extracted directly from the three real
PDFs (annual_tax_statement_redacted.pdf, income_computation_redacted.pdf,
income_tax_acknowledgement_redacted.pdf), cross-checked against each other
by hand before being accepted (see adapt_tax_docs_to_schema.py's module
docstring and BASELINE_CHECKPOINT/the 2026-10-02 findings report for the
full cross-check):

  - Real Form 26AS (PART-I): exactly 1 deductor, HINDUSTAN PETROLEUM
    CORPORATION LIMITED, TAN MUMH09973F, Total TDS Deposited Rs.32,018.00.
  - Real COI's own TDS schedule: TWO rows under the same TAN (Rs.22,018
    under section 194C + Rs.10,000 under section 194R) summing to
    Rs.32,018.00 -- matching the 26AS figure exactly, and matching the
    COI's own "Less: TDS (-) 32018" line in its tax computation.
  - Real ITR acknowledgement: Total Income Rs.43,73,040, Taxes Paid
    Rs.13,94,808 -- the latter independently re-derivable from the COI's
    own detailed computation (TDS 32,018 + Advance Tax 10,50,000 +
    Self-assessment tax 3,12,790 = 13,94,808, matching to the rupee).

Also pins the specific bug fix in extract_coi_claimed(): it used to
silently overwrite same-TAN rows (claimed[tan] = {...} unconditionally)
instead of accumulating them, so the two real schedule rows for Hindustan
Petroleum collapsed into just the second one (Rs.10,000), never matching
either the COI's own total or the real Form 26AS figure.
"""
import os

import pytest

import adapt_tax_docs_to_schema as adapt


@pytest.fixture(scope="module")
def real_26as_deductors():
    if not os.path.exists(adapt.INPUT_26AS):
        pytest.skip(f"Annual Tax Statement not present at {adapt.INPUT_26AS}")
    return adapt.extract_26as_deductors(adapt.INPUT_26AS)


@pytest.fixture(scope="module")
def real_coi_claimed():
    if not os.path.exists(adapt.INPUT_COI):
        pytest.skip(f"Computation of Income PDF not present at {adapt.INPUT_COI}")
    return adapt.extract_coi_claimed(adapt.INPUT_COI)


@pytest.fixture(scope="module")
def real_ack_summary():
    if not os.path.exists(adapt.INPUT_ACK):
        pytest.skip(f"ITR acknowledgement PDF not present at {adapt.INPUT_ACK}")
    return adapt.extract_itr_acknowledgement_summary(adapt.INPUT_ACK)


def test_period_extracted_from_real_document_not_hardcoded():
    """The old script hardcoded financial_year='2025-26'/assessment_year=
    '2026-27' as Python literals -- a full year ahead of every real
    document. This pins the real, extracted period instead."""
    financial_year, assessment_year = adapt.extract_period(adapt.INPUT_26AS)
    assert financial_year == "2024-25"
    assert assessment_year == "2025-26"


def test_26as_has_exactly_one_real_deductor(real_26as_deductors):
    assert len(real_26as_deductors) == 1
    deductor = real_26as_deductors["MUMH09973F"]
    assert deductor["deductor_name"] == "HINDUSTAN PETROLEUM CORPORATION LIMITED"
    assert deductor["tds_reported_by_deductor"] == pytest.approx(32018.00, abs=0.01)


def test_coi_claimed_accumulates_same_tan_rows_not_overwrites(real_coi_claimed):
    """Regression test for the exact bug this investigation found: without
    accumulation, this would be 10000.00 (only the second schedule row),
    not 32018.00 (both rows summed)."""
    assert len(real_coi_claimed) == 1
    claimed = real_coi_claimed["MUMH09973F"]
    assert claimed["tds_claimed_in_itr_share"] == pytest.approx(32018.00, abs=0.01)


def test_coi_claimed_matches_26as_reported(real_26as_deductors, real_coi_claimed):
    """The real cross-check that makes 'zero mismatch' genuine rather than
    assumed: the amount the firm claimed in its COI and the amount the
    deductor reported depositing are the same figure, independently."""
    reported = real_26as_deductors["MUMH09973F"]["tds_reported_by_deductor"]
    claimed = real_coi_claimed["MUMH09973F"]["tds_claimed_in_itr_share"]
    assert reported == pytest.approx(claimed, abs=0.01)


def test_ack_summary_matches_coi_cross_check(real_ack_summary):
    """Taxes Paid per the acknowledgement (13,94,808) independently
    reproduces from the COI's own detailed computation: TDS (32,018) +
    Advance Tax across 4 challans (15,00,00 + 3,00,000 + 3,00,000 +
    3,00,000 = 10,50,000) + Self-assessment tax across 3 further challans
    (3,760 + 3,08,360 + 670 = 3,12,790). 32018 + 1050000 + 312790 =
    1394808 -- matches to the rupee."""
    assert real_ack_summary["total_income"] == pytest.approx(4373040.00, abs=0.01)
    tds = 32018.00
    advance_tax = 150000 + 300000 + 300000 + 300000
    self_assessment_tax = 3760 + 308360 + 670
    assert real_ack_summary["taxes_paid"] == pytest.approx(
        tds + advance_tax + self_assessment_tax, abs=0.01
    )


def test_adapt_tax_docs_is_reproducible(tmp_path):
    """Running the real adapter against the real PDFs must reproduce
    exactly what's committed in data/form26as.csv and data/itr_summary.csv
    -- the entire point of this fix was making that true again."""
    out_26as = tmp_path / "form26as.csv"
    out_itr = tmp_path / "itr_summary.csv"
    form26as_rows, itr_row = adapt.adapt_tax_docs(
        out_26as_path=str(out_26as), out_itr_path=str(out_itr)
    )

    assert form26as_rows == [{
        "deductor_name": "HINDUSTAN PETROLEUM CORPORATION LIMITED",
        "pan": "",
        "financial_year": "2024-25",
        "tds_reported_by_deductor": "32018.00",
        "tds_claimed_in_itr_share": "32018.00",
        "tan": "MUMH09973F",
    }]
    assert itr_row == {
        "assessment_year": "2025-26",
        "financial_year": "2024-25",
        "pan": "",
        "gross_total_income": "4373040.00",
        "total_deductions": "0.00",
        "taxable_income": "4373040.00",
        "tds_claimed": "32018.00",
        "tax_paid": "1394808.00",
    }

    with open(adapt.OUTPUT_26AS, encoding="utf-8") as f:
        committed_26as = f.read()
    with open(out_26as, encoding="utf-8") as f:
        fresh_26as = f.read()
    assert committed_26as == fresh_26as, (
        "data/form26as.csv no longer matches a fresh run of the adapter "
        "against the real PDFs -- the exact drift this suite exists to catch"
    )

    with open(adapt.OUTPUT_ITR, encoding="utf-8") as f:
        committed_itr = f.read()
    with open(out_itr, encoding="utf-8") as f:
        fresh_itr = f.read()
    assert committed_itr == fresh_itr, (
        "data/itr_summary.csv no longer matches a fresh run of the adapter "
        "against the real PDFs -- the exact drift this suite exists to catch"
    )
