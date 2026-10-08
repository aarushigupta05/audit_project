"""
dashboard/data.py
------------------
UI-agnostic data-loading and transform layer for the Streamlit dashboard.

Deliberately kept free of any `import streamlit` or rendering calls: every
function here takes plain arguments (or nothing) and returns plain Python
data (dicts, lists, pandas DataFrames) so it can be exercised directly by
pytest (see tests/test_dashboard_data.py) without spinning up a Streamlit
app. dashboard/app.py is the only module that imports streamlit and calls
these functions to render them.

Two JSON sources, both produced by the pipeline's own rule scripts:
  - output/combined_report.json  <- scripts/combined_report.py
      (Rules 1/2/3/4/5/6: ledger-leg flags, Benford, ITR/26AS, trial balance)
  - output/gstr_report.json      <- scripts/rule_gstr_reconciliation.py
      (kept separate deliberately -- see that module's docstring: different,
      non-overlapping period from the ledger, so merging it into the same
      KPIs/table would misleadingly imply they share a timeframe)

Neither file is guaranteed to exist the first time the dashboard is opened
(e.g. a fresh checkout before anyone has run the pipeline), so every loader
returns a clear "not generated yet" shape instead of raising, and the
re-run helpers (`run_combined_pipeline`, `run_gstr_pipeline`) let the UI
generate them on demand.
"""
import csv
import os
import re
import sys
from collections import Counter, defaultdict

import pandas as pd
import json as _json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
RAW_PDFS_DIR = os.path.join(DATA_DIR, "raw_pdfs")

COMBINED_REPORT_PATH = os.path.join(OUTPUT_DIR, "combined_report.json")
GSTR_REPORT_PATH = os.path.join(OUTPUT_DIR, "gstr_report.json")
# The person's own uploaded GSTR-1 / GSTR-3B, checked against each other
# separately from the sample returns above -- see run_gstr_pipeline().
GSTR_UPLOADED_REPORT_PATH = os.path.join(OUTPUT_DIR, "gstr_report__uploaded.json")
SEGMENTATION_REPORT_PATH = os.path.join(OUTPUT_DIR, "benford_segmentation.json")
# FY2025-26's own ledger-only report (Rules 1/2/3/5/7/8 -- see
# combined_report.run_ledger_only_report()) and the ledger-vs-GSTR-1
# reconciliation report (rule_ledger_gstr_reconciliation.run_ledger_gstr_report())
# -- both added 2026-10-04: this data existed in output/ already (every
# figure below was computed and tested) but had no dashboard tab at all
# until this fix, so it was invisible to anyone using the UI instead of
# reading JSON by hand.
FY2025_26_REPORT_PATH = os.path.join(OUTPUT_DIR, "combined_report_FY2025-26.json")
LEDGER_GSTR_REPORT_PATH = os.path.join(OUTPUT_DIR, "ledger_gstr_reconciliation_report.json")
# One report per ledger the person has processed (written next to the
# established one above): output/ledger_gstr__<slug>.json -- see
# run_ledger_gstr_pipeline().
LEDGER_GSTR_UPLOAD_PREFIX = "ledger_gstr__"
# Tax & Compliance results built from whatever tax documents are on file,
# chosen by content (scripts/tax_compliance_report.py) -- see
# run_tax_compliance_pipeline() below.
TAX_COMPLIANCE_REPORT_PATH = os.path.join(OUTPUT_DIR, "tax_compliance_report.json")

# Raw ledger + its upstream pipeline artifacts, used by finding_detail() (to
# join a flagged leg back to its full raw row) and pipeline_status() (to
# show which pipeline stages have produced output on disk).
GENERAL_LEDGER_PATH = os.path.join(DATA_DIR, "general_ledger.csv")
RECONSTRUCTED_ENTRIES_PATH = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_entries.csv")
FORM26AS_PATH = os.path.join(DATA_DIR, "form26as.csv")
ITR_SUMMARY_PATH = os.path.join(DATA_DIR, "itr_summary.csv")
FY2025_26_GL_PATH = os.path.join(DATA_DIR, "general_ledger_FY2025-26.csv")

# The two real ledger PDFs this project knows how to fully reconstruct end
# to end, and each one's known, already-investigated unbalanced-voucher
# count (see BASELINE_CHECKPOINT_V3.13.md/V3.9.md) -- the dashboard's
# "Re-run full ledger reconstruction" button compares a fresh run's count
# against this baseline so a jump is a visible warning, not a silent
# surprise. Keyed by PDF basename (no extension), matching
# reconstruct_ledger_entries.py's own is_frozen_baseline check.
# NOTE on genericization (2026-10-04): this dict is NOT where the dashboard's
# ledger tabs come from anymore -- see discover_ledger_documents()/
# process_ledger_document()/ledger_tab_info() below, which work against
# WHATEVER ledger-shaped PDF is actually sitting in data/raw_pdfs/, content-
# classified via scripts/classify_document.py, not against this dict's keys.
# This dict still exists, unchanged in shape, for two reasons that are both
# about NOT regressing already-pinned behavior, not about the dashboard
# still being hardcoded to these two files:
#   1. run_full_ledger_reconstruction(pdf_basename) (below) and several
#      pinned tests (tests/test_dashboard_data.py) key off it directly --
#      changing its shape would be a large-blast-radius rename for no
#      functional gain.
#   2. It's this project's registry of the two documents with an
#      established, human-chosen display label and a long-settled output
#      path/known-unbalanced baseline -- ledger_tab_info() below consults
#      it (by content-derived slug, never by assuming these specific
#      files exist) so those two tabs keep their existing nice labels and
#      existing report files instead of being relabeled by date-range
#      auto-detection, which would be a cosmetic regression for no reason.
# "report_path"/"report_label" added this phase (additive -- no existing
# key removed or renamed) so ledger_tab_info() can find each one's already-
# existing combined_report*.json without hardcoding either filename a
# second time somewhere else.
LEDGER_PDF_INFO = {
    "ledgers_redacted": {
        "label": "FY2024-25 (frozen baseline)",
        "pdf_path": os.path.join(RAW_PDFS_DIR, "ledgers_redacted.pdf"),
        "output_csv": GENERAL_LEDGER_PATH,
        "known_unbalanced": 2,
        "is_frozen": True,
        "report_path": COMBINED_REPORT_PATH,
    },
    "Ledgers_Anonymised": {
        "label": "FY2025-26",
        "pdf_path": os.path.join(RAW_PDFS_DIR, "Ledgers_Anonymised.pdf"),
        "output_csv": FY2025_26_GL_PATH,
        "known_unbalanced": 3,
        "is_frozen": False,
        "report_label": "FY2025-26",
        "report_path": FY2025_26_REPORT_PATH,
    },
}

# The frozen FY2024-25 baseline has its own much richer, dedicated pipeline
# (run_combined_pipeline() -> Rules 1-8 plus 4/6, its own Overview-anchored
# tab set) and must never be silently re-processed through the generic
# ledger-only path below under a second/different label -- see
# process_ledger_document()'s own guard.
FROZEN_BASELINE_SLUG = "ledgers_redacted"

# Human-readable names for classify_document()'s DOCTYPES, shown in the
# dashboard's Document Library -- mirrors RULE_DISPLAY_NAMES' convention of
# keeping the internal code (useful when reading the scripts together) out
# of the UI entirely.
DOCTYPE_DISPLAY_NAMES = {
    "ledger": "Ledger (Tally export)",
    "trial_balance": "Trial Balance",
    "form_26as": "Form 26AS (Annual Tax Statement)",
    "itr_computation": "ITR Computation of Income",
    "itr_acknowledgement": "ITR-V Acknowledgement",
    "gstr1": "GSTR-1",
    "gstr3b": "GSTR-3B",
    "unknown": "Unrecognized",
}

# Which generic analysis a classified document type feeds into -- added
# 2026-10-04 for the sidebar architecture correction's point 7 ("show
# specialized checks dynamically after classification rather than
# hardcoding them into the sidebar"). Purely a display label: shown next
# to a document in the Process Documents tab's "already on file" table so
# a person can see what a non-ledger upload is FOR without needing to
# know this project's internal pipeline/script names. Never "FY2025-26"
# or "GSTR-1 pipeline" -- the same generic vocabulary run_full_analysis()
# itself reports under (see its own _ANALYSIS_STEPS names above).
DOCTYPE_ANALYSIS_USE = {
    "ledger": "Ledger analysis",
    "trial_balance": "Tax & ledger reconciliation",
    "form_26as": "Tax & ledger reconciliation",
    "itr_computation": "Tax & ledger reconciliation",
    "itr_acknowledgement": "Tax & ledger reconciliation",
    "gstr1": "GST reconciliation",
    "gstr3b": "GST reconciliation",
    "unknown": "--",
}

ALLOWED_UPLOAD_EXTENSIONS = {".pdf"}
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}

# Human-readable names for the per-leg rule codes stored in
# ledger_leg_flags[*]["reasons"][*]["rule"] (see scripts/combined_report.py).
# The dashboard shows these instead of the bare "rule_segregation_of_duties"-
# style codes or the old "Rule 1"/"Rule 2" numbering -- a reader shouldn't
# need to know the codebase's internal rule numbers to understand what
# fired. The numbers still exist in the code (useful for us when reading
# scripts/combined_report.py together) but are never shown in the UI.
RULE_DISPLAY_NAMES = {
    "segregation_of_duties": "Segregation of Duties",
    "round_number_bias": "Round-Number Bias",
    "duplicate_transaction": "Duplicate Transaction",
}

# Parses the voucher IDs a duplicate_transaction reason names out of its own
# generated sentence, e.g. "Duplicate transaction candidate: matches
# voucher(s) Journal_598, Journal_636 on 31-Mar-25 (...)" -> ["Journal_598",
# "Journal_636"]. Matches the exact f-string combined_report.py's
# rule_duplicate_transactions() builds that sentence from (see that
# function's "other_vchs" join) -- verified against all 215 real
# duplicate_transaction reasons in output/combined_report.json before being
# relied on here (see tests/test_dashboard_data.py). If that sentence's
# wording ever changes, this stops matching and related-transaction lookups
# just come back empty -- it never raises or shows a wrong voucher.
_DUPLICATE_VOUCHER_RE = re.compile(r"matches voucher\(s\) (.+?) on ")

# combined_report.py and rule_gstr_reconciliation.py live in scripts/ and
# import each other via a relative sys.path insert of their own directory;
# make scripts/ importable from here the same way for run_*_pipeline().
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE"]

# Brightened for legibility against the dashboard's dark theme (near-black
# background) -- the original values were tuned for a light background and
# read as muddy/low-contrast on dark, which matters for an audit tool where
# severity is the single most important signal on the page.
SEVERITY_COLORS = {
    "CRITICAL": "#f87171",
    "HIGH": "#fb923c",
    "MEDIUM": "#fbbf24",
    "LOW": "#4ade80",
    "NONE": "#94a3b8",
}


def _load_json(path):
    """Returns the parsed JSON dict, or None if the file doesn't exist yet.
    Never raises on a missing file -- that's a normal "pipeline hasn't been
    run yet" state the UI needs to handle, not an error."""
    if not os.path.exists(path):
        return None
    # A report can be mid-write when this runs (analysis now happens on a
    # background thread while the page keeps rendering). One short retry
    # covers a write in progress; a file that is still unreadable after
    # that is treated like a missing one -- "not generated yet" -- rather
    # than crashing the page with a JSONDecodeError.
    for attempt in range(2):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except ValueError:
            if attempt == 0:
                import time as _time
                _time.sleep(0.25)
    return None


def load_combined_report(path=COMBINED_REPORT_PATH):
    return _load_json(path)


def load_gstr_report(path=GSTR_REPORT_PATH):
    return _load_json(path)


def load_gstr_reports(output_dir=None):
    """Every GSTR-1 vs GSTR-3B report on disk as [{"label", "slug",
    "report"}]: the built-in sample returns' first, then the person's own
    uploaded returns. They are never merged -- see run_gstr_pipeline()."""
    output_dir = output_dir or OUTPUT_DIR
    entries = []
    for slug, default_label, path in (
        ("sample", "Sample returns", os.path.join(output_dir, os.path.basename(GSTR_REPORT_PATH))),
        ("uploaded", "Your uploaded returns",
         os.path.join(output_dir, os.path.basename(GSTR_UPLOADED_REPORT_PATH))),
    ):
        report = _load_json(path)
        if report is None:
            continue
        label = (report.get("dataset") or {}).get("label") or default_label
        entries.append({"label": label, "slug": slug, "report": report})
    return entries


def newest_report(entries):
    """The most recently generated report among load_*_reports() entries,
    or None -- what the sidebar's "last run" line reads."""
    reports = [e["report"] for e in entries if e.get("report")]
    return max(reports, key=lambda r: r.get("generated_at") or "", default=None)


def load_segmentation_report(path=SEGMENTATION_REPORT_PATH):
    return _load_json(path)


