"""
Tests for dashboard/jobs.py (the background job runner) and
dashboard/ingest.py (what happens to an uploaded document). No PDFs and no
real analysis: the work functions are stand-ins.
"""
import os
import sys
import threading
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "dashboard"))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "scripts"))

import jobs
import ingest
import data as dd
import source_resolver


@pytest.fixture(autouse=True)
def clean_jobs():
    assert jobs.wait_idle(10)
    jobs._reset_for_tests()
    yield
    jobs.wait_idle(10)
    jobs._reset_for_tests()


def test_job_runs_and_records_result():
    jid = jobs.submit("k", "a", "A", lambda report: (report("working"), 42)[1])
    assert jobs.wait_idle(5)
    j = jobs.get(jid)
    assert j["status"] == "done" and j["result"] == 42 and j["finished_at"]
    assert "fn" not in j


def test_exception_becomes_failed_job_and_worker_survives():
    def boom(report):
        raise ValueError("bad document")
    bad = jobs.submit("k", "bad", "bad", boom)
    good = jobs.submit("k", "good", "good", lambda report: "fine")
    assert jobs.wait_idle(5)
    assert jobs.get(bad)["status"] == "failed"
    assert "ValueError: bad document" in jobs.get(bad)["error"]
    assert jobs.get(good)["status"] == "done"


def test_jobs_run_strictly_one_at_a_time_in_order():
    order, running, peak = [], [0], [0]
    lock = threading.Lock()

    def make(n):
        def fn(report):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.05)
            order.append(n)
            with lock:
                running[0] -= 1
        return fn

    for n in range(4):
        jobs.submit("k", n, str(n), make(n))
    assert jobs.wait_idle(10)
    assert order == [0, 1, 2, 3] and peak[0] == 1


def test_duplicate_active_job_is_not_queued_twice():
    gate = threading.Event()
    first = jobs.submit("k", "same", "x", lambda r: gate.wait(5))
    again = jobs.submit("k", "same", "x", lambda r: None)
    assert first == again
    gate.set()
    assert jobs.wait_idle(5)


def test_coalesce_queued_request_is_absorbed_but_running_one_is_not():
    gate, started = threading.Event(), threading.Event()

    def slow(report):
        started.set()
        gate.wait(5)

    running = jobs.submit("analysis", "all", "a", slow, coalesce=True)
    assert started.wait(5)
    queued = jobs.submit("analysis", "all", "a", lambda r: None, coalesce=True)
    assert queued != running                       # must run AFTER the running one
    absorbed = jobs.submit("analysis", "all", "a", lambda r: None, coalesce=True)
    assert absorbed == queued                      # a queued one absorbs further requests
    gate.set()
    assert jobs.wait_idle(5)


def test_snapshot_seq_changes_when_a_job_finishes_and_lists_active():
    gate = threading.Event()
    before = jobs.snapshot()["seq"]
    jobs.submit("k", "g", "g", lambda r: gate.wait(5))
    snap = jobs.snapshot()
    assert [j["label"] for j in snap["active"]] == ["g"] and jobs.is_active("k", "g")
    gate.set()
    assert jobs.wait_idle(5)
    after = jobs.snapshot()
    assert after["seq"] == before + 1 and after["active"] == []
    assert after["finished"][0]["label"] == "g"
    assert not jobs.is_active()


def test_stage_updates_are_visible_while_running():
    gate, seen = threading.Event(), threading.Event()

    def fn(report):
        report("Extracting data", detail="extracting")
        seen.set()
        gate.wait(5)

    jid = jobs.submit("k", "s", "s", fn)
    assert seen.wait(5)
    assert jobs.get(jid)["stage"] == "Extracting data"
    assert jobs.get(jid)["status"] == "running"
    gate.set()
    assert jobs.wait_idle(5)


# ---- ingest -------------------------------------------------------------

def _fake_classify(doctype, evidence="fake"):
    return lambda path, *a, **k: {"doctype": doctype, "evidence": evidence, "error": None}


