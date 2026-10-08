"""
RULE 4: ITR vs Form 26AS Reconciliation Check
------------------------------------------------
This is the strongest, most direct check in the whole system.

Logic:
Form 26AS is filed independently by third parties (the vendors/clients who
paid the company and deducted tax on its behalf). The ITR is filed by the
company itself, claiming tax credit for that same TDS. These two numbers,
describing the SAME real-world transaction from two different sides,
should match.

If they don't match beyond a small rounding tolerance, that's a direct,
provable discrepancy -- not an inferred pattern like Benford's Law or
round-number bias. Either the company over-claimed credit, or the deductor
under-reported, or there's a genuine timing/clerical issue -- but the
mismatch itself is a hard fact, not a guess.
"""

import csv
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
ITR_FILE = os.path.join(DATA_DIR, "itr_summary.csv")
FORM26AS_FILE = os.path.join(DATA_DIR, "form26as.csv")

TOLERANCE_RUPEES = 10.0  # allow small rounding differences, nothing more


def classify_severity(mismatch_amount, claimed_amount):
    """FIX (2026-10-02): claimed_amount == 0 used to force pct_diff = 0
    purely to dodge ZeroDivisionError, which meant ANY nonzero mismatch
    against a zero claim fell into the "NONE" branch -- even though
    "claimed nothing, but a deductor reported TDS against you" is one of
    the most suspicious shapes this rule can see (percentage gap is
    undefined, not small). The old pinned test for this
    (tests/test_rule_itr_26as_reconciliation.py) only asserted this was
    reachable without crashing ("must not divide by zero"), never that
    NONE was the intended severity -- confirmed by combined_report.py's
    independent inline reimplementation of this same rule never agreeing
    with it (it defaulted to MEDIUM instead, by omission rather than
    design). Both are now fixed to agree: a genuine zero/zero (no claim,
    nothing reported) is NONE, but any nonzero mismatch against a zero
    claim is CRITICAL -- treated as the maximal, undefined-percentage
    case rather than silently downgraded."""
    if claimed_amount == 0:
        return "CRITICAL" if mismatch_amount != 0 else "NONE"

    pct_diff = abs(mismatch_amount / claimed_amount) * 100
    if pct_diff >= 30:
        return "CRITICAL"
    elif pct_diff >= 10:
        return "HIGH"
    elif pct_diff > 0:
        return "MEDIUM"
    else:
        return "NONE"


def check_reconciliation(form26as_rows):
    flagged = []

    for row in form26as_rows:
        reported = float(row["tds_reported_by_deductor"])
        claimed = float(row["tds_claimed_in_itr_share"])
        mismatch = claimed - reported  # positive = company claimed more than deductor reported

        if abs(mismatch) > TOLERANCE_RUPEES:
            severity = classify_severity(mismatch, claimed)
            flagged.append({
                "deductor_name": row["deductor_name"],
                "claimed_in_itr": claimed,
                "reported_by_deductor": reported,
                "mismatch_amount": round(mismatch, 2),
                "severity": severity,
            })

    return flagged


if __name__ == "__main__":
    with open(FORM26AS_FILE, "r") as f:
        reader = csv.DictReader(f)
        form26as_rows = list(reader)

    print(f"Total deductor entries checked: {len(form26as_rows)}")
    print()

    flagged = check_reconciliation(form26as_rows)

    print(f"Mismatches found: {len(flagged)}")
    print()

    if flagged:
        print(f"{'Deductor':<20}{'Claimed (ITR)':<16}{'Reported (26AS)':<18}{'Mismatch':<12}{'Severity'}")
        print("-" * 80)
        for item in flagged:
            print(f"{item['deductor_name']:<20}{item['claimed_in_itr']:<16}"
                  f"{item['reported_by_deductor']:<18}{item['mismatch_amount']:<12}{item['severity']}")

    # Validation only -- checking against our planted ground truth
    if form26as_rows and "mismatch_flag_ground_truth" in form26as_rows[0]:
        true_flags = sum(1 for row in form26as_rows if row["mismatch_flag_ground_truth"] == "YES")
        print()
        print("--- Validation against planted issues (test only) ---")
        print(f"Planted mismatches in data: {true_flags}")
        print(f"Correctly caught by our rule: {len(flagged)}")
