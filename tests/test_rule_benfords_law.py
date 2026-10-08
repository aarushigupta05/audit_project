import pytest
from rule_benfords_law import (
    get_first_digit,
    check_benfords_law,
    compute_mad,
    classify_mad_conformity,
)


@pytest.mark.parametrize("amount,expected", [
    (12345, 1),
    (987.65, 9),
    (-500, 5),       # sign must not affect leading digit
    (0.042, 4),      # leading zeros / decimals must be skipped
    (0, None),       # zero has no meaningful leading digit
])
def test_get_first_digit(amount, expected):
    assert get_first_digit(amount) == expected


def test_perfectly_benford_distribution_has_low_chi_square():
    """A distribution built to match Benford's expected percentages
    exactly should NOT deviate (sanity check that the chi-square math
    itself is correct, independent of any real data)."""
    rows = []
    # Build roughly the expected proportions out of 10,000 rows per digit.
    expected = {1: 3010, 2: 1760, 3: 1250, 4: 970, 5: 790,
                6: 670, 7: 580, 8: 510, 9: 460}
    for digit, count in expected.items():
        rows.extend({"amount": str(digit * 100)} for _ in range(count))
    chi_square, deviates = check_benfords_law(rows)
    assert deviates is False
    assert chi_square < 15.5


def test_uniform_leading_digit_deviates_strongly():
    """If every amount starts with the same digit, that's about as
    unnatural as data gets -- chi-square should blow well past the
    threshold."""
    rows = [{"amount": "500.00"} for _ in range(500)]  # all leading digit 5
    chi_square, deviates = check_benfords_law(rows)
    assert deviates is True
    assert chi_square > 15.5


# ---------- MAD (added 2026-10-02, alongside chi-square above) ----------

def test_perfectly_benford_distribution_has_low_mad():
    """Same construction as test_perfectly_benford_distribution_has_low_chi_square
    above -- a distribution built to match Benford's expected percentages
    exactly should land in "Close conformity", the best MAD band."""
    rows = []
    expected = {1: 3010, 2: 1760, 3: 1250, 4: 970, 5: 790,
                6: 670, 7: 580, 8: 510, 9: 460}
    for digit, count in expected.items():
        rows.extend({"amount": str(digit * 100)} for _ in range(count))
    mad = compute_mad(rows)
    assert mad < 0.006
    assert classify_mad_conformity(mad) == "Close conformity"


def test_uniform_leading_digit_has_high_mad():
    """Same construction as test_uniform_leading_digit_deviates_strongly
    above -- all-one-digit data is as far from Benford as it gets, so MAD
    should land in "Nonconformity", agreeing with chi-square's "deviates"
    here (unlike on the real dataset, where the two disagree -- see
    test_benford_mad_pinned in test_combined_report_regression.py)."""
    rows = [{"amount": "500.00"} for _ in range(500)]
    mad = compute_mad(rows)
    assert mad >= 0.015
    assert classify_mad_conformity(mad) == "Nonconformity"


@pytest.mark.parametrize("mad,expected_band", [
    (0.000, "Close conformity"),
    (0.0059, "Close conformity"),
    (0.006, "Acceptable conformity"),   # boundary, inclusive upward per Nigrini
    (0.0119, "Acceptable conformity"),
    (0.012, "Marginally acceptable"),   # boundary
    (0.0149, "Marginally acceptable"),
    (0.015, "Nonconformity"),           # boundary
    (0.05, "Nonconformity"),
])
def test_classify_mad_conformity_boundaries(mad, expected_band):
    assert classify_mad_conformity(mad) == expected_band