def test_ledger_upload_is_processed_as_a_ledger(monkeypatch, tmp_path):
    p = tmp_path / "mine.pdf"
    p.write_bytes(b"x")
    monkeypatch.setattr(source_resolver, "classify_cached", _fake_classify("ledger"))
    calls = []

    def fake_process(path, on_stage=None):
        calls.append(path)
        on_stage("extracting")
        return {"ok": True, "rows": 3}

    monkeypatch.setattr(dd, "process_ledger_document", fake_process)
    monkeypatch.setattr(dd, "run_tax_compliance_pipeline", lambda: None)
    jid = ingest.submit_upload(str(p))
    assert jobs.wait_idle(10)
    res = jobs.get(jid)["result"]
    assert calls == [str(p)]
    assert res["kind"] == "ledger" and res["ok"] and res["filename"] == "mine.pdf"


@pytest.mark.parametrize("doctype", ["form_26as", "itr_computation", "itr_acknowledgement",
                                     "trial_balance", "gstr1", "gstr3b"])
def test_tax_and_gst_uploads_queue_an_analysis(monkeypatch, tmp_path, doctype):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"x")
    monkeypatch.setattr(source_resolver, "classify_cached", _fake_classify(doctype))
    ran = []
    monkeypatch.setattr(dd, "run_full_analysis",
                        lambda on_step=None: ran.append(1) or {"failed": [], "steps": []})
    jid = ingest.submit_upload(str(p))
    assert jobs.wait_idle(10)
    res = jobs.get(jid)["result"]
    assert res["kind"] == "routed" and res["doctype"] == doctype
    assert ran == [1]
    assert jobs.get(res["analysis_job"])["status"] == "done"


def test_unrecognised_upload_is_reported_with_a_reason(monkeypatch, tmp_path):
    p = tmp_path / "menu.pdf"
    p.write_bytes(b"x")
    monkeypatch.setattr(source_resolver, "classify_cached", _fake_classify("unknown", "no markers"))
    jid = ingest.submit_upload(str(p))
    assert jobs.wait_idle(10)
    res = jobs.get(jid)["result"]
    assert res["kind"] == "unrecognized" and res["ok"] is False
    assert "wasn't recognised" in res["error"] and "no markers" in res["error"]


def test_failed_ledger_processing_comes_back_as_failed_result_not_crash(monkeypatch, tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"x")
    monkeypatch.setattr(dd, "process_ledger_document",
                        lambda path, on_stage=None: (_ for _ in ()).throw(RuntimeError("parse failed")))
    jid = ingest.submit_ledger(str(p))
    assert jobs.wait_idle(10)
    assert jobs.get(jid)["status"] == "failed"
    assert "parse failed" in jobs.get(jid)["error"]


def test_uploads_made_while_busy_all_run_afterwards(monkeypatch, tmp_path):
    """The 'upload before the dashboard has loaded' case: everything queued
    during a long-running job is processed once it finishes -- none lost."""
    gate, started = threading.Event(), threading.Event()
    done = []
    monkeypatch.setattr(source_resolver, "classify_cached", _fake_classify("ledger"))
    monkeypatch.setattr(dd, "run_tax_compliance_pipeline", lambda: None)

    def fake_process(path, on_stage=None):
        done.append(os.path.basename(path))
        return {"ok": True}

    monkeypatch.setattr(dd, "process_ledger_document", fake_process)
    jobs.submit("analysis", "all", "boot", lambda r: (started.set(), gate.wait(5)), coalesce=True)
    assert started.wait(5)
    for n in ("a.pdf", "b.pdf", "c.pdf"):
        p = tmp_path / n
        p.write_bytes(b"x")
        ingest.submit_upload(str(p))
    assert done == []                      # nothing started yet -- the worker is busy
    assert {j["label"] for j in jobs.snapshot()["active"]} >= {"a.pdf", "b.pdf", "c.pdf"}
    gate.set()
    assert jobs.wait_idle(10)
    assert sorted(done) == ["a.pdf", "b.pdf", "c.pdf"]


def test_unreadable_documents_get_a_message_that_says_what_to_do():
    scanned = ingest.unreadable_message({"issue": "scanned", "evidence": "This PDF has no selectable text -- scan."})
    assert "no selectable text" in scanned
    broken = ingest.unreadable_message({"issue": "unreadable", "evidence": "Could not read this PDF: boom"})
    assert "could not be opened" in broken and "boom" not in broken
    other = ingest.unreadable_message({"issue": None, "evidence": "no markers"})
    assert "wasn't recognised" in other and "no markers" in other
