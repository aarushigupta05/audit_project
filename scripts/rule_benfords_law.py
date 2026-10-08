"""
RULE 3: Benford's Law Check
-----------------------------
Unlike Rules 1 and 2 (which flag INDIVIDUAL suspicious transactions),
this check looks at the ENTIRE dataset's first-digit distribution and
compares it against the naturally expected Benford's Law distribution.

Benford's Law: in naturally occurring numerical data, the first digit
is NOT uniformly distributed. Digit 1 appears ~30.1% of the time,
digit 2 ~17.6%, all the way down to digit 9 ~4.6%. This is a well
established statistical pattern used in forensic accounting.

If our actual data deviates significantly from this expected curve,
it suggests the numbers may have been manipulated/fabricated somewhere
in the dataset -- even if no single transaction looks suspicious on
its own.
"""

import csv
import os
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "..", "data", "general_ledger.csv")

# Expected Benford's Law percentage for each leading digit 1-9
BENFORD_EXPECTED = {
    1: 30.1, 2: 17.6, 3: 12.5, 4: 9.7, 5: 7.9,
    6: 6.7, 7: 5.8, 8: 5.1, 9: 4.6,
}


# Nigrini's Mean Absolute Deviation (MAD) conformity bands for the
# first-digit Benford's Law test specifically (the bands differ for the
# second-digit and other digit tests, which this module doesn't compute).
MAD_CONFORMITY_BANDS = {
    "close": 0.006,
    "acceptable": 0.012,
    "marginal": 0.015,
}


def get_first_digit(amount):
    # Convert amount to a string, strip any leading zeros/decimal issues,
    # and grab the very first numeral character
    amount_str = str(abs(amount))
    for char in amount_str:
        if char.isdigit() and char != "0":
            return int(char)
    return None


def compute_mad(rows):
    """Nigrini's Mean Absolute Deviation (MAD) for the first-digit Benford
    test: the average absolute gap between actual and expected digit
    PROPORTIONS (0-1 scale, not percentage points), across all 9 digits.

    ADDED 2026-10-02 alongside (not replacing) the existing chi-square
    check. Chi-square's significance threshold scales with sample size --
    at n=14,625 (this dataset), even small, immaterial deviations trigger
    "deviates" at the standard 15.5 threshold, which read-only segment
    analysis (scripts/analyze_benford_segmentation.py) showed happening
    across every voucher-type/account slice, including the least-repetitive
    one (Journal entries, chi-square=25.96 -- still "deviates", but far
    below Sales/Purchase/Contra's triple-digit values). MAD is sample-size
    independent, so it's a useful second opinion on whether that deviation
    is actually large in practice. Per Nigrini's published conformity
    bands for this test: MAD < 0.006 is "Close conformity", 0.006-0.012
    "Acceptable conformity", 0.012-0.015 "Marginally acceptable", and
    >= 0.015 "Nonconformity". Nothing about chi-square's output or
    threshold is changed by this -- it's an additional, independent read.
    """
    first_digits = [get_first_digit(float(row["amount"])) for row in rows]
    first_digits = [d for d in first_digits if d]
    total = len(first_digits)
    counts = Counter(first_digits)

    mad = 0.0
    for digit in range(1, 10):
        expected_proportion = BENFORD_EXPECTED[digit] / 100
        actual_proportion = counts.get(digit, 0) / total
        mad += abs(actual_proportion - expected_proportion)
    mad /= 9
    return mad


def classify_mad_conformity(mad):
    """See compute_mad()'s docstring for where these first-digit-test
    band boundaries come from."""
    if mad < MAD_CONFORMITY_BANDS["close"]:
        return "Close conformity"
    elif mad < MAD_CONFORMITY_BANDS["acceptable"]:
        return "Acceptable conformity"
    elif mad < MAD_CONFORMITY_BANDS["marginal"]:
        return "Marginally acceptable"
    else:
        return "Nonconformity"


def check_benfords_law(rows):
    first_digits = []
    for row in rows:
        amount = float(row["amount"])
        digit = get_first_digit(amount)
        if digit:
            first_digits.append(digit)

    total = len(first_digits)
    counts = Counter(first_digits)

    print(f"{'Digit':<8}{'Expected %':<14}{'Actual %':<14}{'Difference'}")
    print("-" * 50)

    chi_square = 0.0
    for digit in range(1, 10):
        expected_pct = BENFORD_EXPECTED[digit]
        actual_count = counts.get(digit, 0)
        actual_pct = (actual_count / total) * 100
        difference = actual_pct - expected_pct

        print(f"{digit:<8}{expected_pct:<14}{actual_pct:<14.2f}{difference:+.2f}")

        # chi-square contribution from this digit
        expected_count = (expected_pct / 100) * total
        chi_square += ((actual_count - expected_count) ** 2) / expected_count

    print()
    print(f"Total transactions analyzed: {total}")
    print(f"Chi-square statistic: {chi_square:.2f}")

    # A common rough threshold: chi-square above ~15.5 (for 8 degrees of
    # freedom, digits 1-9) suggests the distribution likely does NOT
    # match Benford's Law at a 95% confidence level.
    threshold = 15.5
    deviates = chi_square > threshold
    if deviates:
        print(f"RESULT: Chi-square exceeds threshold ({threshold}) -- "
              f"the overall number pattern deviates significantly from "
              f"Benford's Law. Worth deeper investigation.")
    else:
        print(f"RESULT: Chi-square is within threshold ({threshold}) -- "
              f"the overall number pattern looks statistically natural.")

    # Returned (not just printed) so this is callable/testable from other
    # code -- previously this function always returned None, which made it
    # impossible to assert on from a test without scraping stdout.
    return chi_square, deviates


if __name__ == "__main__":
    with open(INPUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    check_benfords_law(rows)

    mad = compute_mad(rows)
    conformity = classify_mad_conformity(mad)
    print()
    print(f"Nigrini's MAD (first-digit test): {mad:.5f} -- {conformity}")
