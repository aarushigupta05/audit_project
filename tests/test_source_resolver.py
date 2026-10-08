"""
Tests for scripts/source_resolver.py -- the persistent classification cache
(what makes a warm dashboard load do no PDF work) and the content-based
selection of tax / trial-balance / GST source documents (what lets an
upload under ANY filename reach Tax & Compliance).

Synthetic tests use a counting stand-in for classify_document() and
monkeypatched period readers, so they need no real PDFs; the real-document
tests at the bottom skip where the (gitignored) PDFs aren't present.
"""
import json
import os
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "scripts"))

import source_resolver as sr

RAW = os.path.join(TESTS_DIR, "..", "data", "raw_pdfs")


class _Counter:
    """Stand-in for classify_document() that records every call."""
    def __init__(self, doctype="ledger"):
        self.calls = []
        self.doctype = doctype

    def __call__(self, path):
        self.calls.append(os.path.basename(path))
        return {"doctype": self.doctype, "evidence": "fake", "error": None}


@pytest.fixture
def raw_dir(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    for name in ("a.pdf", "b.pdf"):
        (d / name).write_bytes(b"%PDF-1.4 " + name.encode())
    (d / "notes.txt").write_text("not a pdf")
    return d


@pytest.fixture
def cache_path(tmp_path):
    path = str(tmp_path / "cache" / "classification_cache.json")
    sr._memory.pop(path, None)
    yield path
    sr._memory.pop(path, None)


# ---------- persistent classification cache ----------

def test_scan_classifies_each_pdf_once_and_ignores_non_pdfs(raw_dir, cache_path):
    fake = _Counter()
    docs = sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert [d["filename"] for d in docs] == ["a.pdf", "b.pdf"]
    assert sorted(fake.calls) == ["a.pdf", "b.pdf"]
    assert all(d["doctype"] == "ledger" and os.path.isabs(d["path"]) for d in docs)


def test_second_scan_does_no_classification(raw_dir, cache_path):
    fake = _Counter()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    fake.calls.clear()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert fake.calls == []


def test_cache_survives_a_restart(raw_dir, cache_path):
    """The in-memory mirror is dropped (a new server process); the on-disk
    file alone must be enough to skip every classification."""
    fake = _Counter()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    sr._memory.pop(cache_path)
    fake.calls.clear()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert fake.calls == []
    assert os.path.exists(cache_path)


def test_changed_file_is_reclassified_but_unchanged_one_is_not(raw_dir, cache_path):
    fake = _Counter()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    fake.calls.clear()
    (raw_dir / "b.pdf").write_bytes(b"%PDF-1.4 a different, longer replacement")
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert fake.calls == ["b.pdf"]


def test_new_file_is_the_only_one_classified(raw_dir, cache_path):
    fake = _Counter()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    fake.calls.clear()
    (raw_dir / "c.pdf").write_bytes(b"%PDF-1.4 c")
    docs = sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert fake.calls == ["c.pdf"]
    assert [d["filename"] for d in docs] == ["a.pdf", "b.pdf", "c.pdf"]


def test_removed_file_is_dropped_from_the_cache(raw_dir, cache_path):
    sr.scan_documents(str(raw_dir), cache_path, classify=_Counter())
    os.remove(raw_dir / "a.pdf")
    sr.scan_documents(str(raw_dir), cache_path, classify=_Counter())
    with open(cache_path) as f:
        assert sorted(json.load(f)["entries"]) == ["b.pdf"]


def test_corrupt_cache_file_means_classify_fresh_never_an_error(raw_dir, cache_path):
    os.makedirs(os.path.dirname(cache_path))
    with open(cache_path, "w") as f:
        f.write("{ this is not json")
    fake = _Counter()
    docs = sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert len(docs) == 2 and len(fake.calls) == 2


def test_missing_directory_is_an_empty_scan(tmp_path, cache_path):
    assert sr.scan_documents(str(tmp_path / "nope"), cache_path, classify=_Counter()) == []


def test_progress_callback_reports_every_file_and_its_failure_is_ignored(raw_dir, cache_path):
    seen = []

    def progress(done, total, name):
        seen.append((done, total, name))
        raise RuntimeError("a broken UI callback must not break the scan")

    docs = sr.scan_documents(str(raw_dir), cache_path, on_progress=progress, classify=_Counter())
    assert len(docs) == 2
    assert seen == [(1, 2, "a.pdf"), (2, 2, "b.pdf")]


def test_pending_scan_count_counts_only_unclassified_or_changed(raw_dir, cache_path):
    assert sr.pending_scan_count(str(raw_dir), cache_path) == 2
    sr.scan_documents(str(raw_dir), cache_path, classify=_Counter())
    assert sr.pending_scan_count(str(raw_dir), cache_path) == 0
    (raw_dir / "a.pdf").write_bytes(b"%PDF-1.4 changed content here")
    assert sr.pending_scan_count(str(raw_dir), cache_path) == 1


def test_an_interrupted_scan_keeps_the_files_it_already_classified(raw_dir, cache_path):
    """The person uploading mid-load makes Streamlit abort the script run
    part-way through the scan. Each result must already be saved."""
    fake = _Counter()

    def abort_after_first(done, total, name):
        if done == 1:
            raise KeyboardInterrupt  # not an Exception: propagates like Streamlit's stop signal

    with pytest.raises(KeyboardInterrupt):
        # on_progress swallows Exception only; BaseException aborts the scan.
        sr.scan_documents(str(raw_dir), cache_path, on_progress=abort_after_first, classify=fake)
    sr._memory.pop(cache_path)
    fake.calls.clear()
    sr.scan_documents(str(raw_dir), cache_path, classify=fake)
    assert fake.calls == ["b.pdf"]  # a.pdf's result survived


# ---------- content-based selection ----------

def _doc(tmp_path, name, doctype, age_seconds=0):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 " + name.encode())
    t = time.time() - age_seconds
    os.utime(path, (t, t))
    return {"filename": name, "path": str(path), "doctype": doctype, "evidence": "", "error": None}


@pytest.fixture
def ay(monkeypatch):
    """Control the assessment year each fake document reports."""
    table = {}
    monkeypatch.setattr(sr, "assessment_year", lambda path, doctype: table.get(os.path.basename(path)))
    return table


def test_full_set_for_one_year_is_ok_whatever_the_filenames_are(tmp_path, ay):
    docs = [
        _doc(tmp_path, "my_26as.pdf", "form_26as"),
        _doc(tmp_path, "coi final.pdf", "itr_computation"),
        _doc(tmp_path, "ack.pdf", "itr_acknowledgement"),
    ]
    ay.update({"my_26as.pdf": "2025-26", "coi final.pdf": "2025-26", "ack.pdf": "2025-26"})
    r = sr.resolve_tax_sources(docs)
    assert r["status"] == "ok" and r["assessment_year"] == "2025-26" and r["missing"] == []
    assert r["filenames"] == {
        "form_26as": "my_26as.pdf", "itr_computation": "coi final.pdf",
        "itr_acknowledgement": "ack.pdf",
    }


def test_newest_upload_wins_but_only_with_partners_of_the_same_year(tmp_path, ay):
    docs = [
        _doc(tmp_path, "old_26as.pdf", "form_26as", age_seconds=1000),
        _doc(tmp_path, "new_26as.pdf", "form_26as", age_seconds=10),
        _doc(tmp_path, "coi_old.pdf", "itr_computation", age_seconds=1000),
        _doc(tmp_path, "ack_old.pdf", "itr_acknowledgement", age_seconds=1000),
        _doc(tmp_path, "coi_new.pdf", "itr_computation", age_seconds=5),
        _doc(tmp_path, "ack_new.pdf", "itr_acknowledgement", age_seconds=5),
    ]
    ay.update({
        "old_26as.pdf": "2024-25", "coi_old.pdf": "2024-25", "ack_old.pdf": "2024-25",
        "new_26as.pdf": "2025-26", "coi_new.pdf": "2025-26", "ack_new.pdf": "2025-26",
    })
    r = sr.resolve_tax_sources(docs)
    assert r["status"] == "ok" and r["assessment_year"] == "2025-26"
    assert r["filenames"]["form_26as"] == "new_26as.pdf"
    assert r["filenames"]["itr_computation"] == "coi_new.pdf"


def test_a_new_26as_without_its_own_year_partners_is_incomplete_not_mixed(tmp_path, ay):
    """The exact trap: a newer 26AS for a DIFFERENT year than the
    computation/acknowledgement on file must never be paired with them."""
    docs = [
        _doc(tmp_path, "26as_2025_26.pdf", "form_26as", age_seconds=5),
        _doc(tmp_path, "coi_2024_25.pdf", "itr_computation", age_seconds=500),
        _doc(tmp_path, "ack_2024_25.pdf", "itr_acknowledgement", age_seconds=500),
    ]
    ay.update({"26as_2025_26.pdf": "2025-26", "coi_2024_25.pdf": "2024-25",
               "ack_2024_25.pdf": "2024-25"})
    r = sr.resolve_tax_sources(docs)
    assert r["status"] == "incomplete"
    assert r["missing"] == ["itr_computation", "itr_acknowledgement"]
    assert "2025-26" in r["reason"] and "Computation of Income" in r["reason"]
    assert r["itr_computation"] is None and r["itr_acknowledgement"] is None


def test_only_one_partner_missing_names_exactly_that_one(tmp_path, ay):
    docs = [_doc(tmp_path, "a.pdf", "form_26as"), _doc(tmp_path, "b.pdf", "itr_computation")]
    ay.update({"a.pdf": "2025-26", "b.pdf": "2025-26"})
    r = sr.resolve_tax_sources(docs)
    assert r["status"] == "incomplete" and r["missing"] == ["itr_acknowledgement"]
    assert "ITR-V acknowledgement" in r["reason"] and "Computation" not in r["reason"]


def test_unreadable_assessment_year_is_reported_not_guessed(tmp_path, ay):
    docs = [_doc(tmp_path, "a.pdf", "form_26as"), _doc(tmp_path, "b.pdf", "itr_computation"),
            _doc(tmp_path, "c.pdf", "itr_acknowledgement")]
    r = sr.resolve_tax_sources(docs)  # every AY lookup returns None
    assert r["status"] == "incomplete" and "Could not read the assessment year" in r["reason"]


def test_no_26as_at_all_is_status_none(tmp_path, ay):
    r = sr.resolve_tax_sources([_doc(tmp_path, "tb.pdf", "trial_balance")])
    assert r["status"] == "none" and "No Form 26AS" in r["reason"]
    assert sr.resolve_tax_sources([])["status"] == "none"


def test_newest_trial_balance_is_chosen(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "trial_balance_period", lambda p: None)
    docs = [_doc(tmp_path, "old_tb.pdf", "trial_balance", age_seconds=900),
            _doc(tmp_path, "new_tb.pdf", "trial_balance", age_seconds=1)]
    assert sr.resolve_trial_balance(docs)["filename"] == "new_tb.pdf"
    assert sr.resolve_trial_balance([_doc(tmp_path, "x.pdf", "ledger")]) is None


def test_gst_sources_are_every_classified_gstr_file_regardless_of_name(tmp_path):
    docs = [
        _doc(tmp_path, "weird name 1.pdf", "gstr1"), _doc(tmp_path, "b.pdf", "gstr3b"),
        _doc(tmp_path, "a.pdf", "gstr1"), _doc(tmp_path, "led.pdf", "ledger"),
    ]
    g1, g3 = sr.gst_source_paths(docs)
    assert [os.path.basename(p) for p in g1] == ["a.pdf", "weird name 1.pdf"]
    assert [os.path.basename(p) for p in g3] == ["b.pdf"]


def test_parse_dmy_handles_two_and_four_digit_years():
    assert sr.parse_dmy("1-Apr-24").year == 2024
    assert sr.parse_dmy("31-Mar-2025").month == 3
    assert sr.parse_dmy("not a date") is None


# ---------- real established documents ----------

needs_real = pytest.mark.skipif(
    not os.path.exists(os.path.join(RAW, "annual_tax_statement_redacted.pdf")),
    reason="real (gitignored) raw PDFs not present",
)


@needs_real
def test_real_documents_resolve_to_the_established_2025_26_set():
    r = sr.resolve_tax_sources([
        {"filename": n, "path": os.path.join(RAW, n), "doctype": t}
        for n, t in [
            ("annual_tax_statement_redacted.pdf", "form_26as"),
            ("income_computation_redacted.pdf", "itr_computation"),
            ("income_tax_acknowledgement_redacted.pdf", "itr_acknowledgement"),
        ]
    ])
    assert r["status"] == "ok" and r["assessment_year"] == "2025-26"


@needs_real
def test_real_assessment_year_extraction_per_document_type():
    expect = {
        ("annual_tax_statement_redacted.pdf", "form_26as"): "2025-26",
        ("income_computation_redacted.pdf", "itr_computation"): "2025-26",
        ("income_tax_acknowledgement_redacted.pdf", "itr_acknowledgement"): "2025-26",
    }
    for (name, doctype), want in expect.items():
        assert sr.assessment_year(os.path.join(RAW, name), doctype) == want, name


@needs_real
def test_real_trial_balance_period_is_financial_year_2024_25():
    start, end = sr.trial_balance_period(os.path.join(RAW, "trial_balance_redacted.pdf"))
    assert (start.year, start.month, start.day) == (2024, 4, 1)
    assert (end.year, end.month, end.day) == (2025, 3, 31)


def test_cached_unknown_without_issue_field_is_classified_again(tmp_path):
    """An 'unknown' cached before classify_document() recorded why must be
    re-classified once, so a scan gets the clear explanation."""
    import source_resolver as sr
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    cache = str(tmp_path / "cache.json")
    calls = []

    def old_style(path):
        calls.append("old")
        return {"doctype": "unknown", "evidence": "No recognized marker", "error": None}

    def new_style(path):
        calls.append("new")
        return {"doctype": "unknown", "evidence": "no selectable text", "error": None, "issue": "scanned"}

    sr.classify_cached(str(pdf), cache_path=cache, classify=old_style)
    again = sr.classify_cached(str(pdf), cache_path=cache, classify=new_style)
    assert again["issue"] == "scanned" and calls == ["old", "new"]
    sr.classify_cached(str(pdf), cache_path=cache, classify=new_style)
    assert calls == ["old", "new"]          # now served from the cache
