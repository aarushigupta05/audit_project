"""
Tests for the upload path's data-layer pieces in dashboard/data.py:
save_upload outcomes (baseline documents are never overwritten),
document_usage labels, the tax-compliance summaries, tolerant JSON loading,
chronological GST period ordering, and the content-based GSTR adapter's
extract cache / bad-file handling.
"""
import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "dashboard"))
sys.path.insert(0, os.path.join(TESTS_DIR, "..", "scripts"))

import data as dd


@pytest.fixture
def raw(tmp_path, monkeypatch):
    d = tmp_path / "raw_pdfs"
    d.mkdir()
    monkeypatch.setattr(dd, "RAW_PDFS_DIR", str(d))
    return d


# ---- save_upload ------------------------------------------------------

def test_new_name_is_saved(raw):
    path, outcome = dd.save_upload("client_ledger.pdf", b"%PDF-1.4 a")
    assert outcome == "saved" and os.path.basename(path) == "client_ledger.pdf"
    assert (raw / "client_ledger.pdf").read_bytes() == b"%PDF-1.4 a"


def test_same_name_own_upload_is_replaced(raw):
    dd.save_upload("client_ledger.pdf", b"%PDF-1.4 a")
    path, outcome = dd.save_upload("client_ledger.pdf", b"%PDF-1.4 bb")
    assert outcome == "replaced"
    assert (raw / "client_ledger.pdf").read_bytes() == b"%PDF-1.4 bb"


def test_established_document_is_never_overwritten(raw):
    (raw / "ledgers_redacted.pdf").write_bytes(b"%PDF-1.4 original")
    path, outcome = dd.save_upload("ledgers_redacted.pdf", b"%PDF-1.4 copy")
    assert outcome == "saved_alongside"
    assert os.path.basename(path) == "ledgers_redacted_2.pdf"
    assert (raw / "ledgers_redacted.pdf").read_bytes() == b"%PDF-1.4 original"
    # a second copy gets the next free number
    path3, outcome3 = dd.save_upload("ledgers_redacted.pdf", b"%PDF-1.4 copy 2")
    assert os.path.basename(path3) == "ledgers_redacted_3.pdf" and outcome3 == "saved_alongside"


def test_established_name_not_yet_on_disk_is_a_plain_save(raw):
    _, outcome = dd.save_upload("ledgers_redacted.pdf", b"%PDF-1.4 x")
    assert outcome == "saved"


def test_non_pdf_is_refused(raw):
    with pytest.raises(ValueError):
        dd.save_upload("notes.txt", b"hello")
    assert list(raw.iterdir()) == []


def test_path_components_in_the_name_are_ignored(raw):
    path, _ = dd.save_upload("../../evil.pdf", b"%PDF-1.4 x")
    assert os.path.dirname(path) == str(raw)


def _record_results(out, name):
    """A finished-results record for the ledger saved as `name`."""
    meta = out / f"ledger_meta__{os.path.splitext(name)[0]}.json"
    meta.write_text(json.dumps({"ok": True, "slug": os.path.splitext(name)[0]}))
    return meta


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    d = tmp_path / "output"
    d.mkdir()
    monkeypatch.setattr(dd, "OUTPUT_DIR", str(d))
    return d


def test_replacing_a_file_removes_the_old_files_results(raw, out_dir):
    """Results belong to the document that produced them: a new file saved
    under an old name must not be shown with the old file's numbers."""
    dd.save_upload("client_ledger.pdf", b"%PDF-1.4 a")
    meta = _record_results(out_dir, "client_ledger.pdf")
    dd.save_upload("client_ledger.pdf", b"%PDF-1.4 a different ledger")
    assert not meta.exists()
    assert dd.ledger_tab_info(str(raw / "client_ledger.pdf")) is None


def test_results_left_behind_by_a_deleted_file_are_not_reused(raw, out_dir):
    meta = _record_results(out_dir, "client_ledger.pdf")     # file itself is gone
    dd.save_upload("client_ledger.pdf", b"%PDF-1.4 again")
    assert not meta.exists()