def load_fy2025_26_report(path=FY2025_26_REPORT_PATH):
    """FY2025-26's own ledger-only report (Rules 1/2/3/5/7/8 against
    data/general_ledger_FY2025-26.csv -- see combined_report.py's
    run_ledger_only_report()). Added 2026-10-04: this file has existed in
    output/ since Rule 8 was built, but nothing in the dashboard ever
    loaded it -- the whole FY2025-26 ledger's own intrinsic checks were
    invisible to anyone using the UI instead of reading JSON by hand."""
    return _load_json(path)


def load_tax_compliance_report(path=TAX_COMPLIANCE_REPORT_PATH):
    """output/tax_compliance_report.json (ITR vs 26AS + trial balance vs
    ledger, built from whatever tax documents are on file -- see
    scripts/tax_compliance_report.py), or None before the first analysis
    run. A corrupt/unreadable file also comes back None rather than
    raising: the dashboard treats that exactly like "not generated yet"."""
    try:
        return _load_json(path)
    except (ValueError, OSError):
        return None


def load_ledger_gstr_report(path=LEDGER_GSTR_REPORT_PATH):
    """The ledger-vs-GSTR-1 taxable-supply reconciliation report (see
    rule_ledger_gstr_reconciliation.run_ledger_gstr_report()) -- the check
    that resolved this project's original motivating question (why ledger
    Sales Dr/Cr didn't look right against the filed returns). Added
    2026-10-04 for the same reason as load_fy2025_26_report() above: this
    file existed and was fully tested, but had no dashboard loader at
    all."""
    return _load_json(path)


def load_ledger_gstr_reports(output_dir=None):
    """Every ledger-vs-GSTR-1 report on disk, as [{"label", "slug",
    "report"}]: the established FY2025-26 sample ledger's first (when its
    report exists), then one per ledger the person has processed, by label.
    Reports that cannot be read are left out."""
    output_dir = output_dir or OUTPUT_DIR
    entries = []
    base = _load_json(os.path.join(output_dir, os.path.basename(LEDGER_GSTR_REPORT_PATH)))
    if base is not None:
        label = (base.get("dataset") or {}).get("ledger_label") \
            or LEDGER_PDF_INFO["Ledgers_Anonymised"]["label"]
        entries.append({"label": label, "slug": "Ledgers_Anonymised", "report": base})
    uploaded = []
    if os.path.isdir(output_dir):
        for name in sorted(os.listdir(output_dir)):
            if not (name.startswith(LEDGER_GSTR_UPLOAD_PREFIX) and name.endswith(".json")):
                continue
            report = _load_json(os.path.join(output_dir, name))
            if report is None:
                continue
            slug = name[len(LEDGER_GSTR_UPLOAD_PREFIX):-len(".json")]
            label = (report.get("dataset") or {}).get("ledger_label") or slug
            uploaded.append({"label": label, "slug": slug, "report": report})
    entries.extend(sorted(uploaded, key=lambda e: e["label"].lower()))
    return entries


def run_combined_pipeline():
    """Re-runs the FULL ledger+tax pipeline fresh: re-adapts the real tax
    documents (data/raw_pdfs/annual_tax_statement_redacted.pdf,
    income_computation_redacted.pdf, income_tax_acknowledgement_redacted.pdf
    -> data/form26as.csv + data/itr_summary.csv via
    adapt_tax_docs_to_schema.adapt_tax_docs()), THEN runs
    scripts/combined_report.py's run_combined_report() against the result
    (also re-writing output/combined_report.json, same as running the
    script directly).

    FIXED 2026-10-04 (v1 finalization, follow-up): this function used to
    call ONLY run_combined_report(), which reads data/form26as.csv and
    data/itr_summary.csv directly -- so re-uploading a replacement tax
    document via the sidebar uploader and clicking "Re-run ledger + tax
    pipeline" silently did nothing for Rule 4 (ITR vs 26AS), despite the
    uploader's own caption promising it would be "picked up the next time
    you re-run the relevant pipeline". adapt_tax_docs() uses FIXED
    filenames (see its own INPUT_26AS/INPUT_COI/INPUT_ACK constants) --
    re-uploading under the exact same filename is what makes a
    replacement actually get picked up here; a differently-named file is
    saved but not read by anything. Verified this call reproduces the
    currently-committed form26as.csv/itr_summary.csv byte-for-byte when
    the real raw PDFs haven't changed, before wiring it in here.

    Trial balance (Rule 6) needed NO equivalent fix: check_trial_balance_
    reconciliation() already re-parses data/raw_pdfs/trial_balance_redacted.pdf
    fresh on every call (see reconcile_trial_balance.reconcile()'s
    tb_rows=None default), so a same-named replacement already works
    without this function doing anything extra.

    The general ledger itself (the two Tally PDF exports) is deliberately
    NOT re-extracted here -- see run_combined_pipeline's module-level note
    in dashboard/app.py's uploader: reconstruct_ledger_entries.py needs a
    human-supplied start_page/end_page for a given PDF (there's no way to
    discover the right page range for an arbitrary new ledger PDF without
    a person looking at it first), so replacing the ledger is a manual,
    separate step, not something this button can safely automate.

    Imported lazily inside the function, not at module load time, so
    importing dashboard.data doesn't require the pipeline's own heavy deps
    (pdfplumber etc.) to already be importable/working just to show a
    stale cached report."""
    import adapt_tax_docs_to_schema
    adapt_tax_docs_to_schema.adapt_tax_docs()
    import combined_report
    return combined_report.run_combined_report()


def gstr_paths_by_origin(documents):
    """GSTR-1 and GSTR-3B documents on file, split by where they came from:
    {"sample": {"gstr1": [...], "gstr3b": [...]}, "uploaded": {...}}. A
    document is a sample when its name is one of PROTECTED_UPLOAD_NAMES (the
    files this project ships with); everything else was uploaded."""
    import source_resolver
    gstr1, gstr3b = source_resolver.gst_source_paths(documents)
    out = {"sample": {"gstr1": [], "gstr3b": []}, "uploaded": {"gstr1": [], "gstr3b": []}}
    for kind, paths in (("gstr1", gstr1), ("gstr3b", gstr3b)):
        for p in paths:
            origin = "sample" if os.path.basename(p) in PROTECTED_UPLOAD_NAMES else "uploaded"
            out[origin][kind].append(p)
    return out


def run_gstr_pipeline():
    """GSTR-1 vs GSTR-3B (scripts/rule_gstr_reconciliation.py), run on two
    separate sets that are never mixed:

      - the built-in SAMPLE returns, adapted to data/gstr1_summary.csv and
        data/gstr3b_summary.csv and reported to output/gstr_report.json --
        exactly what this function produced before there were uploads;
      - the person's UPLOADED returns, compared with each other and reported
        to output/gstr_report__uploaded.json. A sample return is never used
        for an uploaded business, even for a month the sample also covers.
        For this set a month present in only one of the two documents is
        listed as "not compared", never flagged as a missing filing (not
        having uploaded a return is not evidence it was never filed).

    Documents are chosen by CONTENT (source_resolver.gst_source_paths()), so
    any filename works, and both shapes (one month per file, or a whole-year
    bundle) are read; see adapt_gstr_to_schema. A file that cannot be read is
    skipped and noted in the report (and GST_LAST_WARNINGS) rather than
    blocking every other month. Raises FileNotFoundError -- reported by
    run_full_analysis() as "skipped", not "failed" -- when there is nothing
    to check in either set.

    History: FIXED 2026-10-04 so this re-adapts the PDFs instead of only
    re-running the rule over stale CSVs; generalized from filename globs to
    content-based selection 2026-10-07; split by origin 2026-10-07 (it used
    to merge every GSTR document on file, so an uploaded business's month
    could be silently replaced by the sample's)."""
    import source_resolver
    import adapt_gstr_to_schema
    import rule_gstr_reconciliation as rg
    documents = source_resolver.scan_documents(raw_dir=RAW_PDFS_DIR)
    groups = gstr_paths_by_origin(documents)
    warnings_all = []
    last = None

    sample = groups["sample"]
    if sample["gstr1"] and sample["gstr3b"]:
        _, _, warnings = adapt_gstr_to_schema.adapt_gstr_documents(sample["gstr1"], sample["gstr3b"])
        warnings_all += [f"{w} (sample returns)" for w in warnings]
        last = rg.run_gstr_report(
            label="Sample returns",
            gstr1_sources=[os.path.basename(p) for p in sample["gstr1"]],
            gstr3b_sources=[os.path.basename(p) for p in sample["gstr3b"]],
            warnings=warnings,
        )

    up = groups["uploaded"]
    if up["gstr1"] or up["gstr3b"]:
        rows1, w1 = adapt_gstr_to_schema.extract_gstr1_rows(up["gstr1"])
        rows3, w3 = adapt_gstr_to_schema.extract_gstr3b_rows(up["gstr3b"])
        warnings = w1 + w3
        warnings_all += [f"{w} (your uploads)" for w in warnings]
        last = rg.run_gstr_report(
            gstr1_rows=rows1, gstr3b_rows=rows3, out_path=GSTR_UPLOADED_REPORT_PATH,
            label="Your uploaded returns",
            gstr1_sources=[os.path.basename(p) for p in up["gstr1"]],
            gstr3b_sources=[os.path.basename(p) for p in up["gstr3b"]],
            warnings=warnings, missing_is_filing_gap=False,
        )

    GST_LAST_WARNINGS[:] = warnings_all
    if last is None:
        raise FileNotFoundError("No GSTR-1 or GSTR-3B document has been uploaded.")
    return last


# Files the last run_gstr_pipeline() had to skip (unreadable / unfamiliar
# layout), as human-readable strings -- shown by the dashboard so a skipped
# month is never silent. Cleared and refilled on every run.
GST_LAST_WARNINGS = []


def run_segmentation_pipeline():
    """Re-runs scripts/analyze_benford_segmentation.py's
    run_segmentation_report() fresh. Cheap (pure CSV math over the
    general_ledger.csv rows, no PDF parsing), so the dashboard calls this
    directly rather than gating it behind a sidebar button like the two
    pipelines above -- see benford_segmentation_summary()."""
    import analyze_benford_segmentation
    return analyze_benford_segmentation.run_segmentation_report()


def run_fy2025_26_pipeline():
    """Re-runs the FY2025-26 ledger-only report (Rules 1/2/3/5/7/8) fresh
    against data/general_ledger_FY2025-26.csv, via combined_report.py's
    run_ledger_only_report() -- the same function that produced
    output/combined_report_FY2025-26.json in the first place. Added
    2026-10-04 so the FY2025-26 tab can be refreshed from the sidebar
    exactly like the FY2024-25 pipeline, instead of that report only ever
    being generated by running a script by hand.

    Deliberately does NOT re-run reconstruct_ledger_entries.py first --
    this re-derives flags from whatever data/general_ledger_FY2025-26.csv
    currently contains. To refresh the CSV itself from the raw PDF, use
    run_full_ledger_reconstruction() below."""
    import combined_report
    return combined_report.run_ledger_only_report(FY2025_26_GL_PATH, "FY2025-26")


def gstr1_paths_by_origin(documents):
    """The GSTR-1 documents on file, split into ([built-in sample returns],
    [the person's own uploads]) -- a document is built-in when its name is
    one of PROTECTED_UPLOAD_NAMES (the files this project ships with). Used
    to pair a ledger only with returns of its own origin: the sample ledger
    with the sample returns, an uploaded ledger with uploaded returns --
    never an uploaded ledger against another business's sample returns."""
    import source_resolver
    gstr1_paths, _ = source_resolver.gst_source_paths(documents)
    built_in = [p for p in gstr1_paths if os.path.basename(p) in PROTECTED_UPLOAD_NAMES]
    uploaded = [p for p in gstr1_paths if os.path.basename(p) not in PROTECTED_UPLOAD_NAMES]
    return built_in, uploaded


def processed_ledgers_for_gst():
    """The ledgers the person has processed (the generic, non-established
    ones) as [{"slug", "label", "csv_path"}], from their saved metadata."""
    import glob
    found = []
    for meta_path in sorted(glob.glob(os.path.join(OUTPUT_DIR, "ledger_meta__*.json"))):
        meta = _load_json(meta_path)
        if not meta or not meta.get("ok") or meta.get("slug") in LEDGER_PDF_INFO:
            continue
        csv_path = meta.get("output_csv")
        if not csv_path or not os.path.exists(csv_path):
            continue
        found.append({
            "slug": meta.get("slug") or os.path.basename(meta_path),
            "label": f"{meta.get('label', 'Ledger')} ({meta.get('filename', meta.get('slug', ''))})",
            "csv_path": csv_path,
        })
    return found


