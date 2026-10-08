# Financial Irregularity & Tax Compliance Detection — Project

A layered detection system (rule-based → statistical → unsupervised ML)
that finds financial inconsistencies in a company's accounting ledger and
cross-checks its tax filings against each other and against the ledger
itself. Built as a learning project against **real, anonymised documents**
from an actual petrol-pump firm's Chartered Accountant — not synthetic
data pretending to be real.

Status: **v1 finalized, 2026-10-04.** 9 detection rules, 618 tests
passing (the real-document checks skip where those gitignored PDFs
aren't present), both financial years' ledgers reconciled to a known, fully
explained residual. The dashboard's ledger tabs are now generated
dynamically from whatever ledger-shaped documents are actually uploaded
(content-classified, not filename-matched), and its sidebar's pipeline
re-run controls are now a single generic action that auto-detects which
specialized checks apply, rather than one dataset-specific button per
document — see "Dashboard: upload and test" below.

## What this project covers

**Real source documents** (`data/raw_pdfs/`, all PDF — see "Out of scope"
below for why nothing else is ingested yet):
- Two full Tally general-ledger exports: FY2024-25 (`ledgers_redacted.pdf`,
  frozen/committed baseline) and FY2025-26 (`Ledgers_Anonymised.pdf`,
  ~960 pages each)
- The official Tally trial balance for FY2024-25
- ITR-V acknowledgement, Computation of Income, and Form 26AS (Annual Tax
  Statement) for the firm
- GSTR-1 and GSTR-3B, both as individual monthly filings and as
  combined "All Months" PDFs (16 months, April 2025–July 2026)

**9 detection rules**, run by `combined_report.py`:

| # | Rule | Kind | What it checks |
|---|------|------|-----------------|
| 1 | Segregation of duties | Per-leg | Same person entered & approved a voucher |
| 2 | Round-number bias | Per-leg | Exact ₹10,000 multiples are screened (₹10,000 is a real threshold in Indian tax practice, e.g. the Section 40A(3) cash limit). On its own a round amount is only LOW; it rises to HIGH/CRITICAL by amount when another rule (e.g. duplicate transaction) fires on the same leg |
| 3 | Benford's Law | Dataset | First-digit distribution vs. natural expectation (chi-square + Nigrini MAD) |
| 4 | ITR vs. Form 26AS | High-confidence | TDS claimed vs. TDS actually deducted by third parties |
| 5 | Duplicate transaction | Per-leg | Same account/amount/date/direction on a different voucher. Sub-rupee postings to a rounding account are skipped (invoice rounding repeats by nature); a rounding-account posting of Re.1 or more is still checked |
| 6 | Trial balance vs. ledger | High-confidence | Official Tally trial balance vs. the reconstructed ledger, per account |
| 7 | Voucher-number sequence gaps | High-confidence | Missing numbers in a Tally auto-numbered series |
| 8 | ML anomaly detection | Dataset | Isolation Forest over amount/account/direction/date features — catches shapes no hand-written rule looks for |
| — | GSTR-1 vs. GSTR-3B | High-confidence | Monthly GST filings cross-checked against each other (`rule_gstr_reconciliation.py`, run separately — see below). The sample returns are compared with each other, and the returns you upload are compared with each other as a separate report; the two are never mixed. A month present in only one of the two returns is shown as "Not compared", not as a missing filing |
| — | Ledger vs. GSTR-1 | High-confidence | The ledger's own recorded taxable *and* non-GST supply vs. the GSTR-1 returns supplied for it, month by month (`rule_ledger_gstr_reconciliation.py`). Works for any processed ledger: each uploaded ledger is checked against the GSTR-1 documents you uploaded, the sample ledger against the sample returns |

Every rule is a **candidate list for human review, not a verdict** — this
is stated explicitly in each rule's own docstring and is the whole
project's operating philosophy.

## Folder structure

```
audit_project/
├── data/
│   ├── raw_pdfs/                       # real source documents (see above)
│   ├── general_ledger.csv              # FY2024-25, reconstructed + adapted
│   ├── general_ledger_FY2025-26.csv    # FY2025-26, reconstructed + adapted
│   ├── form26as.csv / itr_summary.csv  # real ITR/26AS, adapted
│   ├── gstr1_summary.csv / gstr3b_summary.csv  # real GSTR filings, adapted
│   └── synthetic/                      # generate_data.py's output (ground-truth testing only)
├── scripts/
│   ├── reconstruct_ledger_entries.py   # raw Tally PDF -> reconstructed ledger CSV
│   ├── adapt_*_to_schema.py            # real documents -> canonical rule-engine schema
│   ├── rule_*.py                       # the 9 detection rules, one file each
│   ├── combined_report.py              # runs Rules 1/2/3/5/7/8 together, ranked output
│   ├── source_resolver.py              # content-based document classification cache + source selection
│   ├── tax_compliance_report.py        # ITR vs 26AS + trial balance from whichever documents are on file
│   └── generate_data.py                # synthetic data with planted ground-truth issues
├── tests/                              # 618 tests: synthetic-logic, real-data regression, real-data smoke
├── dashboard/                          # Streamlit viewer (dynamic ledger tabs) over the report output
│   ├── jobs.py / ingest.py             # background worker + upload routing (no Streamlit dependency)
│   ├── plain.py                        # plain-language wording for what the dashboard shows (display only)
│   └── requirements.txt                # dashboard-only deps, pulls in ../requirements.txt
├── requirements.txt                    # core engine deps (pandas, pdfplumber, scikit-learn)
├── requirements-dev.txt                # + pytest/reportlab, pulls in requirements.txt
└── BASELINE_CHECKPOINT_V3.*.md         # one doc per investigation, in order, each with its own evidence trail
```

## How to run

Install dependencies first (added 2026-10-04 -- see "No module named
'sklearn'" below for why this matters):

```
pip install -r requirements-dev.txt   # core engine + dashboard + dev/test deps, all in one
# -- or, for a non-dev install (no pytest/reportlab): pip install -r dashboard/requirements.txt
```

```
cd scripts
python3 combined_report.py                 # FY2024-25: Rules 1/2/3/4/5/6/7/8 together
python3 -c "from combined_report import run_ledger_only_report; \
            run_ledger_only_report('../data/general_ledger_FY2025-26.csv', 'FY2025-26')"
python3 rule_gstr_reconciliation.py         # GSTR-1 vs GSTR-3B, 16 months
python3 rule_ledger_gstr_reconciliation.py  # ledger vs GSTR-1, 12 overlapping months
```

Dashboard: `streamlit run dashboard/app.py` (reads the exported `output/*.json`/`*.csv`).
Its own sidebar has a single generic **🔁 Re-run Analysis** button that
does all four of the above automatically, in the background, choosing the
source documents by their content rather than their filenames (see "Generalized architecture"
below) -- running the scripts by hand above is for direct/offline use.

Tests: `pytest` from the project root (618 tests, ~330s; `pytest -m "not slow"`
skips 4 real end-to-end ledger-reconstruction tests for a quicker run
during iteration).

## Dashboard: upload and test (generic ledger ingestion)

The dashboard's top navigation is grouped into 3 sections -- **Process**,
**Audit**, **Tax & Compliance** -- each a tab with its own nested tabs or
controls underneath where it has more than one page. **Audit** shows a
**Ledger** dropdown instead of nested tabs: pick any ledger processed
through this deployment's own generic upload-and-process flow, and its
own intrinsic checks render below -- KPIs, priority findings, severity
breakdown, the full flags table, Benford's Law, voucher-number sequence
gaps, and ML anomaly detection. ITR vs Form 26AS and Trial Balance
reconciliation live only in **Tax & Compliance**, not duplicated in
**Audit** -- the dropdown's own caption says so, worded per-document
(correctly, since that data doesn't generalize to an arbitrary new
ledger -- see "Out of scope" below).

**Audit deliberately never shows the two established development
documents' (FY2024-25/FY2025-26) stored results** (2026-10-04, same
day): with nothing processed through the generic flow this run, Audit
shows one line -- "No audit results available yet. Upload a ledger under
Process -- it is analysed automatically and appears here when it
finishes." -- no dropdown, no metrics, nothing carried
over from this project's own development datasets. This is a
deliberate product decision, not a bug: a generalized audit portal for
an arbitrary company should never silently show a different company's
(or this project's own test) numbers just because they happen to
already be computed and sitting on disk. The frozen FY2024-25 baseline's
segmentation evidence (the deeper voucher-type/account Benford breakdown,
`scripts/analyze_benford_segmentation.py`) is consequently no longer
reachable from **Audit** at all under this rule; the code that renders
it is kept, commented as intentionally unreachable, pending a deliberate
decision on whether a developer-only "view the baseline anyway" path is
worth adding back.

This replaces an earlier version (2026-10-04, same day) where **Audit**
was itself three tabs (Overview / Ledger Findings / Benford's Law)
permanently hardcoded to the FY2024-25 baseline's report regardless of
what was actually processed, while a separate **Documents** group held
the correctly per-document view for anything else -- direct bug report:
"whether i put 2024 file or 2025 file it shows the same page". Fixed by
retiring the split itself: one dropdown, under **Audit**, covering every
processed ledger including the frozen baseline. **Documents** no longer
exists as a nav item; nothing it did was removed, it was folded into
**Audit**.

The dashboard's ledger tabs are **not** tied to hardcoded filenames, and
the whole upload-and-test workflow lives in one place, in order, top to
bottom: **Process** -> **Process Documents**. Upload a PDF there (a
prominent drop zone, not a bare default widget) and it's classified by
its actual content (`scripts/classify_document.py`), not its name; a
newly uploaded, not-yet-processed ledger shows up immediately below as
an open "N document(s) ready to process" card with a **Process
Document** button and a **Ready to Process** status badge, right there
-- nothing elsewhere to go find. Uploading is not just "save the file":
the document is classified by content and **processed automatically** --
a ledger gets a new entry in **Audit**'s dropdown; a 26AS / ITR / trial
balance / GSTR document triggers the analysis that updates **Tax &
Compliance**; anything unrecognised is reported with the reason.
Re-uploading an earlier upload of your own is marked "✓ Document
replaced" and re-processed (its old result was stale the moment the file
underneath it changed). Uploading a file named like one of this project's
established documents (e.g. `ledgers_redacted.pdf`) never overwrites it:
it is kept as `ledgers_redacted_2.pdf` and analysed as its own document.

Clicking **Process Document** shows a centered, multi-stage processing
card in the main content area -- Document uploaded / Scanning document /
Extracting data / Running audit checks / Preparing results, the active
one spinning, the rest checked off or still pending -- tied to the
pipeline's own real internal stages (no placeholder progress bar, no
invented percentage), then a durable, stage-by-stage outcome banner
(classification / reconstruction -- voucher and unbalanced counts /
validation confidence / audit analysis) once it's done. A successful
result gets its own entry in **Audit**'s **Ledger** dropdown,
auto-labeled from the document's own transaction dates (e.g. "FY2025-26"
for an exact Indian financial year, or a literal date range otherwise).
Everything already handled -- processed ledgers, the two established
baselines, non-ledger documents -- collapses into a single status table
(Document / Type / Status / **Used by** / Last processed; status is
always one of Uploaded / Ready to Process / Processed / Failed) under a
closed "N document(s) already on file" expander, so the tab stays about
what's actionable, not a dump of every file sitting in `data/raw_pdfs/`
(an internal folder name nobody using the dashboard needs to know). The
**Used by** column names the generic analysis a classified document
feeds (e.g. "Tax & ledger reconciliation", "GST reconciliation") --
picked automatically from the document's own classified type, not
hardcoded per filename (see "Generalized architecture" below). The
sidebar's own Data panel is a compact documents/processed count plus an
expandable document list, with a single **🔁 Re-run Analysis** button
below that (see "Generalized architecture") and the two established
documents' own dedicated, safety-gated reconstruction controls tucked
under a collapsed "Advanced" expander, since that's per-document
maintenance, not a primary everyday action.