def test_results_of_other_documents_are_left_alone(raw, out_dir):
    other = _record_results(out_dir, "other_ledger.pdf")
    dd.save_upload("client_ledger.pdf", b"%PDF-1.4 a")
    assert other.exists()


def test_a_copy_saved_alongside_an_established_document_starts_with_no_results(raw, out_dir):
    (raw / "ledgers_redacted.pdf").write_bytes(b"%PDF-1.4 original")
    stale = _record_results(out_dir, "ledgers_redacted_2.pdf")
    path, outcome = dd.save_upload("ledgers_redacted.pdf", b"%PDF-1.4 copy")
    assert outcome == "saved_alongside" and os.path.basename(path) == "ledgers_redacted_2.pdf"
    assert not stale.exists()


def test_established_sample_ledgers_keep_their_results_record(raw, out_dir):
    """The sample ledgers' results do not come from this record; saving their
    name must not touch anything of theirs."""
    slug = next(iter(dd.LEDGER_PDF_INFO))
    meta = _record_results(out_dir, slug + ".pdf")
    dd._forget_previous_results(str(raw / (slug + ".pdf")))
    assert meta.exists()


# ---- document_usage -----------------------------------------------------

def _d(name, doctype):
    return {"filename": name, "path": "/nonexistent/" + name, "doctype": doctype,
            "evidence": "", "error": None}


def test_document_usage_labels(monkeypatch):
    import source_resolver
    monkeypatch.setattr(source_resolver, "resolve_tax_sources", lambda docs: {
        "status": "ok", "filenames": {"form_26as": "new26.pdf"}})
    monkeypatch.setattr(source_resolver, "resolve_trial_balance",
                        lambda docs: {"filename": "tb_new.pdf"})
    docs = [_d("new26.pdf", "form_26as"), _d("old26.pdf", "form_26as"),
            _d("tb_new.pdf", "trial_balance"), _d("tb_old.pdf", "trial_balance"),
            _d("g1.pdf", "gstr1"), _d("g3.pdf", "gstr3b"), _d("x.pdf", "unknown"),
            _d("led.pdf", "ledger")]
    u = dd.document_usage(docs)
    assert u["new26.pdf"] == "Used in Tax & Compliance"
    assert u["old26.pdf"] == "Superseded by a newer upload"
    assert u["tb_new.pdf"] == "Used in Tax & Compliance"
    assert u["tb_old.pdf"] == "Superseded by a newer upload"
    assert u["g1.pdf"] == u["g3.pdf"] == "Used in GST Compliance -- your uploaded returns"
    assert u["x.pdf"] == "Not recognised"
    assert "led.pdf" not in u


def test_document_usage_incomplete_set_says_no_matching_documents(monkeypatch):
    import source_resolver
    monkeypatch.setattr(source_resolver, "resolve_tax_sources",
                        lambda docs: {"status": "incomplete", "filenames": {}})
    monkeypatch.setattr(source_resolver, "resolve_trial_balance", lambda docs: None)
    u = dd.document_usage([_d("a.pdf", "form_26as")])
    assert u["a.pdf"].startswith("Not used")


# ---- summaries ------------------------------------------------------------

def test_summaries_handle_missing_report():
    itr = dd.tax_compliance_itr_summary(None)
    tb = dd.tax_compliance_tb_summary(None)
    assert itr["status"] == tb["status"] == "none"
    assert itr["flags_df"].empty and tb["flags_df"].empty
    assert list(tb["flags_df"].columns)[:2] == ["account", "side"]


def test_summaries_carry_report_values():
    rep = {"itr_vs_26as": {"status": "ok", "assessment_year": "2025-26", "deductor_count": 2,
                           "total_claimed": 10.0, "total_reported": 12.5, "sources": {"a": "b"},
                           "flags": [{"entity": "X", "reason": "r", "severity": "HIGH", "source": "s"}]},
           "trial_balance": {"status": "incomplete", "reason": "why", "period": "p"}}
    itr = dd.tax_compliance_itr_summary(rep)
    assert itr["deductor_count"] == 2 and len(itr["flags_df"]) == 1
    tb = dd.tax_compliance_tb_summary(rep)
    assert tb["status"] == "incomplete" and tb["reason"] == "why" and tb["flags_df"].empty


