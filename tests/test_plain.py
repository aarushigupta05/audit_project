"""Tests for dashboard/plain.py -- the plain-language display wording."""
import os
import sys

import pandas as pd

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "dashboard"))

import plain
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "scripts"))


def test_round_reason_is_plain():
    out = plain.plain_reason("Suspiciously round amount (100000.0)")
    assert out.startswith("Round amount (Rs.100,000)") and "Suspicious" not in out


def test_duplicate_reason_is_plain_and_keeps_the_facts():
    out = plain.plain_reason(
        "Duplicate transaction candidate: matches voucher(s) Receipt_19 on 5-Apr-24 "
        "(Dr J & K Bank Ltd Rs.100000.00)"
    )
    assert "Receipt_19" in out and "5-Apr-24" in out and "J & K Bank Ltd" in out
    assert "Rs.100,000" in out and "candidate" not in out


def test_unknown_reason_is_left_alone():
    assert plain.plain_reason("Same person entered and approved this entry") == \
        "Same person entered and approved this entry"
    assert plain.plain_reason("something new") == "something new"
    assert plain.plain_reason("") == ""


def test_plain_why_deduplicates_identical_sentences():
    one = ("Duplicate transaction candidate: matches voucher(s) V2 on 1-Apr-24 "
           "(Dr Purchase Rs.500.00)")
    out = plain.plain_why(f"{one}; {one}")
    assert out.count("V2") == 1


def test_check_names_and_evidence():
    assert plain.plain_check_names("Round-Number Bias, Duplicate Transaction") == \
        "Round amount, Possible duplicate"
    ev = plain.plain_evidence("Round-Number Bias: Suspiciously round amount (50000.0)")
    assert ev.startswith("Round amount: Round amount (Rs.50,000)")
    assert plain.plain_evidence("no colon here") == "no colon here"


def test_benford_headline_cases():
    base = {"chi_square": 229.7, "threshold": 15.5}
    ok_but_strict = plain.benford_headline({**base, "deviates": True, "mad_conformity": "Acceptable conformity"})
    assert "expected pattern" in ok_but_strict and "practical test" in ok_but_strict
    assert "Both tests agree" in plain.benford_headline({**base, "deviates": False, "mad_conformity": "Close conformity"})
    assert "differ slightly" in plain.benford_headline({**base, "deviates": True, "mad_conformity": "Marginally acceptable"})
    assert "differ markedly" in plain.benford_headline({**base, "deviates": True, "mad_conformity": "Nonconformity"})
    assert plain.benford_headline(None) == "" and plain.benford_headline({"chi_square": None}) == ""


def test_mad_labels_cover_every_band():
    for band in ("Close conformity", "Acceptable conformity", "Marginally acceptable", "Nonconformity"):
        assert plain.mad_plain(band) != band
    assert plain.mad_plain("other") == "other"


def test_rename_columns_only_touches_known_columns():
    df = pd.DataFrame(columns=["vch_type", "group_min", "extra"])
    assert list(plain.rename_columns(df, plain.GAP_COLUMNS).columns) == ["Voucher type", "First number", "extra"]


def test_friendly_time():
    assert plain.friendly_time("2026-10-07T18:31:15") == "7 Oct 2026, 6:31 pm"
    assert plain.friendly_time("2026-10-07T00:05:00") == "7 Oct 2026, 12:05 am"
    assert plain.friendly_time("not a time") == "not a time"
    assert plain.friendly_time(None) is None


def test_ledger_name_hides_internal_tag():
    assert plain.ledger_name("FY2024-25 (frozen baseline)") == "FY2024-25 (sample ledger)"
    assert plain.ledger_name("FY2025-26") == "FY2025-26"
    assert plain.ledger_name(None) is None


def test_step_labels_cover_every_analysis_step():
    import data as dd
    for name, _ in dd._ANALYSIS_STEPS:
        assert plain.step_label(name) != name