### Generalized architecture (2026-10-04)

The sidebar used to show four separate buttons, each named after one
specific document from this project's own development data ("Re-run
ledger + tax pipeline", "Re-run GSTR pipeline", "Re-run FY2025-26 ledger
pipeline", "Re-run Ledger vs GSTR-1 pipeline") -- fine for this project's
own two real documents, wrong as a primary UI action for a product meant
to be "a generalized audit inconsistency detection portal" a future user
will point at entirely different entities and periods, since it required
already knowing which internal pipeline name matched which uploaded
document. Fixed by replacing all four with one generic **🔁 Re-run
Analysis** button: it normalizes whatever source documents are currently
in `data/`, runs consistency checks and audit analysis against them, and
regenerates the matching report(s), auto-detecting which of this
project's four specialized pipelines apply by simply trying each one and
reading the result -- a pipeline whose required documents aren't present
is reported as "skipped" (not an error; the normal case for a future user
without, say, any GSTR filings at all), a real failure is reported as
such, and a missing optional dependency (see "No module named 'sklearn'"
below) is flagged distinctly from both, clearly labeled as a setup
problem rather than an audit finding. Nothing the button calls underneath
changed -- same four pipelines (`dashboard/data.py`'s
`run_combined_pipeline()` / `run_gstr_pipeline()` /
`run_fy2025_26_pipeline()` / `run_ledger_gstr_pipeline()`), same rule
engine, same report files; only *which one(s) run, and when* is now
decided automatically instead of by a person picking the right button.
See `run_full_analysis()`'s own docstring in `dashboard/data.py` for the
exact applicability logic.

