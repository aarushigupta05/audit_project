"""
dashboard/ingest.py
-------------------
Glue between an uploaded document and the background job runner
(dashboard/jobs.py): what should happen to a file once it is on disk.
UI-agnostic (no streamlit), unit-tested in tests/test_jobs.py.

  submit_upload(path)       -- decide by CONTENT what the document is and run
                               the matching analysis:
        ledger              -> full ledger processing (Audit gets a new entry)
        26AS / ITR / trial balance / GSTR-1 / GSTR-3B
                            -> "Re-run Analysis" (Tax & Compliance updates)
        anything else       -> reported as not recognised, with the reason
  submit_ledger(path)       -- the Process / Re-process buttons: process one
                               ledger now.
  submit_analysis()         -- the sidebar's Re-run Analysis.

Every function returns immediately with a job id; the work happens on the
background worker. Nothing here raises for a bad document -- a document
that cannot be processed comes back as a failed job with the reason.
"""
import os

import data as dd
import jobs
import plain

# process_ledger_document()'s on_stage keys -> text shown in the live card.
LEDGER_STAGE_TEXT = {
    "scanning": "Scanning document",
    "extracting": "Extracting data",
    "auditing": "Running audit checks",
    "preparing": "Preparing results",
}


def _ledger_work(path):
    def work(report):
        report("Scanning document", detail="scanning")

        def on_stage(stage):
            report(LEDGER_STAGE_TEXT.get(stage, stage), detail=stage)

        result = dd.process_ledger_document(path, on_stage=on_stage)
        if result.get("ok"):
            # A ledger that has just become available may be the one a
            # trial balance already on file belongs to -- refresh that
            # match (cheap; never fatal to the ledger's own result).
            try:
                report("Matching tax documents", detail="preparing")
                dd.run_tax_compliance_pipeline()
            except Exception:
                pass
        return {"kind": "ledger", "filename": os.path.basename(path), **result}
    return work


def _analysis_work():
    def work(report):
        names = [name for name, _ in dd._ANALYSIS_STEPS]
        total = len(names)

        def on_step(step_name):
            idx = names.index(step_name) + 1 if step_name in names else 0
            report(f"{plain.step_label(step_name)} ({idx} of {total})", detail=("analysis", idx, total))

        result = dd.run_full_analysis(on_step=on_step)
        return {"kind": "analysis", "ok": not result["failed"], **result}
    return work


def unreadable_message(doc):
    """Why a document that was not recognised cannot be analysed, in words
    that say what to do. `doc` is a classification result (see
    scripts/classify_document.py): its "issue" separates a file that will
    not open, a scan with no readable text, and a readable document of a
    kind this dashboard does not check."""
    issue = doc.get("issue")
    if issue == "unreadable":
        return ("This file could not be opened as a PDF -- it may be damaged or "
                "password-protected. Upload it again, or export a fresh copy.")
    if issue == "scanned":
        return (doc.get("evidence") or "").strip() or (
            "This PDF has no selectable text (it looks like a scan or a photo), "
            "so it cannot be read. Upload the digital PDF instead.")
    return (
        "This document wasn't recognised as a ledger, trial balance, Form 26AS, "
        "ITR document or GSTR return, so there is nothing to analyse. "
        + (doc.get("evidence") or "")
    ).strip()


def submit_analysis():
    """Queue "Re-run Analysis". A request already waiting in line absorbs
    this one (it will see the same files); one already running does not."""
    return jobs.submit("analysis", "all", "Analysing documents", _analysis_work(), coalesce=True)


def submit_ledger(path):
    """Queue full processing of one ledger PDF (the established baseline
    ledgers are refused by process_ledger_document()'s own guard and
    come back as a failed job with that explanation)."""
    name = os.path.basename(path)
    return jobs.submit("ledger", path, name, _ledger_work(path), coalesce=True)


def submit_upload(path):
    """Queue whatever the document at `path` needs, decided by content."""
    name = os.path.basename(path)

    def work(report):
        report("Reading document", detail="scanning")
        import source_resolver
        doc = source_resolver.classify_cached(path)
        doctype = doc["doctype"]
        if doctype == "ledger":
            return _ledger_work(path)(report)
        if doctype in ("form_26as", "itr_computation", "itr_acknowledgement",
                       "trial_balance", "gstr1", "gstr3b"):
            analysis_id = submit_analysis()
            return {
                "kind": "routed", "ok": True, "filename": name, "doctype": doctype,
                "analysis_job": analysis_id,
            }
        return {
            "kind": "unrecognized", "ok": False, "filename": name, "doctype": doctype,
            "stage": "classify", "issue": doc.get("issue"),
            "error": unreadable_message(doc),
        }

    return jobs.submit("upload", path, name, work, coalesce=True)
