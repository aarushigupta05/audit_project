from rule_segregation_of_duties import check_segregation_of_duties


def test_flags_same_entered_and_approved(gl_rows):
    flagged = check_segregation_of_duties(gl_rows)
    flagged_ids = {r["entry_id"] for r in flagged}
    assert "JV_1" in flagged_ids


def test_does_not_flag_different_approver(gl_rows):
    flagged = check_segregation_of_duties(gl_rows)
    flagged_ids = {r["entry_id"] for r in flagged}
    assert "JV_2" not in flagged_ids


def test_does_not_flag_when_both_empty(gl_rows):
    """Regression test for the real bug this project hit: "" == "" was
    vacuously True for every one of the 17,195 real rows before the
    empty-string guard was added, which made Rule 1 fire on the entire
    dataset. Must never regress."""
    flagged = check_segregation_of_duties(gl_rows)
    flagged_ids = {r["entry_id"] for r in flagged}
    assert "JV_3" not in flagged_ids


def test_all_real_rows_have_no_entered_or_approved_by_do_not_flag():
    """The real reconstructed ledger has no source for entered_by/
    approved_by (adapt_ledger_to_schema.py leaves them empty -- no real
    mapping exists). Confirms the rule produces zero flags on an
    all-empty dataset rather than silently flagging everything."""
    rows = [{"entry_id": f"X_{i}", "entered_by": "", "approved_by": ""} for i in range(50)]
    flagged = check_segregation_of_duties(rows)
    assert flagged == []
