"""
Tests for scripts/tax_compliance_report.py -- the content-based Tax &
Compliance report. Statuses (none / incomplete / ok / error) are exercised
with synthetic document lists; the real-document tests pin today's numbers
and skip where the (gitignored) PDFs are absent.
"""
import csv
import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "scripts"))

import source_resolver as sr
import tax_compliance_report as tcr

RAW = os.path.join(TESTS_DIR, "..", "data", "raw_pdfs")
DATA = os.path.join(TESTS_DIR, "..", "data")
_REAL = [
    "annual_tax_statement_redacted.pdf", "income_computation_redacted.pdf",
    "income_tax_acknowledgement_redacted.pdf", "trial_balance_redacted.pdf",
]
_have_real = all(os.path.exists(os.path.join(RAW, n)) for n in _REAL)


def _doc(name, doctype, tmp_path):
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 stub")
    return {"filename": name, "path": str(p), "doctype": doctype, "evidence": "", "error": None}


def test_no_documents_gives_none_statuses_and_a_reason(tmp_path):
    rep = tcr.run_tax_compliance_report(documents=[], write=False)
    assert rep["itr_vs_26as"]["status"] == "none"
    assert rep["trial_balance"]["status"] == "none"
    assert rep["trial_balance"]["reason"]
    assert rep["generated_at"]


def test_partial_tax_documents_are_incomplete_and_say_what_is_missing(tmp_path):
    docs = [_doc("x.pdf", "form_26as", tmp_path)]
    rep = tcr.run_tax_compliance_report(documents=docs, write=False)
    sec = rep["itr_vs_26as"]
    assert sec["status"] in ("incomplete", "none")
    assert sec["flags"] == [] and sec["deductor_count"] == 0
    if sec["status"] == "incomplete":
        assert sec["missing"]


def test_trial_balance_with_unreadable_period_is_incomplete(tmp_path, monkeypatch):
    docs = [_doc("tb.pdf", "trial_balance", tmp_path)]
    monkeypatch.setattr(sr, "trial_balance_period", lambda p: None)
    rep = tcr.run_tax_compliance_report(documents=docs, write=False)
    tb = rep["trial_balance"]
    assert tb["status"] == "incomplete"
    assert "tb.pdf" in tb["reason"]
    assert tb["sources"] == {"trial_balance": "tb.pdf"}


def test_trial_balance_without_a_ledger_for_its_period_is_not_reconciled(tmp_path, monkeypatch):
    from datetime import date
    docs = [_doc("tb.pdf", "trial_balance", tmp_path)]
    monkeypatch.setattr(sr, "trial_balance_period", lambda p: (date(2024, 4, 1), date(2025, 3, 31)))
    led = tmp_path / "other.csv"
    led.write_text("date,account\n05-06-2026,Cash\n", encoding="utf-8")
    rep = tcr.run_tax_compliance_report(
        documents=docs, ledger_candidates=[{"label": "FY2025-26", "csv_path": str(led)}],
        write=False)
    tb = rep["trial_balance"]
    assert tb["status"] == "incomplete"
    assert tb["ledger_label"] is None and tb["flags"] == []
    assert "no processed ledger covers" in tb["reason"]


def test_report_is_written_atomically_and_parses(tmp_path):
    out = tmp_path / "sub" / "report.json"
    rep = tcr.run_tax_compliance_report(documents=[], report_path=str(out))
    assert json.loads(out.read_text(encoding="utf-8"))["generated_at"] == rep["generated_at"]
    assert [p.name for p in out.parent.iterdir()] == ["report.json"]  # no .tmp left behind


def test_write_false_creates_nothing(tmp_path):
    out = tmp_path / "report.json"
    tcr.run_tax_compliance_report(documents=[], write=False, report_path=str(out))
    assert not out.exists()


@pytest.mark.skipif(not _have_real, reason="real PDFs not present")
def test_real_documents_reproduce_the_established_numbers(tmp_path, monkeypatch):
    # Keep the CSVs this run writes out of the project's output/ folder.
    monkeypatch.setattr(tcr, "OUT_26AS_CSV", str(tmp_path / "f.csv"))
    monkeypatch.setattr(tcr, "OUT_ITR_CSV", str(tmp_path / "i.csv"))
    docs = sr.scan_documents()
    gl = os.path.join(DATA, "general_ledger.csv")
    rep = tcr.run_tax_compliance_report(
        documents=docs, ledger_candidates=[{"label": "FY2024-25", "csv_path": gl}],
        write=False)
    itr = rep["itr_vs_26as"]
    assert itr["status"] == "ok" and itr["assessment_year"] == "2025-26"
    assert itr["deductor_count"] == 1
    assert round(itr["total_claimed"]) == 32018 == round(itr["total_reported"])
    tb = rep["trial_balance"]
    assert tb["status"] == "ok" and tb["ledger_label"] == "FY2024-25"
    assert tb["clean_count"] == 82 and tb["mismatched_count"] == 0
