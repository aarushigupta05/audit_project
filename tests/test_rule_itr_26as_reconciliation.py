import pytest
from rule_itr_26as_reconciliation import check_reconciliation, classify_severity


@pytest.mark.parametrize("mismatch,claimed,expected_severity", [
    (5000, 10000, "CRITICAL"),   # 50% gap
    (3000, 10000, "CRITICAL"),   # exactly 30% -- boundary, >= 30 is CRITICAL
    (2999, 10000, "HIGH"),       # just under 30%
    (1000, 10000, "HIGH"),       # exactly 10% -- boundary, >= 10 is HIGH
    (999, 10000, "MEDIUM"),      # just under 10%
    (1, 10000, "MEDIUM"),        # tiny but nonzero gap
    (0, 10000, "NONE"),          # no gap
    (0, 0, "NONE"),              # genuine zero/zero -- no claim, nothing reported
    (500, 0, "CRITICAL"),        # claimed = 0 but something WAS reported -- the
                                  # percentage gap is undefined, not small; fixed
                                  # 2026-10-02 from the old "NONE" (see
                                  # classify_severity's docstring -- that was
                                  # never a deliberate severity choice, just a
                                  # side effect of avoiding ZeroDivisionError)
])
def test_classify_severity_boundaries(mismatch, claimed, expected_severity):
    assert classify_severity(mismatch, claimed) == expected_severity


def test_check_reconciliation_flags_all_mismatches_above_tolerance(form26as_rows):
    flagged = check_reconciliation(form26as_rows)
    flagged_names = {f["deductor_name"] for f in flagged}
    assert flagged_names == {"Critical Deductor", "High Deductor", "Medium Deductor",
                              "Zero Claimed Deductor"}
    assert "Clean Deductor" not in flagged_names


def test_check_reconciliation_assigns_correct_severity(form26as_rows):
    flagged = {f["deductor_name"]: f["severity"] for f in check_reconciliation(form26as_rows)}
    assert flagged["Critical Deductor"] == "CRITICAL"
    assert flagged["High Deductor"] == "HIGH"
    assert flagged["Medium Deductor"] == "MEDIUM"


def test_zero_claimed_does_not_crash_and_is_flagged(form26as_rows):
    """claimed=0, reported=500 is a real discrepancy (company claimed
    nothing but a deductor reported TDS against it) and must not raise
    ZeroDivisionError. Re-pinned 2026-10-02: severity is now CRITICAL, not
    NONE -- see classify_severity's docstring for why NONE was never a
    deliberate choice for this case."""
    flagged = {f["deductor_name"]: f for f in check_reconciliation(form26as_rows)}
    assert "Zero Claimed Deductor" in flagged
    assert flagged["Zero Claimed Deductor"]["severity"] == "CRITICAL"