A document whose specialized checks still need an exact, established
filename (a GSTR-1/GSTR-3B month, a trial balance, a tax statement -- see
"Out of scope" below) isn't fully generic yet; what changed is that the
dashboard now tells you this per-document, dynamically, via the **Used
by** column above, right where that document is classified, instead of
a blanket sidebar caption naming every special case up front whether or
not it applied to anything currently on file.

### "No module named 'sklearn'"

Rule 8 (`scripts/rule_ml_anomaly_detection.py`, unsupervised ML anomaly
detection via Isolation Forest -- a real, tested, documented part of the
detection engine, not a development-only feature) depends on
`scikit-learn`, which was used throughout `scripts/` without ever being
declared in a requirements file -- an environment set up from
`pip install -r requirements.txt` alone (rather than whatever happened to
already be installed) was missing it, surfacing as "Pipeline run failed:
No module named 'sklearn'" the moment any report-generating pipeline ran,
since `combined_report.py` imports it at module level. Fixed by adding
`scikit-learn` (and `pdfplumber`, also previously undeclared) to the new
root `requirements.txt`; see "How to run" below for the install command.
This was an environment/setup gap, not an obsolete feature -- fixed by
declaring the dependency correctly, not by removing or isolating the
rule. If this ever happens again on a NOT-yet-set-up environment, it's a
one-line `pip install` fix, not an audit finding.