def run_ledger_gstr_pipeline():
    """Ledger-vs-GSTR-1 reconciliation (rule_ledger_gstr_reconciliation),
    for the sample ledger AND every ledger the person has processed.

    Pairing, by where the documents came from (see gstr1_paths_by_origin()):
      - the established FY2025-26 sample ledger is checked against the
        built-in sample returns -- written to
        output/ledger_gstr_reconciliation_report.json, as before;
      - each processed ledger is checked against the GSTR-1 documents the
        person uploaded -- written to output/ledger_gstr__<slug>.json. With
        none uploaded, the report says so (status "no_returns") instead of
        comparing against another business's returns.
    Generalised 2026-10-07: this used to read only the FY2025-26 sample
    ledger and data/gstr1_summary.csv.

    Raises FileNotFoundError ("skipped") only when there is nothing at all
    to do: no sample ledger and no processed ledger."""
    import source_resolver
    import adapt_gstr_to_schema
    import rule_ledger_gstr_reconciliation as lgr

    documents = source_resolver.scan_documents(raw_dir=RAW_PDFS_DIR)
    built_in, uploaded = gstr1_paths_by_origin(documents)
    last = None
    ran = False

    if os.path.exists(FY2025_26_GL_PATH):
        info = LEDGER_PDF_INFO["Ledgers_Anonymised"]
        if built_in:
            rows, warnings = adapt_gstr_to_schema.extract_gstr1_rows(built_in)
            sources = [os.path.basename(p) for p in built_in]
            last = lgr.run_ledger_gstr_report(
                gl_file=FY2025_26_GL_PATH, gstr1_rows=rows, ledger_label=info["label"],
                gstr1_sources=sources, gstr1_warnings=warnings,
            )
            ran = True
        elif not uploaded and os.path.exists(lgr.GSTR1_FILE):
            # A checkout without the sample return PDFs but with the
            # already-extracted summary (and no uploads that could have been
            # merged into it): the original behaviour.
            last = lgr.run_ledger_gstr_report(ledger_label=info["label"])
            ran = True

    if processed_ledgers_for_gst():
        rows, warnings = adapt_gstr_to_schema.extract_gstr1_rows(uploaded)
        sources = [os.path.basename(p) for p in uploaded]
        failures = []
        for led in processed_ledgers_for_gst():
            out_path = os.path.join(OUTPUT_DIR, f"{LEDGER_GSTR_UPLOAD_PREFIX}{led['slug']}.json")
            try:
                last = lgr.run_ledger_gstr_report(
                    gl_file=led["csv_path"], gstr1_rows=rows, out_path=out_path,
                    ledger_label=led["label"], gstr1_sources=sources, gstr1_warnings=warnings,
                )
                ran = True
            except Exception as e:  # noqa: BLE001 -- one bad ledger must not block the rest
                failures.append(f"{led['label']}: {e}")
        if failures:
            raise RuntimeError("Could not compare: " + "; ".join(failures))

    if not ran:
        raise FileNotFoundError("There is no ledger to compare with a GSTR-1 return.")
    return last


def tax_ledger_candidates():
    """Every ledger with a normalized CSV on disk -- the established ones
    plus any the person has processed -- as [{"label", "csv_path"}], for
    matching a trial balance to the ledger covering the same dates."""
    candidates, seen = [], set()
    for info in LEDGER_PDF_INFO.values():
        path = info["output_csv"]
        if os.path.exists(path) and path not in seen:
            candidates.append({"label": info["label"], "csv_path": path})
            seen.add(path)
    if os.path.isdir(OUTPUT_DIR):
        for name in sorted(os.listdir(OUTPUT_DIR)):
            if not (name.startswith("ledger_meta__") and name.endswith(".json")):
                continue
            meta = _load_json(os.path.join(OUTPUT_DIR, name))
            if not meta or not meta.get("ok"):
                continue
            path = meta.get("output_csv")
            if path and os.path.exists(path) and path not in seen:
                candidates.append({
                    "label": f"{meta.get('label', 'ledger')} ({meta.get('filename', name)})",
                    "csv_path": path,
                })
                seen.add(path)
    return candidates


def run_tax_compliance_pipeline():
    """Builds output/tax_compliance_report.json from whatever tax documents
    are on file (chosen by content -- scripts/tax_compliance_report.py).
    Always writes a report: a check that cannot run says why in its own
    status, so the dashboard can show "needs a Computation of Income for AY
    2025-26" instead of nothing. Raises FileNotFoundError only when there is
    nothing tax-related on file at all (reported as "skipped")."""
    import source_resolver
    import tax_compliance_report
    documents = source_resolver.scan_documents(raw_dir=RAW_PDFS_DIR)
    report = tax_compliance_report.run_tax_compliance_report(
        documents=documents, ledger_candidates=tax_ledger_candidates(),
    )
    if report["itr_vs_26as"]["status"] == "none" and report["trial_balance"]["status"] == "none":
        raise FileNotFoundError(
            "No Form 26AS, ITR document or trial balance has been uploaded."
        )
    return report


def run_tax_pipeline():
    """The "Tax & ledger reconciliation" step of run_full_analysis():
    (1) the content-based Tax & Compliance report -- what the dashboard's
    Tax Reconciliation tab shows -- then (2) the established FY2024-25
    baseline pipeline (run_combined_pipeline(), which keeps
    output/combined_report.json fresh), best effort: the baseline's own
    fixed source files being absent is NOT an error when the person's own
    documents were analysed in (1). Raises FileNotFoundError only when
    neither found anything to do."""
    tax_error = None
    report = None
    try:
        report = run_tax_compliance_pipeline()
    except FileNotFoundError as e:
        tax_error = e
    try:
        run_combined_pipeline()
    except (FileNotFoundError, ValueError):
        # The baseline's fixed source files are missing, or have been
        # replaced by documents of a different shape (ValueError from its
        # extractors). Only fatal when the content-based report above had
        # nothing to analyse either; ImportError (a missing package) is
        # deliberately NOT caught -- that is a setup problem to surface.
        if report is None:
            raise tax_error
    return report


# Generic display name -> underlying pipeline function, in the order
# they're attempted. Used only by run_full_analysis() below; the four
# individual functions above are UNCHANGED and still each independently
# callable exactly as before (nothing here replaces or wraps their own
# behavior, it only decides WHEN to call them).
def refresh_processed_ledger_reports():
    """Re-derives the audit report of every ledger the person has processed
    (the generic, non-established ones listed in Audit's picker) from its
    saved, already-adapted CSV, with the CURRENT rules. Added 2026-10-07:
    Re-run Analysis used to refresh only the two established documents, so
    a ledger processed earlier kept showing results computed by older rules
    (a rule change never reached it until the document was fully
    re-processed from the PDF). No PDF work happens here -- it is the same
    run_ledger_only_report() call process_ledger_document() makes, on the
    CSV that call already wrote -- so it takes seconds.

    Returns the list of report labels refreshed. A ledger whose CSV is
    gone is skipped; one that fails does not stop the others -- failures
    are raised together at the end so the step is reported honestly."""
    import glob
    import combined_report
    refreshed, failures = [], []
    for meta_path in sorted(glob.glob(os.path.join(OUTPUT_DIR, "ledger_meta__*.json"))):
        meta = _load_json(meta_path)
        if not meta or not meta.get("ok"):
            continue
        if meta.get("slug") in LEDGER_PDF_INFO:
            continue  # the established documents have their own pipelines
        csv_path, label = meta.get("output_csv"), meta.get("report_label")
        if not csv_path or not label or not os.path.exists(csv_path):
            continue
        try:
            combined_report.run_ledger_only_report(csv_path, label)
            refreshed.append(label)
        except Exception as e:  # noqa: BLE001 -- one bad ledger must not block the rest
            failures.append(f"{label}: {e}")
    if failures:
        raise RuntimeError("Could not refresh: " + "; ".join(failures))
    return refreshed


def run_ledger_analysis_refresh():
    """The "Ledger analysis refresh" step: every processed ledger's report,
    then the established FY2025-26 baseline. If the baseline's source file
    is absent but processed ledgers were refreshed, that is still a
    successful step (a deployment with only the person's own documents)."""
    refreshed = refresh_processed_ledger_reports()
    try:
        run_fy2025_26_pipeline()
    except FileNotFoundError:
        if not refreshed:
            raise


_ANALYSIS_STEPS = [
    ("Tax & ledger reconciliation", run_tax_pipeline),
    ("GST reconciliation", run_gstr_pipeline),
    ("Ledger analysis refresh", run_ledger_analysis_refresh),
    ("Ledger vs GST reconciliation", run_ledger_gstr_pipeline),
]


def run_full_analysis(on_step=None):
    """The single generic "Re-run Analysis" entry point that replaces the
    four separate, dataset-specific sidebar buttons this project used to
    show (Re-run ledger + tax pipeline / Re-run GSTR pipeline / Re-run
    FY2025-26 ledger pipeline / Re-run Ledger vs GSTR-1 pipeline) --
    architecture correction made 2026-10-04 after direct feedback that
    naming "FY2025-26"/"GSTR-1" as primary UI actions is development-
    dataset-specific, not representative of "a generalized audit
    inconsistency detection portal" a future user with entirely different
    documents would land on.

    Orchestration only -- calls the same four run_*_pipeline() functions
    above completely unchanged, in the same order, with no change to what
    any of them does or how they do it; no audit/reconciliation/
    classification logic anywhere in this project was touched to build
    this. What changes is WHO decides which ones run: previously, the
    person had to already know which of four named buttons corresponded
    to whichever documents they'd uploaded; now this function finds out
    for itself, by simply trying each one and reading the result:
      - a pipeline whose required source documents aren't present raises
        FileNotFoundError from deep inside it (every one of the four
        already does this today, on a plain missing-file open() or an
        explicit check -- see e.g. adapt_gstr_to_schema.adapt_gstr_docs()'s
        own "No GSTR-1 PDFs found matching..." -- nothing added here to
        make that happen) -- caught here and reported as "skipped", not a
        failure: a future user with no GSTR documents at all will
        correctly see GST reconciliation skipped, never an error.
      - any OTHER exception is a real failure, reported as such.
      - specifically an ImportError/ModuleNotFoundError (a missing
        optional dependency, e.g. the 2026-10-04 "No module named
        'sklearn'" incident -- see requirements.txt's own comment) is
        additionally flagged is_dependency_error=True, so the caller can
        show it as the application/setup problem it is, clearly separated
        from an audit finding, rather than a generic failure string.

    This also means one pipeline failing (including a missing dependency)
    can never silently block the others from running -- each is wrapped
    independently, unlike the old sidebar buttons where a person could
    only ever click one at a time anyway but each failure was reported in
    isolation with no sense of the other three's state.

    Returns {"ran": [...], "skipped": [...], "failed": [...]}, each a
    list of dicts with at least "name" (the generic display name from
    _ANALYSIS_STEPS above -- never "FY2025-26" or "GSTR-1"). "skipped"
    entries also carry "reason" (the FileNotFoundError's own message).
    "failed" entries carry "error" (str(exception)) and
    "is_dependency_error" (bool, see above).

    `on_step` (optional, added 2026-10-07): called as on_step(name) right
    before each step starts, so a background job can show which check is
    running. Omitting it changes nothing."""
    results = {"ran": [], "skipped": [], "failed": []}
    for name, fn in _ANALYSIS_STEPS:
        if on_step is not None:
            try:
                on_step(name)  # purely for a live progress display; never allowed to break the run
            except Exception:
                pass
        try:
            fn()
            results["ran"].append({"name": name})
        except FileNotFoundError as e:
            results["skipped"].append({"name": name, "reason": str(e)})
        except Exception as e:
            results["failed"].append({
                "name": name,
                "error": str(e),
                "is_dependency_error": isinstance(e, ImportError),
            })
    return results


def run_full_ledger_reconstruction(pdf_basename):
    """Re-extracts an ENTIRE real ledger PDF from scratch -- the step that
    was, until 2026-10-04, believed to require a human-chosen start_page/
    end_page and was therefore left out of every dashboard re-run button
    (see BASELINE_CHECKPOINT_V3.14.md section 5's "Deliberately NOT fixed"
    note). That belief turned out to be overly conservative: every real
    reconstruction run in this project's history has always processed the
    WHOLE document, auto-detectable via pdfplumber -- see
    reconstruct_ledger_entries.reconstruct_full_document()'s own
    docstring for the full reasoning. This function is the dashboard's
    safe entry point to that corrected capability.

    `pdf_basename` must be a key of LEDGER_PDF_INFO (the two real ledger
    PDFs this project knows how to fully reconstruct end to end --
    "ledgers_redacted" for the frozen FY2024-25 baseline, or
    "Ledgers_Anonymised" for FY2025-26). Raises KeyError with a clear
    message for anything else, rather than silently doing nothing.

    Runs, in order:
      1. reconstruct_ledger_entries.reconstruct_full_document(pdf_path) --
         re-parses the raw PDF into data/<basename>_reconstructed_entries.csv
         (and the matching _unresolved/_state files), auto-detecting the
         full page range.
      2. adapt_ledger_to_schema.adapt_ledger(input_path=<that entries csv>,
         output_path=LEDGER_PDF_INFO[pdf_basename]["output_csv"]) -- the
         same normalization step every other ledger CSV in this project
         goes through before any rule touches it.

    Returns a dict: {pdf_basename, label, is_frozen, total_pages,
    total_vouchers, unbalanced_count, known_unbalanced,
    unbalanced_count_changed, output_csv} -- `unbalanced_count_changed`
    is the dashboard's safety signal: True means this run's unbalanced-
    voucher count differs from LEDGER_PDF_INFO's known, already-
    investigated baseline, which is worth a human look before trusting
    the new CSV, not an error in itself (a genuinely new/different source
    PDF under the same basename would legitimately produce a different
    count).

    Does NOT re-run any rule engine itself -- call run_combined_pipeline()
    (for "ledgers_redacted") or run_fy2025_26_pipeline() (for
    "Ledgers_Anonymised") afterward to refresh the flags against the new
    CSV, same two-step convention the rest of this module already uses
    elsewhere (adapt, then report)."""
    if pdf_basename not in LEDGER_PDF_INFO:
        raise KeyError(
            f"{pdf_basename!r} is not a known ledger PDF. Expected one of "
            f"{sorted(LEDGER_PDF_INFO)} (see dashboard/data.py's LEDGER_PDF_INFO)."
        )
    info = LEDGER_PDF_INFO[pdf_basename]

    import reconstruct_ledger_entries
    recon_result = reconstruct_ledger_entries.reconstruct_full_document(info["pdf_path"])

    import adapt_ledger_to_schema
    adapt_ledger_to_schema.adapt_ledger(
        input_path=recon_result["entries_path"],
        output_path=info["output_csv"],
    )

    unbalanced_count = recon_result["unbalanced_count"]
    return {
        "pdf_basename": pdf_basename,
        "label": info["label"],
        "is_frozen": info["is_frozen"],
        "total_pages": recon_result["total_pages"],
        "total_vouchers": recon_result["total_vouchers"],
        "unbalanced_count": unbalanced_count,
        "known_unbalanced": info["known_unbalanced"],
        "unbalanced_count_changed": unbalanced_count != info["known_unbalanced"],
        "output_csv": info["output_csv"],
    }


