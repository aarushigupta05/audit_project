from rule_round_number import check_round_number_bias, is_round_number


def test_is_round_number():
    assert is_round_number(20000.0) is True
    assert is_round_number(10000.0) is True
    assert is_round_number(20123.45) is False
    assert is_round_number(9999.99) is False


def test_flags_round_non_exempt_account(gl_rows):
    flagged_ids = {r["entry_id"] for r in check_round_number_bias(gl_rows)}
    assert "JV_4" in flagged_ids


def test_exempts_rent_expense_even_when_round(gl_rows):
    flagged_ids = {r["entry_id"] for r in check_round_number_bias(gl_rows)}
    assert "JV_5" not in flagged_ids


def test_does_not_flag_non_round_amount(gl_rows):
    flagged_ids = {r["entry_id"] for r in check_round_number_bias(gl_rows)}
    assert "JV_6" not in flagged_ids
