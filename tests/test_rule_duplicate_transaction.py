from rule_duplicate_transaction import identify_duplicate_legs, check_duplicate_transactions


def test_flags_cross_voucher_duplicate(gl_rows):
    flagged_ids = {r["entry_id"] for r in check_duplicate_transactions(gl_rows)}
    assert "Receipt_10" in flagged_ids
    assert "Receipt_11" in flagged_ids


def test_does_not_flag_legs_within_same_voucher(gl_rows):
    """Two legs of Journal_20 share account/amount/date/direction, but
    they're the SAME voucher (same entry_id) -- these are balancing
    line items, not a duplicate posting, and must never be flagged."""
    flagged_ids = {r["entry_id"] for r in check_duplicate_transactions(gl_rows)}
    assert "Journal_20" not in flagged_ids


def test_does_not_flag_matching_dr_and_cr_pair(gl_rows):
    """Journal_21 (Dr) and Journal_22 (Cr) share account/amount/date but
    opposite direction -- that's an ordinary double-entry pair (e.g. a
    transfer), not a duplicate. Direction must be part of the match key."""
    flagged_ids = {r["entry_id"] for r in check_duplicate_transactions(gl_rows)}
    assert "Journal_21" not in flagged_ids
    assert "Journal_22" not in flagged_ids


def test_three_way_duplicate_all_flagged_and_cross_reference_each_other(gl_rows):
    dup_info, flagged = identify_duplicate_legs(gl_rows)
    flagged_ids = {r["entry_id"] for r in flagged}
    assert {"Sales_30", "Sales_31", "Sales_32"}.issubset(flagged_ids)

    # Each flagged leg's reason text should reference the OTHER two
    # vouchers in its group, not itself.
    idx_by_entry_id = {row["entry_id"]: idx for idx, row in enumerate(gl_rows)}
    reason = dup_info[idx_by_entry_id["Sales_30"]]
    assert "Sales_31" in reason or "Sales_32" in reason
    assert "Sales_30" not in reason.split("matches voucher(s)")[1]


def _leg(entry_id, account, amount, date="5-Apr-24", dr_cr="Dr"):
    return {"entry_id": entry_id, "date": date, "account": account,
            "amount": amount, "entry_type": dr_cr, "dr_cr": dr_cr}


def test_sub_rupee_rounding_postings_are_not_duplicates():
    """Invoice rounding repeats by nature: the same few paise on the same day
    in the rounding account, from different vouchers, is not a repeated entry."""
    rows = [_leg("S1", "Round Off/Discount", "0.20"), _leg("S2", "Round Off/Discount", "0.20")]
    assert check_duplicate_transactions(rows) == []
    assert identify_duplicate_legs(rows)[0] == {}


def test_rounding_account_is_recognised_by_pattern_not_one_name():
    for name in ("Rounding Off", "Round off", "Rounded Off", "ROUNDING"):
        rows = [_leg("A", name, "0.40"), _leg("B", name, "0.40")]
        assert check_duplicate_transactions(rows) == [], name


def test_rounding_account_posting_of_one_rupee_or_more_is_still_checked():
    rows = [_leg("D1", "Round Off/Discount", "6000.00"), _leg("D2", "Round Off/Discount", "6000.00")]
    assert {r["entry_id"] for r in check_duplicate_transactions(rows)} == {"D1", "D2"}


def test_sub_rupee_amount_in_an_ordinary_account_is_still_checked():
    rows = [_leg("E1", "Bank Charges", "0.50"), _leg("E2", "Bank Charges", "0.50")]
    assert {r["entry_id"] for r in check_duplicate_transactions(rows)} == {"E1", "E2"}


def test_combined_report_inline_rule_matches_standalone_with_rounding_rows():
    """The dashboard runs combined_report's inline copy of this rule; it must
    make the same call as the standalone module on rounding rows."""
    import combined_report as cr
    rows = [
        _leg("S1", "Round Off/Discount", "0.20"), _leg("S2", "Round Off/Discount", "0.20"),
        _leg("D1", "Round Off/Discount", "6000.00"), _leg("D2", "Round Off/Discount", "6000.00"),
        _leg("R1", "Receipts", "100.00"), _leg("R2", "Receipts", "100.00"),
    ]
    standalone = {r["entry_id"] for r in check_duplicate_transactions(rows)}
    inline = {rows[i]["entry_id"] for i in cr.rule_duplicate_transactions(rows)}
    assert inline == standalone == {"D1", "D2", "R1", "R2"}