def ledger_slug(pdf_path):
    """Stable key for a ledger PDF: its basename without extension -- the
    same convention LEDGER_PDF_INFO's keys and reconstruct_full_document()'s
    own is_frozen_baseline check already use
    (os.path.splitext(os.path.basename(...))[0]). Used throughout this
    module's generic-discovery code below so a document is always
    identified by this one derived value, never re-derived slightly
    differently in different places."""
    return os.path.splitext(os.path.basename(pdf_path))[0]


def ledger_meta_path(pdf_path):
    """Sidecar JSON path for a ledger document's last-processed metadata --
    output/ledger_meta__{slug}.json. Deliberately a SEPARATE file from
    combined_report*.json (whose shape dozens of existing tests and
    dashboard functions already pin) so recording confidence/label/date-
    range/processing-timestamp here can never change what that file looks
    like, and a missing/corrupt sidecar can never break anything that
    reads the report JSON."""
    return os.path.join(OUTPUT_DIR, f"ledger_meta__{ledger_slug(pdf_path)}.json")


# Where process_ledger_document() writes a given ledger's adapted,
# canonical-schema CSV. The two documents this project has fully verified
# and pinned dozens of tests/constants against (derived here from
# LEDGER_PDF_INFO, not duplicated) keep their EXACT existing filenames;
# any other ledger PDF -- a genuinely new upload -- gets a generic,
# slug-derived name instead, so it can never collide with either pinned
# file.
KNOWN_LEDGER_OUTPUT_PATHS = {
    slug: info["output_csv"] for slug, info in LEDGER_PDF_INFO.items()
}


def ledger_output_csv_path(pdf_path):
    """The general_ledger*.csv path a given ledger PDF's adapted output
    should be written to -- see KNOWN_LEDGER_OUTPUT_PATHS above."""
    slug = ledger_slug(pdf_path)
    if slug in KNOWN_LEDGER_OUTPUT_PATHS:
        return KNOWN_LEDGER_OUTPUT_PATHS[slug]
    return os.path.join(DATA_DIR, f"general_ledger__{slug}.csv")


def derive_period_label(date_range_start, date_range_end):
    """Turns a reconstructed ledger's own transaction date range into a
    display label, WITHOUT reading the PDF's filename -- the whole point
    of this phase's genericization is that a label like "FY2025-26" comes
    from what the document's own data says, not from what it happens to
    be called.

    - Indian financial year (1 Apr - 31 Mar) exactly -> "FY{yyyy}-{yy}"
      (e.g. 2025-04-01..2026-03-31 -> "FY2025-26"), matching this
      project's own existing convention for the two real documents it was
      built and verified against.
    - Any other range -> "DD Mon YYYY to DD Mon YYYY" -- deliberately no
      "/" anywhere in this fallback, so the label is always safe to use
      as a filename/report-label suffix (e.g.
      output/combined_report_{label}.json), unlike a "DD/MM/YYYY"-style
      format would be.
    - Either date missing (reconstruction found no parseable voucher date
      at all -- see reconstruct_full_document()'s own per-voucher
      try/except) -> "Unknown period". Never raises, never guesses a date
      that isn't actually in the data."""
    if not date_range_start or not date_range_end:
        return "Unknown period"
    from datetime import datetime
    try:
        start = datetime.strptime(date_range_start, "%Y-%m-%d")
        end = datetime.strptime(date_range_end, "%Y-%m-%d")
    except ValueError:
        return "Unknown period"
    if (start.month, start.day) == (4, 1) and (end.month, end.day) == (3, 31) \
            and end.year == start.year + 1:
        return f"FY{start.year}-{str(end.year)[-2:]}"
    return f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"


def discover_documents():
    """Classifies every PDF currently in data/raw_pdfs/ via
    scripts/classify_document.py's classify_document() -- content-based,
    not filename-based (see that module's own docstring for exactly why:
    this project's existing adapters all select files by NAME, which is
    the opposite of "upload a document and it gets tested"). Returns a
    list of dicts: {"filename", "path", "doctype", "evidence", "error"},
    sorted by filename.

    Never raises -- classify_document() itself never does (see its own
    docstring: even a corrupt/unreadable/zero-page PDF comes back
    "unknown" with the failure recorded as evidence, not an exception),
    and a raw_pdfs directory that doesn't exist yet just produces an
    empty list, the same "no data yet" convention the rest of this module
    already follows."""
    if not os.path.isdir(RAW_PDFS_DIR):
        return []
    import classify_document as _cd

    results = []
    for name in sorted(os.listdir(RAW_PDFS_DIR)):
        path = os.path.join(RAW_PDFS_DIR, name)
        if not os.path.isfile(path) or not name.lower().endswith(".pdf"):
            continue
        result = _cd.classify_document(path)
        results.append({"filename": name, "path": path, **result})
    return results


def discover_ledger_documents(documents=None):
    """The subset of discover_documents() classified as "ledger" -- the
    documents process_ledger_document() can act on and the dashboard's
    Audit ledger-picker (dashboard/app.py) builds its selectbox options
    from. Kept as its own function (rather than inlining the filter at
    each call site) since both app.py's picker and document_library()
    below need exactly this list.

    `documents`: pass discover_documents()'s own result to filter it
    in-memory instead of re-scanning data/raw_pdfs/ a second time.
    Optional and defaults to None (re-scans, exactly the original
    behavior) so every existing caller -- including every test that
    calls this with no arguments -- is unaffected. app.py passes its
    own single cached scan here (see cached_discover_documents()'s own
    docstring for why this mattered: on a real run, classify_document()
    scanning all of data/raw_pdfs/ -- including this project's two
    ~960-page ledger PDFs -- took several real seconds, and this
    function used to trigger that scan A SECOND TIME on every single
    page load, on top of document_library()'s own separate scan, for no
    reason other than neither call site knew the other had already done
    the same work this script run)."""
    docs = documents if documents is not None else discover_documents()
    return [d for d in docs if d["doctype"] == "ledger"]


def process_ledger_document(pdf_path, on_stage=None):
    """The dashboard's single generic entry point for "take a PDF, find
    out if/how well it works as a ledger" -- built 2026-10-04 for the
    upload-and-test workflow requested explicitly: the dashboard should
    show a ledger tab because a ledger-shaped document was found in
    data/raw_pdfs/, not because its filename is hardcoded into this
    project's source.

    NEVER RAISES. Every failure mode -- not ledger-shaped content, a
    reconstruction that hits something this parser has never seen, an
    adapter or report-generation error -- comes back as
    {"ok": False, "stage": ..., "error": ...} instead of an exception the
    caller has to guard against, so one bad uploaded document can never
    take down the whole dashboard. This is the direct implementation of
    "I don't want it to fail on any document": every external call inside
    is individually wrapped, and a failure at any stage stops cleanly with
    a clear, stage-labeled reason rather than a half-written CSV or a
    crashed Streamlit rerun.

    `on_stage` (added 2026-10-04, optional, default None): an optional
    callback invoked as `on_stage(stage_name)` right before each of the
    four real internal steps below begins -- "scanning" (classify),
    "extracting" (reconstruct), "auditing" (confidence + schema adapt +
    rule-engine report), "preparing" (sidecar metadata write) -- purely so
    a caller (dashboard/app.py) can show a live, honest multi-stage
    progress indicator instead of a single opaque "please wait" for the
    ~45-60s this function can take. This is strictly additive: every
    existing caller (including every test in
    tests/test_dashboard_data.py, which calls this with one positional
    arg) omits it, `on_stage` defaults to None, and behavior, return
    value, and timing are completely unchanged in that case -- the
    callback is never required to do anything and its own failure is
    swallowed (see `_emit_stage` below) so a broken UI callback can never
    take down processing, matching this function's own "NEVER RAISES"
    guarantee. No audit/classification/reconstruction/reconciliation/GST
    logic below was touched to add this -- every call into those modules
    is exactly as it was; `_emit_stage` calls are inserted around them,
    nothing inside them changed.

    Runs, in order:
      1. classify_document() -- confirms this is ledger-shaped content
         BEFORE attempting the expensive (~45-50s) full-document
         reconstruction at all. This is the "run is_ledger_document() to
         check it's ledger-shaped before attempting reconstruction" step
         requested explicitly.
      2. reconstruct_ledger_entries.reconstruct_full_document() -- the
         full-document PDF -> CSV reconstruction.
      3. assess_reconstruction_confidence() -- the honest HIGH/LOW
         verdict, calibrated against this project's own two real,
         fully-investigated documents (see that function's own
         docstring). This is the "if reconstruction produces a high
         unresolved-line count or many unbalanced vouchers... surface
         that prominently" requirement -- computed here and carried
         through to the dashboard regardless of outcome, not only on the
         happy path.
      4. adapt_ledger_to_schema.adapt_ledger() -- normalizes the
         reconstructed entries to this project's canonical schema, into
         whichever output CSV ledger_output_csv_path() says (the known,
         pinned filename for the two already-established documents; a
         fresh generic one for anything else).
      5. derive_period_label() from the reconstruction's own date range
         (or LEDGER_PDF_INFO's existing human label, for the two already-
         established documents -- see that dict's own note on why), then
         combined_report.run_ledger_only_report() against the adapted
         CSV using that label, so a brand-new ledger gets its own
         output/combined_report_{label}.json without colliding with any
         existing one.
      6. Writes a sidecar metadata JSON (ledger_meta_path()) recording
         label/confidence/date range/output paths/processing timestamp.
         Best-effort: if this write itself fails (e.g. a read-only
         filesystem), the function still returns its result -- the
         sidecar is a convenience cache for ledger_tab_info() to avoid
         re-processing on every page load, never load-bearing data.

    Returns the same dict written to the sidecar on success (plus
    "ok": True), or {**partial_result, "ok": False, "stage", "error"} on
    any failure -- "doctype" is also included when the failure was at the
    classify stage, so the caller can show what the document WAS
    classified as instead of just "it's not a ledger"."""
    from datetime import datetime as _dt

    def _emit_stage(stage_name):
        # Never allowed to affect processing -- see this function's own
        # "NEVER RAISES" guarantee and the on_stage docstring above.
        if on_stage is not None:
            try:
                on_stage(stage_name)
            except Exception:
                pass

    result = {
        "pdf_path": pdf_path,
        "filename": os.path.basename(pdf_path),
        "slug": ledger_slug(pdf_path),
        "processed_at": _dt.now().isoformat(timespec="seconds"),
    }

    if result["slug"] == FROZEN_BASELINE_SLUG:
        return {
            **result, "ok": False, "stage": "guard",
            "error": (
                f"{os.path.basename(pdf_path)} is this project's frozen "
                f"FY2024-25 baseline -- use the dedicated, checkbox-gated "
                f"'Re-run full ledger reconstruction' control for it "
                f"instead of this generic entry point (see "
                f"LEDGER_PDF_INFO's own note on why)."
            ),
        }

    _emit_stage("scanning")
    try:
        import classify_document as _cd
        classification = _cd.classify_document(pdf_path)
    except Exception as e:
        return {**result, "ok": False, "stage": "classify", "error": str(e)}

    if classification["doctype"] != "ledger":
        return {
            **result, "ok": False, "stage": "classify",
            "doctype": classification["doctype"],
            "error": (
                f"Not a ledger document -- classified as "
                f"{DOCTYPE_DISPLAY_NAMES.get(classification['doctype'], classification['doctype'])!r} "
                f"({classification['evidence']})"
            ),
        }

    _emit_stage("extracting")
    try:
        import reconstruct_ledger_entries as _rle
        recon_result = _rle.reconstruct_full_document(pdf_path)
    except Exception as e:
        return {**result, "ok": False, "stage": "reconstruct", "doctype": "ledger", "error": str(e)}

    _emit_stage("auditing")
    try:
        confidence = _rle.assess_reconstruction_confidence(recon_result)
    except Exception as e:
        return {**result, "ok": False, "stage": "confidence", "doctype": "ledger", "error": str(e)}

    # Guard found 2026-10-04 via a synthetic stress test (a ledger-shaped
    # PDF whose layout classify_document()/check_is_ledger_document()
    # correctly recognized, but whose actual reconstruction yielded ZERO
    # vouchers -- a garbage/degenerate real-world possibility, not just a
    # test artifact). Without this, 0 vouchers meant 0 rows in the
    # adapted CSV, and combined_report.rule_benfords_law() /
    # compute_mad() both divide by the total row count -- raising a bare
    # ZeroDivisionError from deep inside the rule engine instead of the
    # clear, stage-labeled result this function promises everywhere
    # else. assess_reconstruction_confidence() already detects and
    # explains this exact case (see its own "too few for the percentage
    # checks... parser didn't recognize this document's layout at all"
    # reason) -- reused here rather than re-deriving a second message,
    # and this is checked BEFORE adapt/report are attempted at all, since
    # there is nothing in a zero-row CSV for either to meaningfully do.
    if recon_result["total_vouchers"] == 0:
        return {
            **result, "ok": False, "stage": "reconstruct", "doctype": "ledger",
            "confidence": confidence["confidence"],
            "confidence_reasons": confidence["reasons"],
            "error": (
                "Reconstruction found 0 vouchers in this document -- "
                "nothing to normalize or run the rule engine against. "
                + "; ".join(confidence["reasons"])
            ),
        }

    output_csv = ledger_output_csv_path(pdf_path)
    try:
        import adapt_ledger_to_schema as _als
        _als.adapt_ledger(input_path=recon_result["entries_path"], output_path=output_csv)
    except Exception as e:
        return {**result, "ok": False, "stage": "adapt", "doctype": "ledger", "error": str(e)}

    known = LEDGER_PDF_INFO.get(result["slug"])
    label = (known["label"] if known else None) or derive_period_label(
        recon_result.get("date_range_start"), recon_result.get("date_range_end")
    )
    # A generic (non-established) document's report/flag files are named
    # from report_label, so it must be unique PER DOCUMENT. It used to be
    # just the period label ("FY2025-26"), which meant two uploads covering
    # the same year -- two companies, or a re-upload under a new name --
    # silently overwrote each other's report (every picker entry then
    # showed the last one processed), and an FY2025-26 upload overwrote the
    # established FY2025-26 baseline's own stored report. The slug (the
    # filename) is appended, sanitized so it is always safe in a filename.
    if known:
        report_label = known.get("report_label", label)
    else:
        safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", result["slug"]).strip("_") or "document"
        report_label = f"{label} {safe_slug}"

    try:
        import combined_report as _cr
        _cr.run_ledger_only_report(output_csv, report_label)
    except Exception as e:
        return {**result, "ok": False, "stage": "report", "doctype": "ledger", "error": str(e)}

    _emit_stage("preparing")
    meta = {
        **result,
        "ok": True,
        "doctype": "ledger",
        "label": label,
        "report_label": report_label,
        "report_path": os.path.join(OUTPUT_DIR, f"combined_report_{report_label.replace(' ', '_')}.json"),
        "output_csv": output_csv,
        "total_pages": recon_result["total_pages"],
        "total_vouchers": recon_result["total_vouchers"],
        "total_legs": recon_result["total_legs"],
        "unresolved_count": recon_result["unresolved_count"],
        "unbalanced_count": recon_result["unbalanced_count"],
        "date_range_start": recon_result.get("date_range_start"),
        "date_range_end": recon_result.get("date_range_end"),
        "confidence": confidence["confidence"],
        "confidence_reasons": confidence["reasons"],
    }
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(ledger_meta_path(pdf_path), "w", encoding="utf-8") as f:
            _json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception:
        pass  # sidecar metadata is a convenience cache, never load-bearing

    return meta