Document discovery (classifying every PDF in `data/raw_pdfs/`, including
this project's two ~960-page ledger PDFs) is cached **on disk**
(`output/classification_cache.json`, keyed by filename + modification time
+ size, saved after every file so an interrupted scan keeps its progress)
as well as in memory. The first ever scan of the 11 real documents takes
~7s; every later load -- including after a server restart -- classifies
nothing and takes well under a second. An upload only ever re-classifies
the file(s) that actually changed. While a scan is genuinely needed, the
page shows a real progress bar ("Reading documents... 4 of 11") instead
of a silent wait; on a warm load there is nothing to wait for and the
page just appears. Pages that only read already-computed JSON (Audit,
Tax & Compliance) do not wait on the scan at all.

**Uploading before the dashboard has finished loading is safe.** All
document processing (ledger reconstruction, "Re-run Analysis", routing an
uploaded tax/GST document) runs on a single background worker thread
(`dashboard/jobs.py`) in the server process, not inside the Streamlit
script. So clicking around, refreshing the page, or uploading a second
file while something is running never aborts it; uploads made during the
initial load are queued ("Waiting in line") and processed one after
another, in order, with a live status card showing what is running and
what is waiting. A document finished while the page was away is shown the
next time it renders.

This never crashes on any document: an unreadable, empty, or unrelated
PDF is classified `"unknown"` and skipped cleanly; a ledger-shaped PDF
that reconstructs poorly (too few vouchers, too many unresolved lines or
unbalanced vouchers relative to this project's own two real, fully-
verified baselines) still gets its own tab, but with a prominent
low-confidence warning banner up front naming the specific reasons —
never silently shown with the same authority as the two established
documents. A PDF that reconstructs to literally **zero** vouchers (its
headers looked ledger-shaped, but nothing in it resolves into even one
complete voucher) doesn't get a tab at all — there's nothing in an empty
dataset to show — and fails cleanly with that exact reason instead of
crashing. It cannot literally guarantee a perfect result on every
accounting system's export format in existence (a different package's
layout, or a scanned/image-only PDF, may still reconstruct poorly), but
it will always either work correctly or say clearly that it doesn't
trust its own result, rather than fail or mislead.