def test_ledger_gst_status_messages():
    assert plain.ledger_gst_status_message({"status": "ok"}) is None
    assert plain.ledger_gst_status_message({}) is None
    assert plain.ledger_gst_status_message(None) is None
    assert "No GSTR-1 return" in plain.ledger_gst_status_message({"status": "no_returns"})
    assert "none of them covers" in plain.ledger_gst_status_message({"status": "no_overlap"})
    assert "No sales vouchers" in plain.ledger_gst_status_message({"status": "no_sales"})


def test_gst_flags_show_readable_check_names():
    df = pd.DataFrame([
        {"period": "May 2025-26", "check": "ledger_vs_gstr1_taxable_supply", "reason": "x", "severity": "LOW"},
        {"period": "June 2025-26", "check": "some_new_check", "reason": "y", "severity": "LOW"},
    ])
    out = plain.rename_gst_flags(df)
    assert list(out.columns) == ["Month", "Check", "What differs", "Severity"]
    assert list(out["Check"]) == ["Taxable sales: ledger vs GSTR-1", "some_new_check"]
    assert df["check"].iloc[0] == "ledger_vs_gstr1_taxable_supply"   # input not modified
    assert plain.rename_gst_flags(pd.DataFrame(columns=["period", "check", "reason", "severity"])).empty


def test_coverage_note_states_scope_and_the_limit_of_a_clean_result():
    note = plain.COVERAGE_NOTE
    for must in ("Tally ledger", "trial balance", "Form 26AS", "ITR", "GSTR-1", "GSTR-3B",
                 "Scans and photos", "not an assurance", "not proof"):
        assert must in note
    import classify_document
    assert set(classify_document.DOCTYPES) - {"unknown"} == {
        "ledger", "trial_balance", "form_26as", "itr_computation",
        "itr_acknowledgement", "gstr1", "gstr3b"}


def test_gst_status_and_not_compared_wording_never_says_filing_failure():
    for status in ("no_gstr1", "no_gstr3b", "no_overlap"):
        msg = plain.gst_status_message({"status": status})
        assert msg.startswith("Not compared")
        assert "fail" not in msg.lower() and "missing filing" not in msg.lower()
    assert plain.gst_status_message({"status": "ok"}) is None
    assert plain.gst_status_message(None) is None
    notes = plain.gst_not_compared_notes(
        {"only_in_gstr1": ["May 2025-26"], "only_in_gstr3b": ["June 2025-26"]})
    assert notes[0].startswith("Not compared -- GSTR-1 is on file but no GSTR-3B for: May")
    assert notes[1].startswith("Not compared -- GSTR-3B is on file but no GSTR-1 for: June")
    assert plain.gst_not_compared_notes({}) == []


def test_pending_note_names_the_document_still_being_read():
    note = plain.results_pending_note([
        {"kind": "upload", "label": "my_ledger.pdf"},
        {"kind": "analysis", "label": "Analysing documents"},
    ])
    assert "my_ledger.pdf" in note
    assert "do not include it yet" in note
    assert "Analysing documents" not in note      # an analysis job is not a document


def test_pending_note_handles_several_documents_and_repeats():
    jobs = [{"kind": "upload", "label": f"d{i}.pdf"} for i in (1, 2, 3, 4)]
    jobs.append({"kind": "ledger", "label": "d1.pdf"})          # same file, second stage
    note = plain.results_pending_note(jobs)
    assert "d1.pdf, d2.pdf and 2 more" in note and "do not include them yet" in note


def test_pending_note_without_a_document_says_the_results_are_the_last_run():
    for jobs in ([{"kind": "analysis", "label": "Analysing documents"}], [], None):
        note = plain.results_pending_note(jobs)
        assert "last completed run" in note and "Still reading" not in note


def test_pending_note_with_nothing_on_screen_does_not_point_at_results():
    jobs = [{"kind": "upload", "label": "my_ledger.pdf"}]
    note = plain.results_pending_note(jobs, have_results=False)
    assert "my_ledger.pdf" in note and "nothing to show yet" in note
    assert "below" not in note
    assert "below" not in plain.results_pending_note([], have_results=False)
    # with results on screen the original wording is unchanged
    assert "results below" in plain.results_pending_note(jobs)