def ledger_tab_info(pdf_path):
    """Everything dashboard/app.py's dynamic ledger-tab loop needs for one
    ledger document, regardless of HOW it came to be processed:
      - via process_ledger_document() (a sidecar JSON at
        ledger_meta_path() exists), or
      - one of the two original, already-established documents, processed
        via the dashboard's existing, long-pinned
        run_full_ledger_reconstruction()/run_combined_pipeline()/
        run_fy2025_26_pipeline() buttons, which predate this sidecar-
        metadata system and so never wrote one -- this is the "graceful
        degradation for the two known, already-processed documents" this
        function exists for: it falls back to LEDGER_PDF_INFO's own
        established label/paths plus a "high" confidence (both are this
        project's own fully-investigated, verified baselines) when no
        sidecar is present but that document's report file already
        exists on disk.

    Returns None if there is nothing to show yet for this PDF (never
    processed by either path) -- the caller shows "not processed yet" /
    a Process button instead of a tab."""
    slug = ledger_slug(pdf_path)
    known = LEDGER_PDF_INFO.get(slug)

    meta = _load_json(ledger_meta_path(pdf_path))
    if meta is not None and meta.get("ok"):
        info = dict(meta)
        if known:
            info["label"] = known["label"]  # keep the long-established nice label
        return info

    if known is not None and os.path.exists(known.get("report_path", "")):
        return {
            "slug": slug,
            "pdf_path": pdf_path,
            "filename": os.path.basename(pdf_path),
            "label": known["label"],
            "report_path": known["report_path"],
            "output_csv": known["output_csv"],
            "confidence": "high",
            "confidence_reasons": [],
            "processed_at": None,
            "ok": True,
        }
    return None


def document_usage(documents):
    """What each classified document is actually doing for the analysis,
    as {filename: short status}, so the document lists can say "Used in Tax
    & Compliance" / "Superseded by a newer upload" / "Not recognised"
    instead of a bare "Uploaded". Mirrors the selection source_resolver
    really makes (newest tax set for one assessment year; newest trial
    balance; every GSTR file), so the label can never disagree with the
    numbers. Ledgers are not listed here (their status is processing
    state, shown separately)."""
    import source_resolver
    usage = {}
    tax = source_resolver.resolve_tax_sources(documents)
    chosen_tax = set(tax["filenames"].values())
    tb = source_resolver.resolve_trial_balance(documents)
    chosen_tb = tb["filename"] if tb else None
    for d in documents:
        t, name = d["doctype"], d["filename"]
        if t == "ledger":
            continue
        if t in ("form_26as", "itr_computation", "itr_acknowledgement"):
            usage[name] = (
                "Used in Tax & Compliance" if name in chosen_tax
                else "Not used -- no matching documents for the same assessment year"
                if tax["status"] == "incomplete" else "Superseded by a newer upload"
            )
        elif t == "trial_balance":
            usage[name] = (
                "Used in Tax & Compliance" if name == chosen_tb else "Superseded by a newer upload"
            )
        elif t in ("gstr1", "gstr3b"):
            usage[name] = (
                "Used in GST Compliance -- sample returns" if name in PROTECTED_UPLOAD_NAMES
                else "Used in GST Compliance -- your uploaded returns"
            )
        elif d.get("issue") == "scanned":
            usage[name] = "Not readable -- a scan or photo, no selectable text"
        elif d.get("issue") == "unreadable":
            usage[name] = "Not readable -- the file could not be opened"
        else:
            usage[name] = "Not recognised"
    return usage


def tax_compliance_itr_summary(tax_report):
    """The "ITR vs Form 26AS" card's data from output/tax_compliance_report
    .json -- {status, reason, sources, assessment_year, deductor_count,
    total_claimed, total_reported, flags_df}. Works for every status; a
    missing report reads as status "none"."""
    columns = ["entity", "reason", "severity", "source"]
    sec = (tax_report or {}).get("itr_vs_26as") or {}
    return {
        "status": sec.get("status", "none"),
        "reason": sec.get("reason", ""),
        "sources": sec.get("sources", {}),
        "assessment_year": sec.get("assessment_year"),
        "deductor_count": sec.get("deductor_count", 0),
        "total_claimed": sec.get("total_claimed", 0.0),
        "total_reported": sec.get("total_reported", 0.0),
        "flags_df": pd.DataFrame(sec.get("flags", []), columns=columns),
    }


def tax_compliance_tb_summary(tax_report):
    """The "Trial Balance vs General Ledger" card's data -- {status, reason,
    sources, period, ledger_label, clean_count, mismatched_count,
    unmatched_count, total_checked, flags_df}."""
    columns = ["account", "side", "tb_amount", "gl_amount",
               "mismatch_amount", "severity", "variants_used"]
    sec = (tax_report or {}).get("trial_balance") or {}
    return {
        "status": sec.get("status", "none"),
        "reason": sec.get("reason", ""),
        "sources": sec.get("sources", {}),
        "period": sec.get("period"),
        "ledger_label": sec.get("ledger_label"),
        "clean_count": sec.get("clean_count", 0),
        "mismatched_count": sec.get("mismatched_count", 0),
        "unmatched_count": sec.get("unmatched_count", 0),
        "total_checked": sec.get("total_checked", 0),
        "flags_df": pd.DataFrame(sec.get("flags", []), columns=columns),
    }


def document_library(documents=None):
    """Everything the dashboard's Process Documents tab needs: every
    raw PDF's content-based classification, plus -- for anything
    classified as a ledger -- whether it's been processed yet and, if so,
    its confidence verdict. Built as one call so app.py's sidebar loop
    doesn't need to re-derive any of this itself.

    `documents`: same as discover_ledger_documents()'s own parameter --
    pass discover_documents()'s result to reuse it instead of re-scanning
    data/raw_pdfs/ again. Optional, defaults to None (re-scans, the
    original behavior), so every existing caller and test is unaffected.

    Each row: {filename, path, doctype, doctype_display, evidence, error,
    is_ledger, is_known (this PDF's slug is one of LEDGER_PDF_INFO's two
    already-established documents, which have their OWN dedicated
    reconstruction controls elsewhere in the sidebar -- so this function's
    caller knows not to also offer a generic "Process" button for them),
    is_processed, tab_info (ledger_tab_info()'s result, or None)}."""
    rows = []
    docs = documents if documents is not None else discover_documents()
    for d in docs:
        is_ledger = d["doctype"] == "ledger"
        tab_info = ledger_tab_info(d["path"]) if is_ledger else None
        rows.append({
            **d,
            "doctype_display": DOCTYPE_DISPLAY_NAMES.get(d["doctype"], d["doctype"]),
            "is_ledger": is_ledger,
            "is_known": ledger_slug(d["path"]) in LEDGER_PDF_INFO,
            "is_processed": tab_info is not None,
            "tab_info": tab_info,
        })
    return rows


def kpi_summary(report):
    """Top-line KPI numbers for the Overview tab. `report` is a
    combined_report.json dict (or None if not yet generated)."""
    if report is None:
        return {
            "total_legs": 0,
            "total_legs_flagged": 0,
            "multi_signal": 0,
            "single_signal": 0,
            "critical_count": 0,
            "high_count": 0,
            "generated_at": None,
        }
    by_severity = report["summary"]["by_severity"]
    return {
        "total_legs": report["dataset"]["total_legs"],
        "total_legs_flagged": report["summary"]["total_legs_flagged"],
        "multi_signal": report["summary"]["multi_signal"],
        "single_signal": report["summary"]["single_signal"],
        "critical_count": by_severity.get("CRITICAL", 0),
        "high_count": by_severity.get("HIGH", 0),
        "generated_at": report.get("generated_at"),
    }


def severity_breakdown_df(report):
    """DataFrame of severity -> count for the ledger-leg flags, ordered
    CRITICAL..LOW, ready for a bar chart. Empty (0-row, correct columns)
    DataFrame if there's no report yet or nothing was flagged."""
    columns = ["severity", "count"]
    if report is None:
        return pd.DataFrame(columns=columns)
    by_severity = report["summary"]["by_severity"]
    rows = [
        {"severity": sev, "count": by_severity.get(sev, 0)}
        for sev in SEVERITY_ORDER
        if sev in by_severity
    ]
    return pd.DataFrame(rows, columns=columns)