Rules 4 (ITR vs Form 26AS) and 6 (Trial Balance) are driven by whichever
tax documents are on file (chosen by content, see "Out of scope" below
for the exact pairing rules). The Ledger-vs-GSTR-1 reconciliation works
for every processed ledger: it recognises tax and rounding accounts by
name pattern, takes non-GST sales from sales-type vouchers, and pairs a
ledger only with GSTR-1 returns of its own origin (see "Out of scope"
below). GSTR-1 vs GSTR-3B follows the same rule: sample returns are
compared with sample returns, uploaded returns with uploaded returns.

To regenerate synthetic ground-truth data (only used by tests, never
committed as "real" data): `python3 scripts/generate_data.py`.

## Out of scope (final, as of v1 — not open TODOs)

These are not bugs and not left incomplete by accident. Each is closed out
deliberately because no real document exists to build or verify against,
and this project's discipline is to never guess a layout or threshold it
hasn't seen real data for:

- **AIS (Annual Information Statement)**: an original target document,
  but no AIS for this firm has ever been supplied. (An earlier version of
  this bullet said no AIS PDF had ever been in `data/raw_pdfs/` at all --
  inaccurate, corrected 2026-10-06: `AIS.pdf` is there, but it is an
  individual's FY2025-26 statement from the same batch of unrelated-person
  documents described in `scripts/adapt_tax_docs_to_schema.py`'s HISTORY
  note, and it does not involve the firm's deductor, so it can't be used
  against this ledger. `classify_document` returns `unknown` for it.) ITR
  vs. Form 26AS (Rule 4) already provides the equivalent direct TDS
  cross-check.
- **Excel/XML Tally export**: every real document on hand is a PDF.
  Tally can export the same ledger as Excel or XML, and either would
  likely be *easier* to ingest than the PDF path (most of
  `reconstruct_ledger_entries.py`'s complexity exists only to undo PDF
  print-layout damage) — there's simply no sample file yet to build an
  adapter against. Send one and this becomes a straightforward addition,
  not a redesign.
- **Segregation of duties (Rule 1) on real data**: structurally inert.
  Tally ledger/bank exports carry no `entered_by`/`approved_by` user
  attribution — the logic is correct and tested against synthetic data,
  but has nothing to find in any real document this project has.
- **Duplicate-transaction (Rule 5) blind spot**: cannot distinguish a
  genuine repost from two independent transactions that happen to share
  account/amount/date/direction (verified real case: a repeat
  vendor, two legitimate payments). Deliberately left as a
  candidate-for-review rather than special-cased away, since excluding
  it would risk hiding a real duplicate with the same shape. The one
  exclusion (2026-10-07) is not a pattern like that: sub-rupee postings
  to a rounding account (50 paise or less in practice) are skipped,
  because rounding repeats by design -- they were 129 of 215 duplicate
  flags on the FY2024-25 sample ledger.
- **Benford segmentation evidence (the by-voucher-type/account breakdown
  behind why this project leans on MAD over chi-square) is FY2024-25
  only**: `scripts/analyze_benford_segmentation.py` reads
  `data/general_ledger.csv` directly rather than taking a ledger path
  parameter. Every ledger tab shows the same Rule 3 chi-square/MAD
  verdict either way. As of 2026-10-04 (Addendum 12), this deeper
  breakdown is not reachable from the dashboard at all — the code that
  renders it is still there, but it only ever fired for the frozen
  FY2024-25 baseline selection, and that baseline is now deliberately
  excluded from **Audit**'s picker (see "Dashboard: upload and test"
  above), so the branch is unreachable UI, not a silently-omitted
  feature. Extending the script to take a ledger path parameter (the
  same way `combined_report.run_ledger_only_report()` was for Rules
  1/2/3/5/7/8) and deciding whether/how to resurface the result would
  both need to happen together — a real, bounded follow-up, not a
  blocked-on-data item like the others in this list.