# ---- JSON loading -----------------------------------------------------------

def test_corrupt_or_missing_report_reads_as_not_generated(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    bad = tmp_path / "r.json"
    bad.write_text("{ half written", encoding="utf-8")
    assert dd.load_tax_compliance_report(str(bad)) is None
    assert dd.load_tax_compliance_report(str(tmp_path / "nope.json")) is None
    bad.write_text('{"ok": 1}', encoding="utf-8")
    assert dd.load_tax_compliance_report(str(bad)) == {"ok": 1}


# ---- GST period ordering -------------------------------------------------------

def test_gst_periods_sort_chronologically():
    labels = ["January 2025-26", "April 2025-26", "December 2025-26", "May 2025-26",
              "March 2025-26", "February 2025-26"]
    assert dd.sort_gst_periods(labels) == [
        "April 2025-26", "May 2025-26", "December 2025-26",
        "January 2025-26", "February 2025-26", "March 2025-26"]


def test_gst_periods_across_years_and_unparseable_last():
    out = dd.sort_gst_periods(["April 2026-27", "weird", "March 2025-26"])
    assert out == ["March 2025-26", "April 2026-27", "weird"]


# ---- content-based GSTR adapter -----------------------------------------------------

def test_gstr_adapter_skips_unreadable_file_and_uses_cache(tmp_path, monkeypatch):
    import adapt_gstr_to_schema as ag
    good1, good3, bad = (tmp_path / n for n in ("g1.pdf", "g3.pdf", "bad.pdf"))
    for p in (good1, good3, bad):
        p.write_bytes(b"%PDF-1.4 stub " + p.name.encode())
    calls = []

    def fake_extract(path, all_fn, single_fn):
        calls.append(os.path.basename(path))
        if os.path.basename(path) == "bad.pdf":
            raise ValueError("unknown layout")
        if os.path.basename(path) == "g1.pdf":
            return [{"financial_year": "2025-26", "period": "April 2025-26", "taxable_value": "1"}], True
        return [{"financial_year": "2025-26", "period": "April 2025-26", "tax_paid": "1"}], True

    monkeypatch.setattr(ag, "_extract_any_shape", fake_extract)
    cache = str(tmp_path / "cache.json")
    o1, o3 = str(tmp_path / "o1.csv"), str(tmp_path / "o3.csv")
    r1, r3, warnings = ag.adapt_gstr_documents([str(good1), str(bad)], [str(good3)], o1, o3, cache_path=cache)
    assert len(r1) == 1 and len(r3) == 1
    assert len(warnings) == 1 and "bad.pdf" in warnings[0]
    assert os.path.exists(o1) and os.path.exists(o3) and os.path.exists(cache)

    calls.clear()
    ag.adapt_gstr_documents([str(good1)], [str(good3)], o1, o3, cache_path=cache)
    assert calls == []                      # served from the extract cache


def test_gstr_adapter_with_nothing_usable_raises(tmp_path, monkeypatch):
    import adapt_gstr_to_schema as ag
    p = tmp_path / "g1.pdf"
    p.write_bytes(b"%PDF-1.4 x")
    monkeypatch.setattr(ag, "_extract_any_shape",
                        lambda *a: (_ for _ in ()).throw(ValueError("nope")))
    with pytest.raises(FileNotFoundError):
        ag.adapt_gstr_documents([str(p)], [], str(tmp_path / "a"), str(tmp_path / "b"),
                                cache_path=str(tmp_path / "c.json"))


# ---- Re-run Analysis refreshes ledgers the person processed ---------------

def _write_meta(out_dir, slug, csv_path, label, ok=True):
    (out_dir / f"ledger_meta__{slug}.json").write_text(json.dumps({
        "ok": ok, "slug": slug, "output_csv": str(csv_path), "report_label": label,
    }), encoding="utf-8")


def test_rerun_analysis_refreshes_processed_ledgers_with_current_rules(tmp_path, monkeypatch):
    import combined_report
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(dd, "OUTPUT_DIR", str(out))
    mine, gone, failed = (tmp_path / n for n in ("mine.csv", "gone.csv", "x.csv"))
    mine.write_text("a\n", encoding="utf-8")
    failed.write_text("a\n", encoding="utf-8")
    _write_meta(out, "mine", mine, "FY2024-25 mine")
    _write_meta(out, "gone", gone, "FY2024-25 gone")              # CSV missing -> skipped
    _write_meta(out, "broken", failed, "FY2024-25 broken", ok=False)  # failed processing -> skipped
    _write_meta(out, "ledgers_redacted", mine, "FY2024-25")        # established -> own pipeline
    calls = []
    monkeypatch.setattr(combined_report, "run_ledger_only_report",
                        lambda csv_path, label: calls.append((os.path.basename(csv_path), label)))
    assert dd.refresh_processed_ledger_reports() == ["FY2024-25 mine"]
    assert calls == [("mine.csv", "FY2024-25 mine")]


def test_one_failing_ledger_does_not_stop_the_others(tmp_path, monkeypatch):
    import combined_report
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(dd, "OUTPUT_DIR", str(out))
    for n in ("a", "b"):
        (tmp_path / f"{n}.csv").write_text("x\n", encoding="utf-8")
        _write_meta(out, n, tmp_path / f"{n}.csv", f"L {n}")
    done = []

    def fake(csv_path, label):
        if label == "L a":
            raise ValueError("bad csv")
        done.append(label)

    monkeypatch.setattr(combined_report, "run_ledger_only_report", fake)
    with pytest.raises(RuntimeError, match="L a: bad csv"):
        dd.refresh_processed_ledger_reports()
    assert done == ["L b"]


def test_ledger_refresh_step_survives_missing_baseline_when_own_ledgers_refreshed(monkeypatch):
    monkeypatch.setattr(dd, "refresh_processed_ledger_reports", lambda: ["L x"])
    monkeypatch.setattr(dd, "run_fy2025_26_pipeline",
                        lambda: (_ for _ in ()).throw(FileNotFoundError("no baseline")))
    dd.run_ledger_analysis_refresh()  # must not raise
    monkeypatch.setattr(dd, "refresh_processed_ledger_reports", lambda: [])
    with pytest.raises(FileNotFoundError):
        dd.run_ledger_analysis_refresh()


# ---- Ledger vs GSTR-1 for any uploaded ledger ---------------------------------

def _sale_rows(entry_id, date, taxable):
    half = round(taxable * 0.09, 2)
    rows = []
    for account, dr_cr, amount in (
        ("Sales GST", "Cr", taxable), ("CGST", "Cr", half), ("SGST", "Cr", half),
        ("Customer", "Dr", taxable + 2 * half),
    ):
        rows.append({"entry_id": entry_id, "date": date, "account": account,
                     "dr_cr": dr_cr, "amount": str(amount), "vch_type": "Sales GST"})
    return rows


def _write_ledger_csv(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_id", "date", "account", "dr_cr", "amount", "vch_type"])
        w.writeheader()
        w.writerows(rows)


def _g1(period, taxable, cgst, sgst):
    return {"financial_year": "2025-26", "period": period, "taxable_value": str(taxable),
            "igst": "0", "cgst": str(cgst), "sgst": str(sgst), "non_gst_value": "0"}


@pytest.fixture
def gst_env(tmp_path, monkeypatch):
    """A tiny world: no sample ledger, one processed ledger of the person's
    own (April sale of 1,000 taxable), built-in sample returns that would
    NOT match it, and (per test) the person's own uploaded return."""
    import source_resolver
    import adapt_gstr_to_schema as ag
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(dd, "OUTPUT_DIR", str(out))
    monkeypatch.setattr(dd, "FY2025_26_GL_PATH", str(tmp_path / "no_sample_ledger.csv"))
    csv_path = tmp_path / "mine.csv"
    _write_ledger_csv(csv_path, _sale_rows("S1", "5-Apr-25", 1000))
    (out / "ledger_meta__mine.json").write_text(json.dumps({
        "ok": True, "slug": "mine", "output_csv": str(csv_path), "label": "FY2025-26",
        "filename": "mine.pdf", "report_label": "FY2025-26 mine",
    }), encoding="utf-8")
    docs = [
        {"path": "/x/GSTR1_All_Months_Anonymised.pdf", "doctype": "gstr1"},   # built-in sample
        {"path": "/x/my_gstr1.pdf", "doctype": "gstr1"},                       # the person's own
    ]
    monkeypatch.setattr(source_resolver, "scan_documents", lambda **kw: docs)
    sample_rows = [_g1("April", 999999, 1, 1)]          # a different business's numbers
    own_rows = [_g1("April", 1000, 90, 90)]             # matches the ledger

    def fake_rows(paths, cache_path=None):
        names = sorted(os.path.basename(p) for p in paths)
        if names == ["GSTR1_All_Months_Anonymised.pdf"]:
            return sample_rows, []
        if names == ["my_gstr1.pdf"]:
            return own_rows, []
        return [], []

    monkeypatch.setattr(ag, "extract_gstr1_rows", fake_rows)
    return {"out": out, "docs": docs}


def test_gstr1_documents_are_split_into_built_in_and_uploaded():
    docs = [
        {"path": "/r/GSTR1_All_Months_Anonymised.pdf", "doctype": "gstr1"},
        {"path": "/r/gstr1_june_2026_redacted.pdf", "doctype": "gstr1"},
        {"path": "/r/client_gstr1.pdf", "doctype": "gstr1"},
        {"path": "/r/client_gstr3b.pdf", "doctype": "gstr3b"},
    ]
    built_in, uploaded = dd.gstr1_paths_by_origin(docs)
    assert [os.path.basename(p) for p in built_in] == [
        "GSTR1_All_Months_Anonymised.pdf", "gstr1_june_2026_redacted.pdf"]
    assert [os.path.basename(p) for p in uploaded] == ["client_gstr1.pdf"]


def test_uploaded_ledger_is_checked_against_the_persons_own_returns_only(gst_env):
    dd.run_ledger_gstr_pipeline()
    report = json.loads((gst_env["out"] / "ledger_gstr__mine.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["dataset"]["gstr1_sources"] == ["my_gstr1.pdf"]
    assert report["dataset"]["ledger_label"].endswith("(mine.pdf)")
    assert report["dataset"]["periods_compared"] == ["April 2025-26"]
    # The sample returns (999,999 taxable) would have produced a CRITICAL
    # mismatch; they must not have been used.
    assert report["flags"] == []


def test_uploaded_ledger_with_a_mismatch_is_flagged(gst_env, monkeypatch):
    import adapt_gstr_to_schema as ag
    monkeypatch.setattr(ag, "extract_gstr1_rows",
                        lambda paths, cache_path=None: ([_g1("April", 1500, 90, 90)], []))
    dd.run_ledger_gstr_pipeline()
    report = json.loads((gst_env["out"] / "ledger_gstr__mine.json").read_text(encoding="utf-8"))
    assert [f["check"] for f in report["flags"]] == ["ledger_vs_gstr1_taxable_supply"]


def test_uploaded_ledger_with_no_uploaded_returns_says_so(gst_env):
    gst_env["docs"][:] = [d for d in gst_env["docs"] if "my_gstr1" not in d["path"]]
    dd.run_ledger_gstr_pipeline()
    report = json.loads((gst_env["out"] / "ledger_gstr__mine.json").read_text(encoding="utf-8"))
    assert report["status"] == "no_returns" and report["flags"] == []


def test_ledger_gstr_pipeline_skips_when_there_is_no_ledger(tmp_path, monkeypatch):
    import source_resolver
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(dd, "OUTPUT_DIR", str(out))
    monkeypatch.setattr(dd, "FY2025_26_GL_PATH", str(tmp_path / "none.csv"))
    monkeypatch.setattr(source_resolver, "scan_documents", lambda **kw: [])
    with pytest.raises(FileNotFoundError):
        dd.run_ledger_gstr_pipeline()


def test_load_ledger_gstr_reports_lists_sample_first_then_uploads(tmp_path):
    def write(name, label):
        (tmp_path / name).write_text(json.dumps({"dataset": {"ledger_label": label}, "flags": []}),
                                     encoding="utf-8")
    write("ledger_gstr_reconciliation_report.json", "FY2025-26")
    write("ledger_gstr__zeta.json", "Zeta ledger")
    write("ledger_gstr__alpha.json", "Alpha ledger")
    (tmp_path / "ledger_gstr__broken.json").write_text("{not json", encoding="utf-8")
    entries = dd.load_ledger_gstr_reports(output_dir=str(tmp_path))
    assert [e["label"] for e in entries] == ["FY2025-26", "Alpha ledger", "Zeta ledger"]
    assert dd.load_ledger_gstr_reports(output_dir=str(tmp_path / "missing")) == []


def test_ledger_gstr_summary_carries_the_new_fields_and_tolerates_old_reports():
    old = {"generated_at": "2026-10-04T10:00:00", "dataset": {
        "ledger_periods_with_taxable_activity": ["April 2025-26"]},
        "flags": [], "summary": {"total_flagged": 0, "by_severity": {}}}
    s = dd.ledger_gstr_summary(old)
    assert s["status"] == "ok" and s["gstr1_sources"] is None and s["not_covered_periods"] == []
    new = dict(old, status="no_returns", dataset=dict(old["dataset"], gstr1_sources=[], ledger_label="L"))
    s = dd.ledger_gstr_summary(new)
    assert s["status"] == "no_returns" and s["ledger_label"] == "L"
    assert dd.ledger_gstr_summary(None)["status"] is None


def test_extract_gstr1_rows_reads_only_the_given_files_and_never_writes_the_summary(tmp_path, monkeypatch):
    import adapt_gstr_to_schema as ag
    p = tmp_path / "g1.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(ag, "_extract_any_shape",
                        lambda path, a, b: ([_g1("April", 1, 1, 1), _g1("April", 2, 2, 2)], True))
    rows, warnings = ag.extract_gstr1_rows([str(p)], cache_path=str(tmp_path / "c.json"))
    assert len(rows) == 1 and warnings == []        # a duplicate period is dropped
    assert ag.extract_gstr1_rows([], cache_path=str(tmp_path / "c.json")) == ([], [])


def test_document_usage_explains_why_an_unknown_document_is_not_used():
    docs = [
        {"filename": "scan.pdf", "path": "/r/scan.pdf", "doctype": "unknown", "issue": "scanned"},
        {"filename": "broken.pdf", "path": "/r/broken.pdf", "doctype": "unknown", "issue": "unreadable"},
        {"filename": "menu.pdf", "path": "/r/menu.pdf", "doctype": "unknown", "issue": None},
    ]
    u = dd.document_usage(docs)
    assert "scan or photo" in u["scan.pdf"]
    assert "could not be opened" in u["broken.pdf"]
    assert u["menu.pdf"] == "Not recognised"


# ---- GSTR-1 vs GSTR-3B: sample and uploaded returns never mixed ---------------

def _r1(period, taxable, fy="2025-26"):
    return {"financial_year": fy, "period": period, "taxable_value": str(taxable), "igst": "0",
            "cgst": "0", "sgst": "0", "cess": "0", "non_gst_value": "0"}


def _r3(period, taxable, fy="2025-26"):
    row = _r1(period, taxable, fy)
    row.update({"cgst_payable": "0", "cgst_paid_total": "0", "sgst_payable": "0",
                "sgst_paid_total": "0", "igst_payable": "0", "igst_paid_total": "0"})
    return row


def _write_rows(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def gstr_env(tmp_path, monkeypatch):
    """Sample returns (built-in names) that AGREE for April, and the person's
    own returns for the SAME April that DISAGREE -- so any mixing shows."""
    import source_resolver
    import adapt_gstr_to_schema as ag
    import rule_gstr_reconciliation as rg
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(dd, "OUTPUT_DIR", str(out))
    monkeypatch.setattr(rg, "OUTPUT_DIR", str(out))
    monkeypatch.setattr(dd, "GSTR_UPLOADED_REPORT_PATH", str(out / "gstr_report__uploaded.json"))
    s1, s3 = tmp_path / "gstr1_summary.csv", tmp_path / "gstr3b_summary.csv"
    monkeypatch.setattr(rg, "GSTR1_FILE", str(s1))
    monkeypatch.setattr(rg, "GSTR3B_FILE", str(s3))
    docs = [
        {"path": "/x/GSTR1_All_Months_Anonymised.pdf", "doctype": "gstr1"},
        {"path": "/x/GSTR3B_All_Months_Anonymised.pdf", "doctype": "gstr3b"},
        {"path": "/x/my_gstr1.pdf", "doctype": "gstr1"},
        {"path": "/x/my_gstr3b.pdf", "doctype": "gstr3b"},
    ]
    monkeypatch.setattr(source_resolver, "scan_documents", lambda **kw: docs)
    seen = {}

    def fake_adapt(g1_paths, g3_paths, *a, **kw):
        seen["sample_paths"] = sorted(os.path.basename(p) for p in list(g1_paths) + list(g3_paths))
        _write_rows(s1, [_r1("April", 1000)])
        _write_rows(s3, [_r3("April", 1000)])
        return [], [], []

    state = {"own1": [_r1("April", 5000), _r1("May", 200)], "own3": [_r3("April", 1000)]}
    monkeypatch.setattr(ag, "adapt_gstr_documents", fake_adapt)
    monkeypatch.setattr(ag, "extract_gstr1_rows",
                        lambda paths, cache_path=None: (state["own1"], []) if paths else ([], []))
    monkeypatch.setattr(ag, "extract_gstr3b_rows",
                        lambda paths, cache_path=None: (state["own3"], []) if paths else ([], []))
    return {"out": out, "docs": docs, "seen": seen, "state": state}


def _read(env, name):
    return json.loads((env["out"] / name).read_text(encoding="utf-8"))


def test_gstr_documents_are_split_by_origin():
    docs = [
        {"path": "/r/GSTR1_All_Months_Anonymised.pdf", "doctype": "gstr1"},
        {"path": "/r/gstr3b_june_2026_redacted.pdf", "doctype": "gstr3b"},
        {"path": "/r/client_gstr1.pdf", "doctype": "gstr1"},
        {"path": "/r/client_gstr3b.pdf", "doctype": "gstr3b"},
    ]
    g = dd.gstr_paths_by_origin(docs)
    names = lambda ps: [os.path.basename(p) for p in ps]  # noqa: E731
    assert names(g["sample"]["gstr1"]) == ["GSTR1_All_Months_Anonymised.pdf"]
    assert names(g["sample"]["gstr3b"]) == ["gstr3b_june_2026_redacted.pdf"]
    assert names(g["uploaded"]["gstr1"]) == ["client_gstr1.pdf"]
    assert names(g["uploaded"]["gstr3b"]) == ["client_gstr3b.pdf"]


def test_uploaded_return_is_isolated_from_sample_data_for_the_same_period(gstr_env):
    """THE regression this change exists for: the person's April return must
    be what is compared for April -- never replaced by the sample's April --
    and the sample set must stay exactly as it was."""
    dd.run_gstr_pipeline()
    sample = _read(gstr_env, "gstr_report.json")
    mine = _read(gstr_env, "gstr_report__uploaded.json")
    # sample: only sample documents were adapted, and it still reconciles cleanly
    assert gstr_env["seen"]["sample_paths"] == [
        "GSTR1_All_Months_Anonymised.pdf", "GSTR3B_All_Months_Anonymised.pdf"]
    assert sample["flags"] == [] and sample["dataset"]["periods_compared"] == ["April 2025-26"]
    assert sample["dataset"]["gstr1_sources"] == ["GSTR1_All_Months_Anonymised.pdf"]
    # uploaded: April used MY numbers (5000 vs 1000), not the sample's matching 1000/1000
    assert mine["dataset"]["gstr1_sources"] == ["my_gstr1.pdf"]
    assert mine["dataset"]["gstr3b_sources"] == ["my_gstr3b.pdf"]
    assert mine["dataset"]["periods_compared"] == ["April 2025-26"]
    assert [f["check"] for f in mine["flags"]] == ["gstr1_vs_gstr3b_taxable_supplies"]
    assert "Rs.5000" in mine["flags"][0]["reason"]


def test_uploaded_month_with_only_one_document_is_not_compared_and_not_a_failure(gstr_env):
    dd.run_gstr_pipeline()
    mine = _read(gstr_env, "gstr_report__uploaded.json")
    # May has a GSTR-1 but no GSTR-3B among the uploads
    assert mine["dataset"]["periods_only_in_gstr1"] == ["May 2025-26"]
    assert not any(f["check"] == "missing_filing" for f in mine["flags"])


def test_uploaded_gstr1_alone_is_reported_as_not_compared(gstr_env):
    gstr_env["docs"][:] = [d for d in gstr_env["docs"] if "my_gstr3b" not in d["path"]]
    gstr_env["state"]["own3"] = []
    dd.run_gstr_pipeline()
    mine = _read(gstr_env, "gstr_report__uploaded.json")
    assert mine["status"] == "no_gstr3b" and mine["flags"] == []
    assert mine["dataset"]["periods_compared"] == []


def test_without_uploads_only_the_sample_report_is_made(gstr_env):
    gstr_env["docs"][:] = [d for d in gstr_env["docs"] if "my_" not in d["path"]]
    dd.run_gstr_pipeline()
    assert (gstr_env["out"] / "gstr_report.json").exists()
    assert not (gstr_env["out"] / "gstr_report__uploaded.json").exists()


def test_only_uploaded_returns_still_run_when_the_samples_are_absent(gstr_env):
    gstr_env["docs"][:] = [d for d in gstr_env["docs"] if "my_" in d["path"]]
    dd.run_gstr_pipeline()
    assert not (gstr_env["out"] / "gstr_report.json").exists()
    assert (gstr_env["out"] / "gstr_report__uploaded.json").exists()


def test_gstr_pipeline_skips_when_there_are_no_returns(gstr_env):
    gstr_env["docs"][:] = []
    with pytest.raises(FileNotFoundError):
        dd.run_gstr_pipeline()


def test_load_gstr_reports_orders_sample_first_and_newest_report(tmp_path):
    (tmp_path / "gstr_report.json").write_text(json.dumps(
        {"generated_at": "2026-10-07T10:00:00", "dataset": {"label": "Sample returns"}}), encoding="utf-8")
    (tmp_path / "gstr_report__uploaded.json").write_text(json.dumps(
        {"generated_at": "2026-10-07T12:00:00", "dataset": {}}), encoding="utf-8")
    entries = dd.load_gstr_reports(output_dir=str(tmp_path))
    assert [e["label"] for e in entries] == ["Sample returns", "Your uploaded returns"]
    assert dd.newest_report(entries)["generated_at"] == "2026-10-07T12:00:00"
    assert dd.newest_report([]) is None
    assert dd.load_gstr_reports(output_dir=str(tmp_path / "none")) == []


def test_gstr_summary_carries_sources_and_tolerates_old_reports():
    old = {"generated_at": "t", "dataset": {"periods": ["April 2025-26"]},
           "flags": [], "summary": {"total_flagged": 0, "by_severity": {}}}
    s = dd.gstr_summary(old)
    assert s["status"] == "ok" and s["gstr1_sources"] is None and s["periods_compared"] is None
    new = dict(old, status="no_gstr3b", dataset=dict(
        old["dataset"], gstr1_sources=["a.pdf"], gstr3b_sources=[], periods_compared=[],
        periods_only_in_gstr1=["May 2025-26"], warnings=["w"], label="Mine"))
    s = dd.gstr_summary(new)
    assert s["status"] == "no_gstr3b" and s["only_in_gstr1"] == ["May 2025-26"] and s["label"] == "Mine"
    assert dd.gstr_summary(None)["status"] is None