def ledger_flags_dataframe(report):
    """Flattens combined_report.json's ledger_leg_flags into one row per
    flag-reason (a leg with 2 signals becomes 2 rows, each carrying its own
    rule/reason) so the Ledger Flags tab can filter by rule or severity
    without losing which specific signals fired on a multi-signal leg.

    `amount` is cast to float (the JSON stores it as a string, e.g.
    "100000.00") and `date_parsed` is added as a real datetime -- both
    needed so the tab's amount-range and date-range filters (and the
    existing "Sort by amount" option) compare numerically/chronologically
    instead of lexically. Sorting the old string amount column put
    "999.00" ahead of "1500000.00" (string comparison, not numeric) --
    fixed here since every sort/filter on amount goes through this
    function. `rule_display` adds the human-readable name
    (RULE_DISPLAY_NAMES) alongside the raw `rule` code, which stays for
    anything that still needs the code (filters map back to it)."""
    columns = ["leg_key", "entry_id", "date", "date_parsed", "account", "amount",
               "rule", "rule_display", "reason", "reason_severity",
               "signal_count", "severity"]
    if report is None:
        return pd.DataFrame(columns=columns)

    rows = []
    for flag in report.get("ledger_leg_flags", []):
        for reason in flag["reasons"]:
            rows.append({
                "leg_key": flag["leg_key"],
                "entry_id": flag["entry_id"],
                "date": flag["date"],
                "account": flag["account"],
                "amount": float(flag["amount"]),
                "rule": reason["rule"],
                "rule_display": RULE_DISPLAY_NAMES.get(reason["rule"], reason["rule"]),
                "reason": reason["reason"],
                "reason_severity": reason["severity"],
                "signal_count": flag["signal_count"],
                "severity": flag["severity"],
            })
    df = pd.DataFrame(rows, columns=[c for c in columns if c != "date_parsed"])
    if df.empty:
        df["date_parsed"] = pd.Series(dtype="datetime64[ns]")
    else:
        df["date_parsed"] = pd.to_datetime(df["date"], format="%d-%b-%y")
    return df[columns]


def ledger_flag_row_count(report):
    """Total number of (leg, rule) flag-ROWS -- what ledger_flags_dataframe()
    has one row per, and what the Ledger Flags tab's "Showing X of Y" caption
    counts -- as distinct from the number of flagged LEGS
    (kpi_summary()'s total_legs_flagged). A leg with N reasons contributes
    N rows, so this is always >= total_legs_flagged, and the gap is exactly
    the number of "extra" signals on multi-signal legs.

    Added 2026-10-02 for a real reported confusion: Overview said "Legs
    flagged: 1,271", the Ledger Flags tab said "Showing 360 of 1,279 flag
    rows" -- two similarly-worded totals, 8 apart, with nothing on the page
    explaining why (the 8-row gap is exactly the multi-signal-leg count).
    This function exists so the UI can say "1,279 flag-rows across 1,271
    flagged legs" explicitly instead of leaving two numbers unreconciled."""
    if report is None:
        return 0
    return sum(len(flag["reasons"]) for flag in report.get("ledger_leg_flags", []))


def distinct_voucher_count(report):
    """Number of distinct VOUCHERS (entry_id) with at least one flagged
    leg -- see voucher_severity_breakdown_df()'s docstring for why this
    differs from both total_legs_flagged and ledger_flag_row_count()."""
    if report is None:
        return 0
    return len({flag["entry_id"] for flag in report.get("ledger_leg_flags", [])})


def voucher_severity_breakdown_df(report):
    """Severity breakdown by distinct VOUCHER (entry_id) rather than by
    individual ledger LEG -- a complement to severity_breakdown_df(), not
    a replacement for it.

    Added 2026-10-02 for a real reported issue: round_number_bias (and any
    other leg-level rule) flags each leg of a voucher independently, so a
    single suspicious voucher with two round-amount legs (e.g. a Dr leg to
    a vendor and the matching Cr leg to a bank, both Rs.10,00,000) produces
    TWO "CRITICAL" leg-flags for what a reader would reasonably call ONE
    suspicious voucher. The leg-level "CRITICAL: 360" headline is accurate
    as a count of legs, but overstates the number of distinct suspicious
    transactions if read as "360 separate problems" -- the real figure is
    roughly half that. This function counts distinct entry_ids instead,
    with a voucher's severity taken as the HIGHEST severity among its own
    flagged legs (a voucher is at least as serious as its worst leg)."""
    columns = ["severity", "voucher_count"]
    if report is None:
        return pd.DataFrame(columns=columns)

    by_entry = defaultdict(list)
    for flag in report.get("ledger_leg_flags", []):
        by_entry[flag["entry_id"]].append(flag["severity"])

    voucher_severity = {
        entry_id: max(sevs, key=lambda s: SEVERITY_RANK.get(s, 0))
        for entry_id, sevs in by_entry.items()
    }
    counts = Counter(voucher_severity.values())
    rows = [
        {"severity": sev, "voucher_count": counts.get(sev, 0)}
        for sev in SEVERITY_ORDER
        if sev in counts
    ]
    return pd.DataFrame(rows, columns=columns)


def tax_reconciliation_dataframe(report):
    """Rule 4 (ITR vs Form 26AS) flags as a DataFrame. Empty today (real
    data reconciles after the zero-claimed fix was re-verified), but the
    shape is pinned so the table renders correctly once new financial-year
    data introduces a real mismatch."""
    columns = ["entity", "reason", "severity", "source"]
    if report is None:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(report.get("tax_reconciliation_flags", []), columns=columns)


def trial_balance_dataframe(report):
    """Rule 6 (Trial Balance vs Ledger) flags as a DataFrame."""
    columns = ["account", "side", "tb_amount", "gl_amount",
               "mismatch_amount", "severity", "variants_used"]
    if report is None:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(report.get("trial_balance_flags", []), columns=columns)


def voucher_gap_dataframe(report):
    """Rule 7 (voucher-number sequence gaps) flags as a DataFrame. Works
    against EITHER report shape this module loads (combined_report.json
    or combined_report_FY2025-26.json -- both carry
    "voucher_sequence_gap_flags" with the same shape, since both are
    produced by the same compute_ledger_intrinsic_flags() call inside
    scripts/combined_report.py).

    Added 2026-10-04: this rule's output has been computed and exported
    to JSON since it was built, but had no dashboard presence at all --
    not even on the existing FY2024-25 tabs. `missing_numbers` is joined
    into a display string (it's a list in the JSON) the same convention
    trial_balance_dataframe() uses for `variants_used`."""
    columns = ["vch_type", "prefix", "missing_count", "group_min",
               "group_max", "group_size", "severity", "missing_numbers"]
    if report is None:
        return pd.DataFrame(columns=columns)
    rows = []
    for flag in report.get("voucher_sequence_gap_flags", []):
        rows.append({**flag, "missing_numbers": ", ".join(
            str(n) for n in flag.get("missing_numbers", [])
        )})
    return pd.DataFrame(rows, columns=columns)


def ml_anomaly_dataframe(report):
    """Rule 8 (unsupervised ML anomaly detection, Isolation Forest) flags
    as a DataFrame. Works against either report shape, same as
    voucher_gap_dataframe() above.

    Added 2026-10-04: Rule 8 was built and wired into combined_report.py
    earlier the same day, with its own full test suite and checkpoint
    writeup (BASELINE_CHECKPOINT_V3.14.md section 1.1) -- but nothing in
    the dashboard ever displayed its 731/699 real flags. `severity` is
    upper-cased here (rule_ml_anomaly_detection.py emits lowercase
    "high"/"medium"/"low", unlike every other rule in this project, which
    emits uppercase "HIGH"/"MEDIUM"/"LOW"/"CRITICAL"/"NONE") so it lines
    up with SEVERITY_COLORS/SEVERITY_ORDER and every other severity column
    this dashboard already renders, rather than needing its own
    special-cased styling."""
    columns = ["entry_id", "account", "dr_cr", "amount", "date",
               "vch_type", "severity", "anomaly_score", "anomaly_rank_pct"]
    if report is None:
        return pd.DataFrame(columns=columns)
    rows = []
    for flag in report.get("ml_anomaly_flags", []):
        rows.append({**flag, "severity": flag["severity"].upper()})
    df = pd.DataFrame(rows, columns=columns)
    if not df.empty:
        df["amount"] = df["amount"].astype(float)
    return df


def ml_anomaly_summary(report):
    """Status line + flags DataFrame for the ML Anomaly Detection (Rule 8)
    section, same "summary with supporting figures" convention as
    tax_reconciliation_summary()/trial_balance_summary() above. Severity
    counts are read straight off ml_anomaly_dataframe()'s already-upper-
    cased severity column so this never drifts from what the table itself
    shows."""
    df = ml_anomaly_dataframe(report)
    by_severity = df["severity"].value_counts().to_dict() if not df.empty else {}
    return {
        "total_flagged": len(df),
        "by_severity": by_severity,
        "flags_df": df,
    }