- **Ledger-vs-GSTR-1 now works for any processed ledger** (updated
  2026-10-07; supersedes the earlier "Rules 4/6 and Ledger-vs-GSTR-1 are
  not part of the generic upload flow" entry).
  Tax & Compliance is content-driven: an uploaded Form 26AS,
  Computation of Income, ITR-V, trial balance, GSTR-1 or GSTR-3B is
  recognised by what it contains, under any filename, and feeds
  `output/tax_compliance_report.json` and the GST report after the
  background analysis runs. ITR vs 26AS pairs documents of the **same
  assessment year** only (a set with a missing partner, or partners from
  different years, is reported as "incomplete" with the reason -- never
  reconciled across years); a trial balance is reconciled against the
  processed ledger whose dates fall inside the trial balance's own
  period (none -> "no matching ledger", never the wrong year).
  **Ledger vs GSTR-1** pairs by origin: the sample ledger with the
  built-in sample returns, and every ledger you process with the GSTR-1
  documents you upload (`output/ledger_gstr__<ledger>.json`), never an
  uploaded ledger with another business's sample returns. Its limits,
  stated rather than hidden: it assumes the uploaded returns belong to the
  same business as the ledger (the redacted documents carry no GSTIN or
  name to check); tax accounts are recognised by name ("CGST", "Output
  CGST @9%", "Central Tax", "UTGST", ...), so an unusual naming could be
  missed -- the tab then reports that nothing could be compared; and
  non-GST sales are the credit side of sales-type vouchers (voucher type
  containing "sale") with no tax line, with the accounts counted listed
  under the tab's Technical details. A ledger month with no GSTR-1 is
  flagged as missing only when it falls between two returns that were
  uploaded; months outside the uploaded returns are reported as "not
  compared", because not having uploaded a return is not evidence it was
  never filed. Established documents are never overwritten by an upload
  of the same name; the copy is kept alongside (`name_2.pdf`) and
  analysed as its own document. A file saved under a name used before (or one whose earlier
  copy was deleted) starts with no results: the earlier file's results
  are removed when the new file arrives, so Audit can never show them as
  the new file's. While a document is being read, the page names it and
  says what is, and is not, included in the numbers on screen.
  **GSTR-1 vs GSTR-3B** pairs by origin in the same way (updated
  2026-10-07): the built-in sample returns are compared with each other
  (`output/gstr_report.json`, unchanged), and the GSTR-1 and GSTR-3B you
  upload are compared with each other (`output/gstr_report__uploaded.json`).
  An uploaded return is never compared with a sample return, and a sample
  return is never used in place of an uploaded one for the same month. A
  month that has one return but not the other, a missing GSTR-1 or GSTR-3B
  altogether, or two sets with no month in common, is shown as "Not
  compared" with the reason -- never as a filing failure, because not
  having uploaded a return is not evidence that it was not filed. The GST
  returns tab names the exact documents used and the months compared. All
  uploaded returns are treated as belonging to one business (the redacted
  documents carry no GSTIN or name to check).
- **Company-level / multi-document "audit session" grouping**: there is no
  persisted concept anywhere in this codebase of several source documents
  (a ledger plus its GSTR-1, trial balance, etc.) belonging together as
  one client's one-FY audit. **Audit**'s picker switches between
  individual processed ledger documents one at a time, by design — that
  matches the unit its checks (Benford, duplicate vouchers) actually
  operate on, which is a single ledger, not a bundle. Decided 2026-10-04:
  deliberately kept out of the UI rather than implied — no fabricated
  company name, document count, or "audit bundle" label anywhere, since
  none of those concepts are backed by real state. If a true multi-document
  audit session is wanted later, it needs its own data model (what
  documents belong to which session, who assigns them) — a separate,
  larger feature, not a label change.

## Known, explained residual

Each real ledger has its own small, fully investigated, deliberately-left
unbalanced-voucher residual — two **different** numbers, not the same one
twice:

- **FY2024-25** (`ledgers_redacted.pdf`, 6,216 vouchers): **2** unbalanced
  — `Payment_2` and `Sales_Payment`, a single header-regex ambiguity where
  a truncated counterparty name ("Bhagwati Sales Corporation" →
  "...Sales") happens to collide with a real voucher-type word
  ("Payment"), producing one bogus split entry from what is really one
  voucher. Deliberately left unfixed — the blast radius of touching the
  shared header regexes for one isolated occurrence in 926 pages wasn't
  justified by the evidence (see `BASELINE_CHECKPOINT_V3.10.md` §5, §9).
- **FY2025-26** (`Ledgers_Anonymised.pdf`, 5,947 vouchers): **3**
  unbalanced — `Journal_777`, `Journal_778`, `Journal_779`, after that
  ledger's own separate header-detection investigation (see
  `BASELINE_CHECKPOINT_V3.13.md` for the full trace).

Both are documented, investigated, known residuals, not unexamined error
counts — and both counts are load-bearing elsewhere in this project (the
dashboard's ledger-reconstruction safety check in `dashboard/data.py`'s
`LEDGER_PDF_INFO` compares a fresh re-run against each PDF's own baseline
above, not a shared one).