def tax_reconciliation_summary(report):
    """Rule 4 status WITH supporting figures, not just the flag list.

    Added 2026-10-02: the dashboard's "No mismatches" box for Rule 4 showed
    no numbers at all -- for an audit tool specifically, a clean result
    with zero figures behind it is less convincing than one that shows its
    work (e.g. "1 deductor checked, Rs.32,018.00 claimed = Rs.32,018.00
    reported"). Reads data/form26as.csv directly (not from
    combined_report.json, which only carries the FLAGGED deductors, not the
    full universe checked) via rule_itr_26as_reconciliation.py's own
    FORM26AS_FILE constant, so this can never drift from what that rule
    actually checked."""
    import csv
    import rule_itr_26as_reconciliation as ritr

    with open(ritr.FORM26AS_FILE, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total_claimed = sum(float(r["tds_claimed_in_itr_share"]) for r in rows)
    total_reported = sum(float(r["tds_reported_by_deductor"]) for r in rows)

    return {
        "deductor_count": len(rows),
        "total_claimed": total_claimed,
        "total_reported": total_reported,
        "flags_df": tax_reconciliation_dataframe(report),
    }


def trial_balance_summary(report):
    """Rule 6 status WITH supporting figures (e.g. "82/82 accounts
    reconcile"), for the same reason as tax_reconciliation_summary() above.
    Calls reconcile_trial_balance.reconcile() directly -- not from
    combined_report.json, which (like Rule 4) only carries the FLAGGED
    accounts -- so "how many did we check in total" always matches what
    that module actually reconciled. reconcile() also parses the trial
    balance PDF each call (~0.3s on the real data), so callers that run
    this on every Streamlit rerun should wrap it in st.cache_data."""
    import reconcile_trial_balance as rtb

    result = rtb.reconcile()
    clean_count = len(result["clean"])
    mismatched_count = len(result["mismatched"])
    unmatched_count = len(result["unmatched"])

    return {
        "clean_count": clean_count,
        "mismatched_count": mismatched_count,
        "unmatched_count": unmatched_count,
        "total_checked": clean_count + mismatched_count,
        "flags_df": trial_balance_dataframe(report),
    }


def benford_summary(report):
    """Benford's Law section (chi-square + MAD, both reported additively --
    see rule_benfords_law.py's module docstring for why neither replaces
    the other).

    Every field is read with .get(), not direct indexing, because the
    "benford" key has existed since this project's Rule 3 was first built,
    but the mad/mad_conformity fields inside it were added later (2026-10-02).
    A combined_report.json regenerated before that date -- e.g. a stale
    checked-in output file that hasn't been re-run since pulling the latest
    scripts/combined_report.py -- has "benford" present but without those
    two keys. Returning None for them here (instead of letting a KeyError
    propagate, which is what happened the first time this shipped) lets the
    UI show "re-run the pipeline" instead of crashing the whole app."""
    if report is None or "benford" not in report:
        return None
    benford = report["benford"]
    return {
        "chi_square": benford.get("chi_square"),
        "threshold": benford.get("threshold"),
        "deviates": benford.get("deviates"),
        "mad": benford.get("mad"),
        "mad_conformity": benford.get("mad_conformity"),
    }


def benford_segmentation_summary(segmentation_report):
    """Transforms analyze_benford_segmentation.py's run_segmentation_report()
    result into what the Benford tab needs to actually resolve the chi-
    square-vs-MAD contradiction for a reader, instead of just displaying
    both numbers side by side with no supporting evidence (the gap flagged
    2026-10-02: the tab showed the disagreement but not why the project
    leans on MAD here).

    Returns None if segmentation_report is None (not generated yet).
    Otherwise returns the vch_type/account breakdown DataFrames, the two
    exclusion checks, and a plain-English `verdict` paragraph built
    directly from the real numbers -- not a canned explanation -- so it
    stays accurate if the underlying data ever changes:
      - what fraction of substantial (n>=10) vch_type segments deviate on
        chi-square,
      - whether both exclusion checks (stripping the obvious repetitive-
        pricing candidates) still deviate,
      - which segments are the most statistically extreme outliers and
        worth a manual look (named explicitly, not suppressed -- this
        project's standing rule is to add findings, never hide one just
        because it doesn't fit the overall "probably not fraud" read)."""
    if segmentation_report is None:
        return None

    vch_rows = segmentation_report["by_vch_type"]
    acct_rows = segmentation_report["by_account"]
    exclusion_checks = segmentation_report["exclusion_checks"]

    substantial = [s for s in vch_rows if s["n"] >= 10]
    deviating = [s for s in substantial if s["deviates"]]
    both_exclusions_deviate = all(c["deviates"] for c in exclusion_checks)

    # Same breadth check as above, applied to accounts instead of voucher
    # types -- added 2026-10-02 alongside the checklist-style Benford tab,
    # to answer "is this concentrated in one account?" with a real count
    # instead of leaving the reader to infer it from the raw table.
    acct_substantial = [s for s in acct_rows if s["n"] >= 10]
    acct_deviating = [s for s in acct_substantial if s["deviates"]]

    outliers = sorted(vch_rows, key=lambda s: -s["chi_square"])[:2]
    outlier_names = [s["segment"] for s in outliers]
    outlier_chi_squares = [s["chi_square"] for s in outliers]

    verdict = (
        f"{len(deviating)} of {len(substantial)} voucher types with at least "
        f"10 legs deviate on chi-square"
    )
    if both_exclusions_deviate:
        verdict += (
            ", and the deviation persists even after excluding the highest-"
            "volume accounts and all fuel-sale voucher types -- so it isn't "
            "concentrated in the segments most likely to carry repetitive, "
            "fixed fuel/GST pricing. That breadth is more consistent with "
            "chi-square's known sensitivity to this dataset's size than with "
            "fraud concentrated in a specific area."
        )
    else:
        verdict += ". Not every exclusion check still deviates, which is worth a closer look."
    verdict += (
        f" The most statistically extreme segments are {' and '.join(outlier_names)} "
        f"(chi-square {outliers[0]['chi_square']:.0f} and {outliers[1]['chi_square']:.0f} "
        f"respectively, well above the rest) -- worth a manual look as a precaution, "
        f"even though the overall read leans toward a structural/statistical artifact "
        f"rather than concentrated fraud."
    )

    columns = ["segment", "n", "pct_of_total", "chi_square", "deviates", "mad", "mad_conformity"]
    return {
        "by_vch_type_df": pd.DataFrame(vch_rows, columns=columns),
        "by_account_df": pd.DataFrame(acct_rows, columns=columns),
        "exclusion_checks": exclusion_checks,
        "outlier_segments": outlier_names,
        "verdict": verdict,
        "generated_at": segmentation_report.get("generated_at"),
        # Added 2026-10-02: the same numbers the `verdict` paragraph above
        # is built from, exposed individually (not new calculations -- just
        # not previously returned on their own) so the Benford tab can show
        # a scannable checklist instead of requiring the full paragraph to
        # be read to find them.
        "deviating_count": len(deviating),
        "substantial_count": len(substantial),
        "both_exclusions_deviate": both_exclusions_deviate,
        "account_deviating_count": len(acct_deviating),
        "account_substantial_count": len(acct_substantial),
        "outlier_chi_squares": outlier_chi_squares,
    }


_MONTH_INDEX = {
    m: i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}


def sort_gst_periods(periods):
    """Chronological order for period labels like 'April 2025-26' /
    'January 2025-26' (January is in calendar 2026 there). The report lists
    them alphabetically, which is useless for reading a range ("April
    2025-26 to September 2025-26" while the data actually runs to July
    2026). Labels that don't parse keep their relative order, at the end."""
    def key(label):
        try:
            month, fy = label.split()
            m = _MONTH_INDEX[month]
            start = int(fy[:4])
            return (0, start if m >= 4 else start + 1, m)
        except (ValueError, KeyError):
            return (1, 0, 0)
    return sorted(periods, key=key)


def gstr_summary(gstr_report):
    """Status line + flags DataFrame for the GSTR-1/GSTR-3B section.
    `gstr_report` is a gstr_report.json dict (or None if not generated)."""
    columns = ["period", "check", "reason", "severity"]
    if gstr_report is None:
        return {
            "generated_at": None,
            "status": None,
            "label": None,
            "gstr1_sources": None,
            "gstr3b_sources": None,
            "warnings": [],
            "periods_compared": None,
            "only_in_gstr1": [],
            "only_in_gstr3b": [],
            "periods": [],
            "total_flagged": 0,
            "by_severity": {},
            "flags_df": pd.DataFrame(columns=columns),
        }
    dataset = gstr_report["dataset"]
    return {
        "generated_at": gstr_report.get("generated_at"),
        # Reports written before origin-aware pairing were all complete comparisons.
        "status": gstr_report.get("status", "ok"),
        "label": dataset.get("label"),
        "gstr1_sources": dataset.get("gstr1_sources"),
        "gstr3b_sources": dataset.get("gstr3b_sources"),
        "warnings": dataset.get("warnings", []),
        "periods_compared": dataset.get("periods_compared"),
        "only_in_gstr1": dataset.get("periods_only_in_gstr1", []),
        "only_in_gstr3b": dataset.get("periods_only_in_gstr3b", []),
        "periods": gstr_report["dataset"]["periods"],
        "total_flagged": gstr_report["summary"]["total_flagged"],
        "by_severity": gstr_report["summary"]["by_severity"],
        "flags_df": pd.DataFrame(gstr_report.get("flags", []), columns=columns),
    }


def ledger_gstr_summary(ledger_gstr_report):
    """Status line + flags DataFrame for the Ledger vs GSTR-1 taxable-
    supply reconciliation section (rule_ledger_gstr_reconciliation.py) --
    the check that resolved this project's original motivating finding
    (the ledger's Sales account Dr/Cr pattern, later traced to a header-
    detection bug -- see that module's own docstring). Added 2026-10-04:
    this report has existed in output/ since the header-detection bug
    chain was closed out (BASELINE_CHECKPOINT_V3.13.md), fully tested,
    but had never been surfaced in the dashboard at all.

    Mirrors gstr_summary()'s shape, adapted for this report's own dataset
    keys: it has TWO period lists (taxable-activity periods and non-GST-
    activity periods, since a ledger period can have one, the other, or
    both -- see rule_ledger_gstr_reconciliation.py's module docstring)
    instead of gstr_report.json's single "periods" list, because this
    reconciliation checks two separate kinds of supply against GSTR-1,
    not one."""
    columns = ["period", "check", "reason", "severity"]
    if ledger_gstr_report is None:
        return {
            "generated_at": None,
            "status": None,
            "ledger_label": None,
            "gstr1_sources": None,
            "gstr1_warnings": [],
            "periods_compared": None,
            "not_covered_periods": [],
            "non_gst_accounts": [],
            "taxable_periods": [],
            "non_gst_periods": [],
            "total_flagged": 0,
            "by_severity": {},
            "flags_df": pd.DataFrame(columns=columns),
        }
    dataset = ledger_gstr_report["dataset"]
    return {
        "generated_at": ledger_gstr_report.get("generated_at"),
        # Reports written before this field existed were all "ok" comparisons.
        "status": ledger_gstr_report.get("status", "ok"),
        "ledger_label": dataset.get("ledger_label"),
        "gstr1_sources": dataset.get("gstr1_sources"),
        "gstr1_warnings": dataset.get("gstr1_warnings", []),
        "periods_compared": dataset.get("periods_compared"),
        "not_covered_periods": dataset.get("periods_not_covered_by_gstr1", []),
        "non_gst_accounts": dataset.get("non_gst_sales_accounts", []),
        "taxable_periods": dataset.get("ledger_periods_with_taxable_activity", []),
        # .get() with a [] default, not direct indexing: a
        # output/ledger_gstr_reconciliation_report.json written before the
        # non-GST supply check was added to rule_ledger_gstr_reconciliation.py
        # (see that module's docstring) won't have this key yet -- same
        # "don't crash on an older-shaped stale JSON, show what we can"
        # convention benford_summary() already follows for mad/mad_conformity.
        "non_gst_periods": dataset.get("ledger_periods_with_non_gst_activity", []),
        "total_flagged": ledger_gstr_report["summary"]["total_flagged"],
        "by_severity": ledger_gstr_report["summary"]["by_severity"],
        "flags_df": pd.DataFrame(ledger_gstr_report.get("flags", []), columns=columns),
    }


def rule_options(report):
    """Sorted list of distinct rule CODES present in the ledger flags (e.g.
    "round_number_bias"). Filtering logic matches on these codes; the
    Ledger Flags tab's dropdown shows RULE_DISPLAY_NAMES built from this
    list, not the raw codes themselves."""
    if report is None:
        return []
    rules = {reason["rule"] for flag in report.get("ledger_leg_flags", [])
             for reason in flag["reasons"]}
    return sorted(rules)


def priority_findings(report, limit=10):
    """The most important findings, ranked by severity then by amount --
    this is what the Overview tab leads with now instead of just
    statistics (added 2026-10-02, the single biggest improvement
    requested: a reader should see the actual worst findings immediately,
    not just counts of them).

    One row per flagged VOUCHER (entry_id), not per leg or per (leg, rule)
    row -- deliberately, to stay consistent with the leg-vs-voucher
    distinction this dashboard already makes elsewhere (see
    voucher_severity_breakdown_df()'s docstring): a voucher with two
    round-number-flagged legs (one Dr, one Cr) is ONE suspicious
    transaction to look at, and showing it as two back-to-back rows here
    would reintroduce the exact double-counting confusion that was fixed
    on the Overview KPIs. `accounts` lists every flagged account on the
    voucher (joined, since a journal entry can flag more than one leg);
    `severity`/`amount` are the voucher's own max (a voucher is at least
    as serious/large as its worst leg, same convention used throughout);
    `checks` and `why` pool every reason across all of the voucher's
    flagged legs. `entry_id` is carried through so the UI can wire an
    "Investigate" action straight to voucher_detail()."""
    columns = ["entry_id", "severity", "accounts", "amount",
               "date", "checks", "why", "leg_count"]
    if report is None:
        return pd.DataFrame(columns=columns)

    by_entry = defaultdict(list)
    for flag in report.get("ledger_leg_flags", []):
        by_entry[flag["entry_id"]].append(flag)
    if not by_entry:
        return pd.DataFrame(columns=columns)

    rows = []
    for entry_id, flags in by_entry.items():
        severity = max(
            (f["severity"] for f in flags), key=lambda s: SEVERITY_RANK.get(s, 0)
        )
        amount = max(float(f["amount"]) for f in flags)
        accounts = ", ".join(sorted({f["account"] for f in flags}))
        checks = ", ".join(sorted({
            RULE_DISPLAY_NAMES.get(r["rule"], r["rule"])
            for f in flags for r in f["reasons"]
        }))
        # dict.fromkeys dedupes (e.g. a repeated reason text across legs)
        # while keeping first-seen order -- plain set() would shuffle it.
        why = "; ".join(dict.fromkeys(
            r["reason"] for f in flags for r in f["reasons"]
        ))
        rows.append({
            "entry_id": entry_id,
            "severity": severity,
            "accounts": accounts,
            "amount": amount,
            "date": flags[0]["date"],
            "checks": checks,
            "why": why,
            "leg_count": len(flags),
            "_severity_rank": SEVERITY_RANK.get(severity, 0),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["_severity_rank", "amount"], ascending=[False, False], kind="stable"
    ).head(limit).reset_index(drop=True)
    return df[columns]


def priority_findings_concentration_note(priority_df):
    """An optional one-line note for the Priority Findings section, added
    2026-10-02 in response to a real observation: the top 10 can end up
    dominated by the same one or two accounts (e.g. repeated large
    round-number payments between the same supplier and bank account),
    which can read as "this tool only catches one kind of thing" even
    though it's really "this supplier relationship is worth a closer
    look". The fix is NOT to re-rank or drop any finding to force variety
    -- priority_findings() stays strictly severity-then-amount, since
    diluting a real CRITICAL finding out of the top 10 to make room for a
    lower-severity one for variety's sake would misrepresent severity to
    make the list look tidier, which this project's standing rule (never
    suppress a real finding) rules out. Instead, the repetition itself
    becomes a visible, honest fact: this is a count over the SAME
    findings priority_findings() already returned, not a new check and
    not a reordering of anything.

    Returns None when there's nothing to say (every account in the list
    is distinct, or there's no data) -- the caller shows nothing rather
    than a note that's just restating "everything here is different"."""
    if priority_df is None or priority_df.empty:
        return None

    counts = Counter()
    for accounts in priority_df["accounts"]:
        for acct in (a.strip() for a in accounts.split(",")):
            if acct:
                counts[acct] += 1

    repeated = {acct: n for acct, n in counts.items() if n > 1}
    if not repeated:
        return None

    rows_with_a_repeat = sum(
        1 for accounts in priority_df["accounts"]
        if any(repeated.get(a.strip(), 0) > 1 for a in accounts.split(","))
    )
    top_account, top_count = max(repeated.items(), key=lambda kv: kv[1])

    return (
        f"{rows_with_a_repeat} of the top {len(priority_df)} findings involve an "
        f"account that recurs in this list -- {top_account} appears in {top_count} "
        f"of them. Worth noting as a concentration, not a sign anything here is wrong."
    )


def _load_ledger_rows(ledger_path=GENERAL_LEDGER_PATH):
    """Raw general_ledger.csv-shaped rows, in file order, as plain dicts --
    the same file combined_report.py (or run_ledger_only_report() for a
    non-FY2024-25 ledger) reads to build ledger_leg_flags, but with every
    column (vch_type, vch_no, dr_cr, vendor, narration, entered_by,
    approved_by, corroboration_count, ...) that ledger_leg_flags itself
    doesn't carry. Returns [] if the file doesn't exist yet (pipeline
    hasn't produced it), same "no data yet" convention as the rest of this
    module.

    `ledger_path` defaults to GENERAL_LEDGER_PATH (FY2024-25) for backward
    compatibility with every existing caller; added 2026-10-04 so
    voucher_detail() can be pointed at FY2025_26_GL_PATH instead for that
    tab's own drill-down, without a second copy of this function."""
    if not os.path.exists(ledger_path):
        return []
    with open(ledger_path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def voucher_legs(entry_id, rows=None):
    """Every raw general_ledger.csv row for one voucher (entry_id), in file
    order, each tagged with a 1-based `leg_num` that matches
    combined_report.py's own per-voucher leg numbering exactly (same
    counting rule: the Nth row seen for that entry_id, in file order) --
    so a leg_num here always lines up with the leg_key
    ("{entry_id} (leg {leg_num})") a flag was recorded under. `rows` lets
    a caller pass in an already-loaded list (e.g. finding_detail(), which
    needs this for more than one entry_id) to avoid re-reading the CSV
    each time."""
    if rows is None:
        rows = _load_ledger_rows()
    legs = []
    seen = 0
    for row in rows:
        if row.get("entry_id") != entry_id:
            continue
        seen += 1
        legs.append({**row, "leg_num": seen})
    return legs


_LEG_NUM_RE = re.compile(r"\(leg (\d+)\)$")


def _leg_num_from_key(leg_key):
    """Extracts the integer leg number from a "{entry_id} (leg N)" leg_key.
    Returns None if leg_key doesn't match that shape (defensive -- every
    leg_key combined_report.py writes matches it, verified against all
    1,271 real flagged legs, but this never raises on an unexpected one)."""
    m = _LEG_NUM_RE.search(leg_key or "")
    return int(m.group(1)) if m else None


def _related_entry_ids_from_reason(reason_text):
    """Pulls the other voucher IDs a duplicate_transaction reason names out
    of its own sentence (see _DUPLICATE_VOUCHER_RE's docstring). Returns []
    if the sentence doesn't match -- e.g. a reason from a different rule --
    rather than raising, since this is called across every reason on a
    flag regardless of which rule produced it."""
    m = _DUPLICATE_VOUCHER_RE.search(reason_text or "")
    if not m:
        return []
    return [v.strip() for v in m.group(1).split(",") if v.strip()]


def voucher_detail(report, entry_id, ledger_path=GENERAL_LEDGER_PATH):
    """Everything the Investigate drill-down needs for one VOUCHER
    (transaction) -- added 2026-10-02 so clicking a finding in Priority
    Findings or Ledger Flags opens a real investigation of the whole
    transaction, not just whichever single leg a table row happened to
    show. Keyed by entry_id rather than leg_key to match
    priority_findings()'s voucher-level granularity -- an auditor
    investigates a transaction, not a fragment of one.

      - `legs`: every leg of this voucher (data/general_ledger.csv rows,
        each tagged with its 1-based leg_num), flagged or not -- a voucher
        can have an unflagged leg (e.g. only one side of a journal entry
        is round-number), and the full transaction still needs to be
        visible so the auditor can see both sides, not just the flagged
        one.
      - `flagged_legs`: just the flagged ones, each paired with its own
        `raw_row` (the full ledger row -- debit/credit, voucher type,
        narration, etc., none of which ledger_leg_flags itself carries)
        and an `evidence` checklist (one "{check name}: {reason text}"
        string per reason on that leg).
      - `related_entry_ids` / `related_legs`: when any flagged leg carries
        a duplicate_transaction reason, the other voucher(s) it named
        (parsed from the reason's own sentence -- see
        _related_entry_ids_from_reason) and their full legs, so a
        "matching/related transactions" section has something real to
        show. Empty when no duplicate signal fired anywhere on this
        voucher -- round-number-bias and segregation-of-duties flags
        don't have a "related transaction" concept, and this doesn't
        invent one.
      - `severity`: the voucher's own max severity across its flagged
        legs (same convention as priority_findings()/
        voucher_severity_breakdown_df()).

    Returns None if the report is missing or this entry_id has no legs at
    all in general_ledger.csv (nothing to investigate).

    `ledger_path` defaults to GENERAL_LEDGER_PATH (FY2024-25); added
    2026-10-04 so the FY2025-26 tab can pass FY2025_26_GL_PATH and get a
    correct drill-down against ITS OWN ledger rows instead of silently
    joining against the wrong financial year's data (entry_ids are not
    guaranteed unique across the two ledgers, so this would otherwise be
    a silent wrong-row bug, not just a missing feature)."""
    if report is None:
        return None

    all_rows = _load_ledger_rows(ledger_path)
    legs = voucher_legs(entry_id, rows=all_rows)
    if not legs:
        return None
    legs_by_num = {leg["leg_num"]: leg for leg in legs}

    flagged = [f for f in report.get("ledger_leg_flags", []) if f["entry_id"] == entry_id]

    flagged_legs = []
    related_ids = set()
    for flag in flagged:
        leg_num = _leg_num_from_key(flag["leg_key"])
        raw_row = legs_by_num.get(leg_num)
        evidence = [
            f"{RULE_DISPLAY_NAMES.get(r['rule'], r['rule'])}: {r['reason']}"
            for r in flag["reasons"]
        ]
        for r in flag["reasons"]:
            if r["rule"] == "duplicate_transaction":
                related_ids.update(_related_entry_ids_from_reason(r["reason"]))
        flagged_legs.append({"flag": flag, "raw_row": raw_row, "evidence": evidence})

    related_ids = sorted(related_ids - {entry_id})
    related_legs = []
    for rid in related_ids:
        related_legs.extend(voucher_legs(rid, rows=all_rows))

    severity = max(
        (f["severity"] for f in flagged),
        key=lambda s: SEVERITY_RANK.get(s, 0),
        default=None,
    )

    return {
        "entry_id": entry_id,
        "severity": severity,
        "legs": legs,
        "flagged_legs": flagged_legs,
        "related_entry_ids": related_ids,
        "related_legs": related_legs,
    }


def pipeline_status(report, gstr_report):
    """Status of the document pipeline, for the "is this dashboard showing
    results from a successfully processed dataset" question (added
    2026-10-02). Each stage's checkmark reflects whether that stage's own
    output file currently exists on disk -- e.g. "Normalization" is marked
    done because data/general_ledger.csv (adapt_ledger_to_schema.py's
    output) exists, not from any separately-tracked execution log, since
    none of this project's scripts currently write one. That's an honest
    proxy, not a live run-tracker: the file existing is real, direct
    evidence that stage produced output, same standard the rest of this
    dashboard already applies to "generated_at" timestamps on the JSON
    reports. Grouping follows the real script boundaries (see
    scripts/combined_report.py's module docstring and each adapt_*/
    reconstruct_*.py script): Extraction+Reconstruction share
    reconstruct_ledger_entries.py's output (pdf_to_csv_converter.py's own
    output path varies per source PDF, so there's no single fixed file to
    check for it alone), Normalization is adapt_ledger_to_schema.py's
    output, and Detection+Reconciliation+Report all come out of one
    combined_report.py run together -- this project doesn't track those
    three separately today, so they share one "done" signal rather than
    three independently-verified ones."""
    reconstructed_done = os.path.exists(RECONSTRUCTED_ENTRIES_PATH)
    normalized_done = os.path.exists(GENERAL_LEDGER_PATH)
    report_done = report is not None

    stages = [
        {"name": "Extraction", "done": reconstructed_done},
        {"name": "Reconstruction", "done": reconstructed_done},
        {"name": "Normalization", "done": normalized_done},
        {"name": "Detection", "done": report_done},
        {"name": "Reconciliation", "done": report_done},
        {"name": "Report", "done": report_done},
    ]

    return {
        "documents_processed": len(list_raw_pdfs()),
        "stages": stages,
        "last_run": report.get("generated_at") if report else None,
        "gstr_last_run": gstr_report.get("generated_at") if gstr_report else None,
    }


def save_uploaded_file(filename, file_bytes):
    """Saves an uploaded raw source document into data/raw_pdfs/, under its
    own filename, so it's picked up by the existing glob-based adapters
    (adapt_gstr_to_schema.py's GSTR1_GLOB/GSTR3B_GLOB, parse_trial_balance.py,
    etc.) the next time the relevant pipeline is re-run.

    Only .pdf is accepted -- these are meant to be the raw source documents
    (a new month's GSTR-1/GSTR-3B, a replacement trial balance, etc.), not
    already-adapted CSVs, which are machine-generated by this project's own
    scripts and shouldn't be hand-uploaded (uploading one could silently
    diverge from what re-running the real adapter on the PDFs would
    produce). Raises ValueError for any other extension, with a message the
    UI can show directly, rather than silently accepting something the
    pipeline can't use.

    Returns the absolute path written to. Overwrites a file of the same
    name without warning -- the caller (dashboard/app.py) is responsible
    for telling the user when that's about to happen, since only it knows
    whether a same-named file already exists before the upload lands."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(
            f"Only PDF files are accepted here (got {filename!r}). "
            f"This is for raw source documents -- a new GSTR-1/GSTR-3B month, "
            f"a replacement trial balance or ledger PDF, etc. -- not the "
            f"already-adapted CSVs those get converted into."
        )
    os.makedirs(RAW_PDFS_DIR, exist_ok=True)
    dest_path = os.path.join(RAW_PDFS_DIR, os.path.basename(filename))
    with open(dest_path, "wb") as f:
        f.write(file_bytes)
    return dest_path


# Established documents this project ships with / was verified against.
# An upload must never silently overwrite one of these (they are the
# baseline other results and tests are pinned to, and data/raw_pdfs/ is
# gitignored so nothing could restore them) -- see save_upload().
PROTECTED_UPLOAD_NAMES = frozenset(
    [os.path.basename(info["pdf_path"]) for info in LEDGER_PDF_INFO.values()] + [
        "annual_tax_statement_redacted.pdf",
        "income_computation_redacted.pdf",
        "income_tax_acknowledgement_redacted.pdf",
        "trial_balance_redacted.pdf",
        "gstr1_june_2026_redacted.pdf",
        "gstr3b_june_2026_redacted.pdf",
        "GSTR1_All_Months_Anonymised.pdf",
        "GSTR3B_All_Months_Anonymised.pdf",
        "Index_Anonymised.pdf",
    ]
)


def _unique_filename(directory, filename):
    """'name.pdf' -> 'name_2.pdf', 'name_3.pdf', ... the first one not
    already present in `directory`."""
    stem, ext = os.path.splitext(filename)
    n = 2
    while True:
        candidate = f"{stem}_{n}{ext}"
        if not os.path.exists(os.path.join(directory, candidate)):
            return candidate
        n += 1


def save_upload(filename, file_bytes):
    """The dashboard's upload entry point (wraps save_uploaded_file(), which
    is unchanged). Returns (path, outcome), outcome one of:
      "saved"            -- a new file, written under its own name.
      "replaced"         -- same name as an earlier upload of the person's
                            own: overwritten (the old results are stale, the
                            caller offers re-processing).
      "saved_alongside"  -- the name belongs to one of this project's
                            established documents (PROTECTED_UPLOAD_NAMES),
                            which are never overwritten: the upload is kept
                            as its own document under 'name_2.pdf' and
                            analysed as such. This is why uploading a copy
                            of an established ledger now gives results in
                            Audit (a separate document) instead of silently
                            replacing the baseline.
    Same validation and errors as save_uploaded_file() (ValueError for a
    non-PDF)."""
    safe_name = os.path.basename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return save_uploaded_file(filename, file_bytes), "saved"  # raises the usual ValueError
    exists = os.path.exists(os.path.join(RAW_PDFS_DIR, safe_name))
    if exists and safe_name in PROTECTED_UPLOAD_NAMES:
        new_name = _unique_filename(RAW_PDFS_DIR, safe_name)
        path = save_uploaded_file(new_name, file_bytes)
        _forget_previous_results(path)
        return path, "saved_alongside"
    path = save_uploaded_file(safe_name, file_bytes)
    _forget_previous_results(path)
    return path, ("replaced" if exists else "saved")


def _forget_previous_results(path):
    """A file has just been saved at `path`: whatever results were recorded
    for an earlier document of that name no longer describe it. Removes the
    ledger's results record (ledger_meta__<name>.json) so Audit cannot show
    the OLD document's results under the NEW file's name while it is read, or
    indefinitely if reading it fails (a failed run never overwrites the
    record). The report and CSV files are replaced when the new file is
    processed. The established sample ledgers are skipped: their results do
    not come from this record."""
    if ledger_slug(path) in LEDGER_PDF_INFO:
        return
    try:
        os.remove(ledger_meta_path(path))
    except OSError:
        pass  # none recorded, or not removable: nothing stale to hide


def list_raw_pdfs():
    """Filenames currently in data/raw_pdfs/, sorted -- so the UI can show
    what's already on file (and warn before an upload overwrites one)
    without the caller needing to know RAW_PDFS_DIR's path directly."""
    if not os.path.isdir(RAW_PDFS_DIR):
        return []
    return sorted(
        name for name in os.listdir(RAW_PDFS_DIR)
        if os.path.isfile(os.path.join(RAW_PDFS_DIR, name))
    )
