# BASELINE CHECKPOINT V3.14 — v1 Finalization

## 0. Relationship to V3.13

V3.13 (`BASELINE_CHECKPOINT_V3.13.md`) closed out the FY2025-26
header-detection bug chain and the non-GST reconciliation breakthrough.
This checkpoint is a different kind of milestone: not a bug investigation,
but a deliberate **finalization pass** — closing out every remaining open
item with no new source documents available, triggered by the explicit
decision to finalize the project as it stands.

Two things prompted this pass directly:
1. A question about whether this project duplicates an existing public
   GitHub project (it doesn't — see §4).
2. A clarification that the FY2025-26 ledger's heavier redaction (more
   removed than the FY2024-25 ledger) is consistent with, and explains,
   the continuation-page name-change phenomenon documented in
   `BASELINE_CHECKPOINT_V3.13.md` §18.3 — the newer document's redaction
   pass was less consistent page-to-page than the older one's.

## 1. What changed this pass

### 1.1 New: Rule 8, unsupervised ML anomaly detection

`scripts/rule_ml_anomaly_detection.py` — an Isolation Forest over
per-leg features (log-amount, per-account amount z-score, debit/credit
direction, day-of-month, round-10k flag, and voucher-type rarity when
present). This was always the third tier of the originally-planned
"rule-based → statistical → unsupervised ML" architecture and had never
been built. It needed no new source document — both real ledgers
(`general_ledger.csv`, `general_ledger_FY2025-26.csv`) already had
everything the feature set uses.

Deliberately excludes `entered_by`/`approved_by`/`timestamp` as features:
those are blank on every real row this project has (same root cause as
Rule 1's documented limitation — see `rule_segregation_of_duties.py`),
so including them would have the model "detect" nothing but
synthetic-vs-real provenance, not a real signal.

**Honest validation against synthetic ground truth** (not tuned to look
good — see `tests/test_rule_ml_anomaly_detection.py`):

| Planted issue type | Recall | Why |
|---|---|---|
| `round_number` | 14/19 (74%) | Directly encoded as a feature |
| `same_approver` | 2/15 (13%) | Needs `entered_by` — deliberately excluded |
| `off_hours` | 1/16 (6%) | Needs `timestamp` — deliberately excluded |
| `duplicate_source`/`duplicate_copy` | 0/12 each | Exact-match duplicates aren't outliers to an Isolation Forest — that's Rule 5's job, not this rule's |
| Clean rows flagged | 43/1,138 (3.8%) | In line with the ~5% severity-band design target |

**Real-data results**:
- FY2024-25: 731 flags (146 high / 292 medium / 293 low) out of 14,625 legs
- FY2025-26: 699 flags (139 high / 280 medium / 280 low) out of 13,981 legs

Both runs are deterministic (seeded `IsolationForest(random_state=42)`,
pinned by `test_detect_anomalies_is_deterministic`) and produce
explainable top flags on manual inspection: very-small-amount Debit Notes
on vendor accounts that usually carry large amounts, and large round-number
Contra (bank/cash transfer) entries that are genuinely rare relative to
typical legs on those accounts.

Wired into `combined_report.py` as its own dataset-level section (same
reporting shape as Benford's Law, for the same reason: it's evidence about
where to look, not a named accusation against one leg), for both
`run_combined_report()` and `run_ledger_only_report()` via
`compute_ledger_intrinsic_flags()`.

15 new tests in `tests/test_rule_ml_anomaly_detection.py`. Full suite:
**381 tests, 381 passing** (366 + 15 new).

### 1.2 Formal closure of data-blocked limitations

Not code fixes — explicit, final scope statements added so the finalized
project doesn't read as having open TODOs where none can honestly be
closed without new documents:

- `scripts/adapt_tax_docs_to_schema.py` — AIS (Annual Information
  Statement) explicitly marked final-scope: no AIS PDF has ever been
  supplied; Rule 4 (ITR vs. 26AS) already covers the equivalent direct
  TDS cross-check.
- `scripts/pdf_to_csv_converter.py` — Excel/XML Tally export explicitly
  marked final-scope: every real document on hand is a PDF; a real
  Excel/XML sample would likely be *easier* to ingest than the PDF path
  (most of `reconstruct_ledger_entries.py`'s complexity exists only to
  undo PDF print-layout damage), but there is no sample to build or
  verify an adapter against.
- Rule 1 (segregation of duties) and Rule 5 (duplicate-transaction blind
  spot) were already thoroughly documented as deliberate, final design
  decisions in their own modules — no further closure needed there.

### 1.3 README.md rewritten

The previous `README.md` dated from the original 4-rule,
synthetic-data-only version of this project (no reconciliation rules, no
real documents, no ledger reconstruction). It is now a complete, accurate
description of the current state: all 9(+2) rules, the real documents
used, how to run everything, and the "Out of scope (final)" section
mirroring §1.2 above.

### 1.4 Public-repository check

Searched publicly for an existing project combining Tally ledger
reconciliation + ITR/26AS + GSTR-1/GSTR-3B + Benford's Law into one
pipeline. Found none — the closest public material is academic/research
papers and ICAI write-ups on using Benford's Law for GST fraud detection
generally (SAI India, ICMAI, individual CA blogs), not working code doing
this specific combination. Separately confirmed this project's own git
history has never been pushed anywhere: `.git/config` on the local
working copy (`C:\Users\<user>\Desktop\audit_project`) has no
`[remote "origin"]` entry, and no GitHub account is linked to the session
that did this check.

## 2. What deliberately did NOT change

- The frozen FY2024-25 ledger's reconstruction output — untouched, as in
  every prior checkpoint.
- The 2 FY2024-25 continuation-name-alias candidates from V3.13
  (`apply_continuation_name_aliases=False` for that document) — still
  implemented, still inert, still pending separate review before being
  applied to that frozen baseline.
- No Excel/XML adapter, no AIS adapter, no fix to Rule 1 or Rule 5's real-
  data limitations — these are closed as final scope (§1.2), not solved,
  because solving them would require guessing without a real sample.

## 3. Final state

- **9 detection rules** (1 segregation-of-duties, 2 round-number,
  3 Benford's Law, 4 ITR-vs-26AS, 5 duplicate-transaction, 6 trial-balance,
  7 voucher-sequence-gap, 8 ML anomaly detection) plus 2 standalone GST
  reconciliation checks (GSTR-1-vs-GSTR-3B, ledger-vs-GSTR-1).
- **381 tests, 381 passing.**
- Both real ledgers (FY2024-25 frozen, FY2025-26) reconciled to the same
  known, fully-investigated residual (3 of 5,947 unbalanced vouchers each,
  identical amounts, documented since V3.13).
- Every remaining known limitation is now either fixed, or formally closed
  as final scope with a stated reason and a stated condition for revisiting
  it (a real sample document) — nothing is left as a vague, undated
  "known issue."

## 4. Files touched this pass

- `scripts/rule_ml_anomaly_detection.py` (new)
- `tests/test_rule_ml_anomaly_detection.py` (new, 15 tests)
- `scripts/combined_report.py` (Rule 8 wired into both report entry points)
- `tests/test_run_ledger_only_report.py` (tuple-unpacking + shape updates
  for Rule 8)
- `scripts/adapt_tax_docs_to_schema.py` (AIS final-scope note)
- `scripts/pdf_to_csv_converter.py` (Excel/XML final-scope note)
- `README.md` (full rewrite)
- `BASELINE_CHECKPOINT_V3.14.md` (this file)

## 5. Addendum — dashboard upload-to-re-run gap found and fixed (same day)

A direct follow-up question ("is the dashboard all working end to end")
prompted checking, rather than assuming, whether the sidebar's document
uploader actually did what its own caption claimed ("saved to
data/raw_pdfs/ and picked up the next time you re-run the relevant
pipeline"). It didn't, for two of three re-run buttons:

- `dashboard/data.py`'s `run_combined_pipeline()` called only
  `combined_report.run_combined_report()`, which reads `data/form26as.csv`
  / `data/itr_summary.csv` directly — a replacement tax-statement PDF
  uploaded via the sidebar was saved but never re-adapted, so Rule 4
  (ITR vs 26AS) silently kept using the old data.
- `run_gstr_pipeline()` called only
  `rule_gstr_reconciliation.run_gstr_report()`, which reads the
  pre-adapted `gstr1_summary.csv`/`gstr3b_summary.csv` directly — a new
  month's GSTR-1/GSTR-3B PDF uploaded via the sidebar was saved but never
  picked up at all.

Trial balance (Rule 6) needed no fix: `reconcile_trial_balance.reconcile()`
already re-parses `trial_balance_redacted.pdf` fresh on every call (its
`tb_rows` parameter defaults to `None`, triggering a live re-parse), so a
same-named replacement already worked correctly before this fix.

**Fix**: both functions now call the matching adapter
(`adapt_tax_docs_to_schema.adapt_tax_docs()` /
`adapt_gstr_to_schema.adapt_gstr_docs()`) before running the rule engine.
Verified safe before wiring in: called both adapters fresh against the
real, unchanged raw PDFs and diffed their output against the currently
committed CSVs — byte-identical in all four files
(`gstr1_summary.csv`, `gstr3b_summary.csv`, `form26as.csv`,
`itr_summary.csv`). Re-ran the full test suite after wiring (381/381
still passing, including all 76 dashboard tests) and smoke-tested both
fixed functions directly, plus launched the Streamlit app headlessly and
confirmed it serves HTTP 200 with no errors in its log.

**Believed NOT fixable the same way at the time — a replacement ledger
PDF**: this section originally claimed ledger replacement needs a
human-chosen `start_page`/`end_page` for `reconstruct_ledger_entries.py`,
so it was left as a manual two-step CLI process, and the sidebar's
upload caption was written to say so explicitly. **That claim was wrong
and has been corrected the same day — see section 6 below.** It was
reasoned about, not verified against this project's own history, and
re-checking it empirically (every real committed reconstruction run has
always used the whole document) showed the page range was never
actually a judgment call.

Files touched in this addendum: `dashboard/data.py`, `dashboard/app.py`.

## 6. Addendum 2 — full dashboard completion pass (same day): FY2025-26 ledger, Ledger vs GSTR-1, and the corrected ledger-reconstruction claim

A direct, explicit instruction ("complete all the loopholes you've left in
the dashboard, I want it complete and perfect") prompted a harder look at
what was still invisible or wrong in the dashboard, beyond the
upload-to-re-run gap fixed in section 5. Found and fixed four more:

### 6.1 The entire FY2025-26 ledger report had zero dashboard presence

`output/combined_report_FY2025-26.json` (Rules 1/2/3/5/7/8 against the
second real financial year's ledger, via `combined_report.run_ledger_only_
report()`) has existed, fully computed and tested, since Rule 8 was built
earlier the same day — but nothing in `dashboard/data.py` or
`dashboard/app.py` ever loaded or displayed it. Fixed: a new **"FY2025-26
Ledger"** tab, built by reusing every existing reusable transform
(`kpi_summary`, `priority_findings`, `severity_breakdown_df`,
`voucher_severity_breakdown_df`, `ledger_flags_dataframe`,
`benford_summary`) against this report instead of the FY2024-25 one, plus
two new transforms for data this report carries that the FY2024-25 tabs
had never surfaced either (see 6.2), and a new sidebar button ("Re-run
FY2025-26 ledger pipeline" → `run_fy2025_26_pipeline()`).

### 6.2 Rules 7 and 8 had zero dashboard presence, even for FY2024-25

While wiring up the FY2025-26 tab, found that **Rule 7 (voucher-number
sequence gaps)** and **Rule 8 (ML anomaly detection)** — both computed and
exported to `combined_report.json` since Rule 8 was built — had never been
displayed anywhere in the dashboard, including the existing FY2024-25
tabs. Fixed: `voucher_gap_dataframe(report)` and
`ml_anomaly_dataframe(report)`/`ml_anomaly_summary(report)` added to
`dashboard/data.py` (both work against either report shape, FY2024-25 or
FY2025-26, since `compute_ledger_intrinsic_flags()` produces the same
shape for both), surfaced on the new FY2025-26 tab. `ml_anomaly_dataframe`
also normalizes `rule_ml_anomaly_detection.py`'s lowercase severity
strings ("high"/"medium"/"low") to uppercase, matching every other rule's
convention and this dashboard's `SEVERITY_COLORS`/`SEVERITY_ORDER`
constants, which are keyed uppercase.

### 6.3 The ledger-vs-GSTR-1 reconciliation — this project's original motivating finding — had zero dashboard presence

`rule_ledger_gstr_reconciliation.py` (the check that traced the project's
very first open question — the ledger's Sales account Dr/Cr pattern — to
the header-detection bug fixed in V3.13, then confirmed the fix by
matching the FY2025-26 ledger's own derived taxable and non-GST supply
against the filed GSTR-1 returns to the rupee on all 12 overlapping
months) has been fully built and tested since V3.13 closed, with its own
`output/ledger_gstr_reconciliation_report.json` — and had never been
shown in the dashboard at all. Fixed: a new **"Ledger vs GSTR-1"** tab,
`ledger_gstr_summary()` added to `dashboard/data.py` (mirrors
`gstr_summary()`'s shape, adapted for this report's two separate period
lists — taxable activity and non-GST activity — instead of one), and a
new sidebar button ("Re-run Ledger vs GSTR-1 pipeline" →
`run_ledger_gstr_pipeline()`).

Found in passing: the committed `output/ledger_gstr_reconciliation_report
.json` on disk predated the non-GST supply check being added to
`rule_ledger_gstr_reconciliation.py` — its `dataset` had
`ledger_periods_with_taxable_activity` but no
`ledger_periods_with_non_gst_activity` key yet. `ledger_gstr_summary()`
reads it with `.get(..., [])`, not direct indexing (same "don't crash on
an older-shaped stale report" convention `benford_summary()` already
follows for `mad`/`mad_conformity`), and the stale file itself was
regenerated by simply re-running `rule_ledger_gstr_reconciliation.py` —
it still reconciles cleanly (0 discrepancies across all 12 months).

### 6.4 The "ledger replacement needs a human-chosen page range" claim was wrong

Section 5 above (written earlier the same day) stated that replacing a
ledger PDF needs a human to pick `start_page`/`end_page`, with no way to
auto-detect the right range — so it was left as a manual CLI step, and the
dashboard's upload caption said so explicitly. Re-examining this claim
empirically, instead of trusting the earlier reasoning, found it was
overly conservative: grepping this project's own history
(`BASELINE_CHECKPOINT_V3.9.md`, `V3.11.md`) confirmed that **every real,
committed reconstruction run against either real ledger has always
processed the whole document** — 926 pages for `ledgers_redacted.pdf`, 888
for `Ledgers_Anonymised.pdf` — never a partial range. The `start_page`/
`end_page` CLI arguments on `main()` exist for partial-range diagnostics
during development (e.g. `tests/test_header_carryover_fix.py`'s targeted
470-550 page checks), not because a full-document run ever needed a
human-chosen range.

Fixed: `reconstruct_ledger_entries.reconstruct_full_document(pdf_path)`
added — auto-detects the page range via `pdfplumber` (`1` to
`len(pdf.pages)`) and otherwise runs the identical pipeline `main()` does.
Verified byte-for-byte identical to the currently committed CSV for BOTH
real ledgers before relying on it anywhere (confirmed again via two new,
real, end-to-end tests —
`test_run_full_ledger_reconstruction_frozen_baseline_byte_identical` and
`..._fy2025_26_byte_identical` in `tests/test_dashboard_data.py`, ~45-50s
each, marked `@pytest.mark.slow`). `dashboard/data.py`'s new
`run_full_ledger_reconstruction(pdf_basename)` wraps it plus the matching
`adapt_ledger_to_schema.adapt_ledger()` normalization step, keyed by a new
`LEDGER_PDF_INFO` dict (the two real ledger PDFs this project knows how to
reconstruct, each with its own, genuinely different, already-investigated
unbalanced-voucher baseline: **2** for `ledgers_redacted.pdf`/FY2024-25,
**3** for `Ledgers_Anonymised.pdf`/FY2025-26 — confirmed directly, not
assumed identical).

The sidebar gained a new **"Ledger reconstruction"** section with one
button per PDF in `LEDGER_PDF_INFO`. The frozen FY2024-25 baseline's
button is gated behind an explicit acknowledgment checkbox ("I've
reviewed this and want to re-reconstruct the frozen baseline") — the
function itself is verified safe and byte-identical, so this gate is
about deliberate human sign-off before overwriting the committed baseline
file, consistent with this project's standing rule that the frozen
baseline changes only on explicit review, not about distrust of the
reconstruction itself. Both buttons report the fresh run's
unbalanced-voucher count against `LEDGER_PDF_INFO`'s known baseline and
warn (not fail) on a mismatch, the same "surface it, don't hide it"
convention Benford's Law and ML anomaly detection already use on this
dashboard.

### 6.5 A latent cross-ledger drill-down bug, caught before it shipped

Wiring the FY2025-26 tab's "Investigate" drill-down exposed a real hazard:
`dashboard/data.py`'s `_load_ledger_rows()` and `voucher_detail()` were
hardcoded to `GENERAL_LEDGER_PATH` (FY2024-25), with no way to point them
at `FY2025_26_GL_PATH`. Simply reusing them for the new tab would not have
failed loudly — it would have silently shown the **wrong voucher's data**,
because `entry_id`s are not unique across the two independent ledgers
(confirmed directly: e.g. `Payment_10` exists as a real, different
voucher in both). Fixed: both functions take an optional `ledger_path`
parameter (defaulting to `GENERAL_LEDGER_PATH`, so every existing call
site is unaffected), and `investigate_table()` in `dashboard/app.py` takes
a matching optional `ledger_path` it passes through, used only by the new
FY2025-26 tab's calls. Regression-tested directly
(`test_voucher_detail_entry_ids_are_not_unique_across_ledgers` in
`tests/test_dashboard_data.py`), not just fixed and trusted.

### 6.6 Verification

- Every new/changed function in `dashboard/data.py` smoke-tested directly
  against the real, committed data before being wired into `app.py`.
- Full test suite: **402 tests, 402 passing** (381 before this pass + 21
  new in `tests/test_dashboard_data.py`, 3 of them marked `@pytest.mark.
  slow` — real end-to-end reconstruction runs, ~45-50s each; `pytest.ini`
  gained a `markers =` section documenting that marker and how to skip it
  during iteration with `-m "not slow"`).
- Streamlit app launched headlessly and driven with a real headless
  browser (Playwright, not just an HTTP status check): confirmed all 7
  tabs render with no exceptions, confirmed the new FY2025-26 tab's full
  content (KPIs, priority findings, severity charts, ledger flags table,
  Benford cards, voucher-gap table, ML anomaly table) and the new Ledger
  vs GSTR-1 tab's content (both period lists, zero discrepancies) render
  correctly end to end.
- `run_full_ledger_reconstruction()` actually executed against both real
  raw PDFs (not just unit-tested) and diffed byte-for-byte against the
  currently committed `data/general_ledger.csv` and
  `data/general_ledger_FY2025-26.csv` — identical in both cases — before
  being wired into the sidebar.
- `data/general_ledger.csv` and `data/general_ledger_FY2025-26.csv`
  confirmed unchanged (same checksums before and after this entire pass)
  despite being overwritten-and-regenerated twice each during testing.

Files touched in this addendum: `scripts/reconstruct_ledger_entries.py`
(the stale "baseline 3 for both ledgers" docstring claim also corrected
to state each PDF's own real, different baseline), `dashboard/data.py`,
`dashboard/app.py`, `tests/test_dashboard_data.py`, `pytest.ini`,
`BASELINE_CHECKPOINT_V3.14.md` (this file).

## 7. Final state (superseding section 3 above)

- **9 detection rules**, now all with dashboard presence for both real
  financial years where applicable (Rules 1/2/3/5/7/8 for FY2024-25 AND
  FY2025-26; Rules 4/6 for FY2024-25 only, since no matching-FY document
  exists yet for FY2025-26) plus 3 standalone GST/ledger reconciliation
  checks (GSTR-1-vs-GSTR-3B, ledger-vs-GSTR-1, trial-balance-vs-ledger).
- **402 tests, 402 passing.**
- **7 dashboard tabs**: Overview, Ledger Flags, Tax & Compliance, Benford's
  Law, GSTR Status, FY2025-26 Ledger, Ledger vs GSTR-1.
- Every sidebar re-run button now does what its own caption claims,
  including full ledger reconstruction from the raw PDF (previously
  believed to need a manual CLI step; corrected in 6.4).
- Every remaining known limitation is still either fixed or formally
  closed as final scope with a stated reason (section 1.2) — nothing left
  as a vague, undated "known issue," and this addendum's one open item
  (6.2's segmentation-evidence breakdown being FY2024-25-only) is stated
  as such in the FY2025-26 tab itself, not hidden.

## 8. Addendum 3 (2026-10-04): generic upload-and-test dashboard (no hardcoded ledger filenames)

### 8.1 The problem

The FY2025-26 tab added in Addendum 2 was a real, fully-tested feature —
but it was wired into the dashboard as a hand-written tab tied to
`Ledgers_Anonymised.pdf` by filename (`LEDGER_PDF_INFO`'s dict keys). The
user caught this directly: *"why have u written it on dashboard itself
ledger 2025 2026... that shud work only if i upload the documents."* She
was right — "upload a document and it gets tested" is not the same thing
as "two filenames are hardcoded into the source, and happen to already be
present." Her explicit, final scope for the fix (quoted in full since it
drove every design decision below): *"run `is_ledger_document()`...to
check it's ledger-shaped before attempting reconstruction; if
reconstruction produces a high unresolved-line count or many unbalanced
vouchers on a new document, surface that prominently...rather than
quietly showing numbers with the same confidence as the fully-verified
ones"* — generalized, but honest about its own limits, and **never
crashes on any document**.

### 8.2 Content-based classification, not filename matching

New module `scripts/classify_document.py`: `classify_document(pdf_path)`
reads a PDF's actual text and returns one of `DOCTYPES` (`ledger`,
`trial_balance`, `form_26as`, `itr_computation`, `itr_acknowledgement`,
`gstr1`, `gstr3b`, `unknown`) plus human-readable `evidence`. Every marker
is a string directly verified against this project's own real documents
(see that module's docstring for exactly which document each one was
confirmed against), not a guess at a "typical" document's layout. Ledger
detection is delegated to a refactored, now-standalone
`check_is_ledger_document()` in `scripts/pdf_to_csv_converter.py` (pulled
out of `PDFToCSVConverter.is_ledger_document()`, whose class constructor
had an unwanted `os.makedirs()` side effect for what should be a pure
read-only check) — verified behavior-identical before relying on it, and
previously covered by **zero tests** despite existing in this codebase
already; both now have full coverage in `tests/test_classify_document.py`
(24 tests: every real document's classification pinned, plus garbage
bytes/empty file/missing path/valid-but-unrelated-PDF all verified to
return `"unknown"` gracefully, never raise).

### 8.3 Confidence scoring, calibrated against real measured baselines

`scripts/reconstruct_ledger_entries.py` gained `total_legs`,
`unresolved_count`, `date_range_start`, `date_range_end` on
`reconstruct_full_document()`'s return, and a new
`assess_reconstruction_confidence(recon_result)` returning
`{"confidence": "high"|"low", "reasons": [...]}`. Thresholds are the
*actual measured* numbers from this project's two real, fully-
investigated documents — not guessed round numbers:

| Signal | FY2024-25 (real) | FY2025-26 (real) | Threshold |
|---|---|---|---|
| Unresolved-line rate | 3.74% | 1.97% | >10% |
| Unbalanced-voucher rate | 0.032% | 0.050% | >1% |
| Minimum voucher count | 6,216 | 5,947 | <20 → too few to score at all |

Both real documents score `"high"` with zero reasons. The margins are
generous enough to absorb normal document-to-document variation within
the pattern this parser already handles, while still catching a document
that genuinely doesn't match it (a different accounting package's export,
a scanned/OCR'd ledger, an unfamiliar layout).

### 8.4 Dynamic discovery and the generic processing entry point

`dashboard/data.py` gained (additively — `LEDGER_PDF_INFO` is untouched in
shape, still backing `run_full_ledger_reconstruction()` and its pinned
tests exactly as before):

- `discover_documents()` / `discover_ledger_documents()` — classify every
  PDF in `data/raw_pdfs/` by content.
- `derive_period_label(start, end)` — turns a ledger's own transaction
  date range into a label ("FY2025-26" for an exact Indian financial
  year, else "DD Mon YYYY to DD Mon YYYY" — never a "/", always filename-
  safe), *without reading the filename*.
- `process_ledger_document(pdf_path)` — the generic entry point: classify
  → (if ledger) reconstruct → confidence-score → adapt → run the ledger-
  only rule report, under an auto-derived label → write a sidecar
  `output/ledger_meta__{slug}.json`. **Never raises** — every stage is
  individually wrapped, and any failure returns
  `{"ok": False, "stage": ..., "error": ...}` instead of propagating.
  Guards the frozen FY2024-25 baseline specifically (refuses to process
  it generically — that document keeps its own dedicated, checkbox-gated
  pipeline, unchanged).
- `ledger_tab_info(pdf_path)` — what the dashboard needs for one ledger's
  tab, gracefully degrading for the two already-established documents
  (which predate this system and have no sidecar file) by falling back to
  `LEDGER_PDF_INFO`'s own established label/paths.
- `document_library()` — the full per-document status list (classification,
  processed/not, confidence) backing the new sidebar section below.

### 8.5 Dashboard: dynamic tabs + Document Library

`dashboard/app.py`: the single hand-written "FY2025-26 Ledger" tab is
replaced by `render_generic_ledger_tab(info)`, called in a loop over
`discover_ledger_documents()` (excluding the frozen baseline). Tab names
and content are now fully data-driven — the existing FY2025-26 document
renders identically (same tab name, "FY2025-26 Ledger", same KPIs/tables,
confirmed byte-for-byte in a headless Playwright pass) purely because it
*is* a ledger PDF sitting in `data/raw_pdfs/`, not because its filename is
written into this file. A document scoring `"low"` confidence gets a
prominent `st.error` banner, with the specific measured reasons listed,
**before** any of its numbers are shown — verified live against a
deliberately sparse synthetic ledger (1 voucher), which correctly
triggered the "too few vouchers" and "100% unbalanced" reasons and
rendered the banner exactly as specified. Each dynamic tab's render is
also wrapped in `try/except` at the call site, so a document whose report
turns out to be malformed in some unanticipated way can't take the rest
of the dashboard down with it.

A new sidebar **Document Library** section lists every file in
`data/raw_pdfs/` with its classification and (for ledgers) a **Process
this document** button — verified live, end to end: a brand-new synthetic
ledger PDF dropped into `data/raw_pdfs/` appeared in the library
unprocessed, was processed with one click, immediately gained its own
dynamic tab (`"01 Apr 2025 to 01 Apr 2025 Ledger"`, auto-labeled from its
own data), and showed its low-confidence banner correctly — with zero
code changes required for that specific document. This is the literal
"upload a document, it gets tested" behavior requested.

### 8.6 Explicit, stated scope boundary (not generalized this round)

Rules 4 (ITR vs 26AS) and 6 (Trial Balance), and the Ledger-vs-GSTR-1 tab,
remain tied to their specific existing documents — matching an arbitrary
uploaded tax/trial-balance/GSTR document to an arbitrary uploaded ledger
by date-overlap is a separate, larger feature than what was asked for this
round, and is stated as such in both the dashboard's ledger-tab caption
and `README.md`.

**Honesty note, stated directly rather than implied:** this cannot
literally guarantee perfect results on every ledger export format in
existence — a different accounting package, a scanned/image-only PDF, or
a layout this parser has genuinely never seen could still reconstruct
poorly. What *is* now true, and verified rather than assumed: the
pipeline **never crashes** on any PDF (verified against garbage bytes, an
empty file, a missing path, and a valid-but-unrelated real PDF — all come
back as a clean, informative result, never an exception), and it **never
shows a questionable result with unearned confidence** — a document that
doesn't match the verified pattern is flagged prominently, with specific,
honest reasons, rather than silently trusted.

### 8.7 Verification

- `scripts/classify_document.py` / `check_is_ledger_document()`: 24 new
  tests (`tests/test_classify_document.py`) — every real document's
  classification pinned, plus 4 distinct synthetic failure modes
  confirmed to degrade gracefully, never raise.
- `dashboard/data.py`'s new functions: 23 new tests appended to
  `tests/test_dashboard_data.py` — `derive_period_label()`'s FY/non-FY/
  missing/malformed cases, `discover_documents()`/
  `discover_ledger_documents()` against real data and an empty directory,
  `ledger_tab_info()`'s graceful degradation for both known real
  documents, `assess_reconstruction_confidence()`'s thresholds (high,
  each low-confidence reason individually, and combined), and
  `process_ledger_document()`'s guard/rejection paths plus one full,
  real, end-to-end run against a novel synthetic ledger (marked
  `@pytest.mark.slow`; self-cleaning via `finally`).
- Full suite: **449 tests, 449 passing** (402 before this pass + 24 + 23
  new), zero regressions.
- Streamlit app launched headlessly and driven with a real headless
  browser (Playwright): confirmed both real documents' tabs render
  identically to before (same names, same KPI numbers, no confidence
  banner — both score "high"), confirmed the Document Library lists all
  11 real documents correctly, and confirmed the full upload → classify →
  process → dynamic-tab → low-confidence-banner flow end to end against a
  brand-new synthetic document, with no exception anywhere on the page.
- All synthetic/demo artifacts created during manual verification (test
  PDFs, their generated CSVs/JSON reports/sidecar metadata) removed after
  confirming the behavior — nothing left in `data/`/`output/` beyond what
  this phase's real, committed work requires.

Files touched: `scripts/pdf_to_csv_converter.py` (refactor only, behavior-
identical), `scripts/classify_document.py` (new),
`scripts/reconstruct_ledger_entries.py`, `dashboard/data.py`,
`dashboard/app.py`, `tests/test_classify_document.py` (new),
`tests/test_dashboard_data.py`, `BASELINE_CHECKPOINT_V3.14.md` (this
file), `README.md`.

## 9. Addendum 4: Document Library moved to a main tab (discoverability fix)

**Trigger — direct user feedback, immediately after Addendum 3 above was
reported as complete:** *"uploading is possible but there is no tab for
data to start processing."* This landed right after the generic upload
flow had been built, tested (447 passing tests) and pushed — a reminder
that passing tests confirm a feature *works*, not that a person can
*find* it.

### 9.1 Diagnosis (reproduced live, not guessed)

The underlying pipeline (`classify_document()` →
`process_ledger_document()`) was never the problem — it was re-verified
working in this round too (see 9.3). The actual fault was information
architecture: the **Document Library**, and the **"Process this
document"** button inside it, lived only in the sidebar, three levels
deep — Data section → "Document Library" header (or a separate "+ Upload
documents" expander) → a per-file `st.expander()` → the button itself.
Nothing in the main tab bar — the first place anyone looks after an
upload — pointed at it. A second, smaller bug was found at the same
time: the uploader's own caption claimed the Document Library was
"above" it, when it actually rendered *below*, in the same sidebar.

Reproduced faithfully with a real browser driving the actual
`st.file_uploader` widget (Playwright, `input[type="file"].set_input_files(...)`
— not the filesystem-shortcut method used for earlier verification
rounds) to get exactly the experience a real upload produces, confirming
the button existed, worked, and was simply buried.

### 9.2 Fix

- `render_document_library()` (`dashboard/app.py`): new function, moved
  the whole Document Library out of the sidebar and into a new first
  main tab, `"Document Library"`. Each document now renders as a single
  flat `st.container(border=True)` card (filename, content-classification,
  evidence, and — for ledger-shaped documents — a **Process this
  document** / **Re-process this document** button, directly visible, no
  expansion required) instead of a nested `st.expander()`.
- Tab assembly changed from one fixed list to
  `_head_tab_names + _base_tab_names + _dynamic_tab_names + _tail_tab_names`,
  with `_head_tab_names = ["Document Library"]` — so it is always the
  first tab, before Overview, regardless of how many dynamic ledger tabs
  exist.
- The old sidebar block (~68 lines: header + per-file nested expander +
  embedded button logic) was removed outright and replaced with a
  two-line pointer caption directing to the new tab.
- Fixed the uploader caption's "above" → "the **Document Library** tab
  (first in the tab bar)", correcting the pre-existing wrong-direction
  reference found during diagnosis.
- `dashboard/data.py` was **not** touched in this round — this was a UI
  placement fix only, not a logic change.

### 9.3 Verification

- `python3 -m py_compile dashboard/app.py dashboard/data.py` — clean.
- Streamlit relaunched headlessly; Playwright confirmed, in a fresh
  session: the tab bar shows "Document Library" first and active by
  default; its content renders the caption plus one flat card per
  document (11 real documents, verified via full-page screenshot and
  `inner_text()` dump); and — the specific regression test for this bug —
  a ledger-shaped PDF uploaded through the real `st.file_uploader` widget
  shows its **Process this document** button immediately, with
  `get_by_role("button", name="Process this document")` returning
  `count=1`, `is_visible()=True`, **with no scrolling or expansion
  needed**.
- Full fast suite re-run after the fix: `python3 -m pytest -m "not slow"`
  → **445 passed, 4 deselected** (the 4 slow, real end-to-end
  reconstruction tests unaffected, since no reconstruction logic changed).
  Combined with the unchanged slow tests, this stays at **449/449**,
  confirming zero regression from a UI-only edit.
- All test artifacts (`data/raw_pdfs/repro_upload_ledger.pdf`, created by
  driving the real uploader during reproduction) removed after
  confirming the fix — nothing left behind beyond this phase's real,
  committed work.

Files touched: `dashboard/app.py` only.

## 10. Addendum 5: tab simplified to results-first + a real zero-voucher crash fixed

**Trigger — direct user feedback, same session as Addendum 4 above, on a
live screenshot:** *"see no data coming only"* / *"is this document lib
necessary who wants to see the data set we have taken in the end results
matter so i think its unnecessary"* / *"even if it takes time to process
user shud now it is waiting and data is processing."* (The screenshot
itself turned out to be the pre-Addendum-4 sidebar build — the file
hadn't been swapped in on her end yet — but the product feedback stands
on its own regardless of which build she was looking at, and was acted
on directly.)

### 10.1 Simplification (render_document_library(), dashboard/app.py)

The tab previously showed every document as a full card with its
classification evidence spelled out, always, including documents already
fully handled. Rebuilt around one idea: only show what needs a decision.

- Tab renamed **"Document Library" → "Process Documents"** (an action,
  not a dataset to browse) — in the tab label itself and every sidebar
  pointer/caption referencing it.
- `document_library()`'s rows are now partitioned into `needs_action`
  (ledger-shaped, not known, not yet processed) vs. everything else.
  `needs_action` renders open, as full cards, exactly as before — that's
  the only thing anyone needs to act on. Everything else (processed
  ledgers, the two established baselines, non-ledger documents) collapses
  into a single closed `st.expander("N document(s) already on file")`,
  each as a one-line entry instead of a full card with its own
  exposition. A non-known, already-processed ledger keeps a small
  **Re-process** button inline on its one-liner.
- When there's nothing to act on, the tab now says exactly that —
  "Nothing new to process. Upload a PDF from the sidebar to add one." —
  instead of a wall of cards for documents that don't need attention.

### 10.2 Processing status made durable, not just present

The spinner (`st.spinner`) covering the ~45-60s reconstruction was
already there, but the success/error message that followed it was shown
with `st.success()`/`st.error()` in the same script run as an immediate
`st.rerun()` right after — easy to miss, since the rerun replaces it
almost immediately. Fixed with `_process_document_and_rerun()`:
  1. `st.status(..., expanded=True)` instead of a bare spinner — stays
     open with an explicit "please wait, this takes under a minute"
     message for the whole synchronous call, then flips itself to a
     checkmark or an error mark.
  2. The outcome is stashed in `st.session_state["_doclib_last_result"]`
     before the `st.rerun()`, not shown immediately before it.
     `render_document_library()` pops and displays it, once, at the top
     of the very next run — so it's still on screen after the rerun
     fires, not just flashed during it.

### 10.3 A real bug, found by stress-testing this myself, not reported by the user

Driving the actual upload flow end-to-end with a deliberately crude
synthetic PDF (to verify the two fixes above against a real "Process"
click, not just inspect the idle tab) surfaced a genuine gap in the
"never fail on any document" guarantee: a PDF whose column headers pass
`classify_document()`'s ledger check, but whose body never resolves into
a single complete voucher, reconstructs to **0 vouchers / 0 legs** — and
the adapted CSV ends up with 0 rows. `combined_report.rule_benfords_law()`
and `compute_mad()` both divide by the dataset's row count with no
zero-guard, so "processing" such a document raised a bare
`ZeroDivisionError: float division by zero` from deep inside the rule
engine — not the clean `{"ok": False, "stage", "error"}` this project's
whole `process_ledger_document()` design promises for every other
failure mode. Confirmed, not guessed: reproduced directly against
`reconstruct_full_document()`, which returns `total_vouchers: 0` for this
PDF, and `assess_reconstruction_confidence()` was *already* correctly
calling this "low" confidence with an explicit, honest reason ("Only 0
voucher(s) found... parser didn't recognize this document's layout at
all") — that existing signal just wasn't being acted on before the crash
point.

**Fix** (`dashboard/data.py`, `process_ledger_document()`): a new guard,
placed immediately after the confidence check and before `adapt`/
`report` are attempted at all — if `recon_result["total_vouchers"] == 0`,
return a clean `{"ok": False, "stage": "reconstruct", ...}` result
(reusing `assess_reconstruction_confidence()`'s own reasons in the error
text) instead of ever calling `adapt_ledger_to_schema`/
`run_ledger_only_report` against a dataset with nothing in it. This is
the correct choke point — not patching the division sites in
`combined_report.py` — because a 0-voucher adapted CSV has nothing
meaningful for ANY of Rules 1/2/3/5/7/8 to compute, not just Benford's.

### 10.4 Verification

- `python3 -m py_compile dashboard/app.py dashboard/data.py` — clean.
- New test: `test_process_ledger_document_zero_vouchers_fails_cleanly_not_a_crash`
  (`tests/test_dashboard_data.py`, marked `@pytest.mark.slow`) — builds a
  PDF with ledger-shaped headers but no resolvable vouchers, asserts
  `reconstruct_full_document()` really does return `total_vouchers == 0`
  (pins the premise, not just the fix), then asserts
  `process_ledger_document()` returns `ok=False`, `stage="reconstruct"`,
  a `"0 vouchers"` error message, and that no adapted CSV or report file
  is ever written.
- Full suite re-run after both the simplification and the fix:
  **450 tests, 450 passing** (449 + 1 new), full run including the slow
  tests this time (not just `-m "not slow"`), since both `app.py` and
  `data.py` changed this round.
- Live verification via Playwright, end to end, against the actual
  running Streamlit app (not just unit tests): uploaded a fresh
  synthetic ledger-shaped PDF through the real `st.file_uploader`
  widget, confirmed it appeared as the sole open card on the
  **Process Documents** tab with the other 11 documents collapsed behind
  "11 document(s) already on file", clicked **Process this document**,
  and confirmed the clean failure banner ("...failed at stage
  'reconstruct': Reconstruction found 0 vouchers...") rendered and
  persisted after the triggered rerun — the exact "user should know it
  is processing, and know the outcome" behavior requested, now
  demonstrated against a real failure, not just a real success.
- All test artifacts (the synthetic PDF and everything it produced:
  `data/general_ledger__verify_ledger.csv`,
  `data/verify_ledger_reconstructed_*.csv`,
  `data/raw_pdfs/verify_ledger.pdf`) removed after verification —
  `tests/test_classify_document.py::test_all_real_pdfs_are_covered`
  (which exists for exactly this purpose) caught one leftover file
  before this was confirmed clean, and the full suite was re-run green
  afterward.

Files touched: `dashboard/app.py`, `dashboard/data.py`,
`tests/test_dashboard_data.py`.

## 11. Addendum 6: the real page-load slowness, quantified and fixed

**Trigger — direct user feedback, same session, on a fresh screenshot
showing the tab bar simply not there yet:** *"fix the issue of backend
taking so much time"* / *"there is no thing for user to come to know
that the data is processing still when he has uploaded the document"* /
*"it is extremely inefficient to be slow."*

### 11.1 Measured, not guessed

Before changing anything: temporarily instrumented `dashboard/app.py`
with wall-clock timing prints around every major section (sidebar setup,
each tab's render block), launched it headlessly, and drove a real page
load through Playwright. Result, against this environment's 11 real
documents:

| Section | Time |
|---|---|
| Sidebar + dynamic-tab-name setup (before `st.tabs()`) | 5.91s |
| `Process Documents` tab render | 5.80s |
| `Overview` tab render | 1.30s |
| every other tab | ≤0.25s each |
| **Total** | **13.68s** |

The first two rows are almost the whole story (11.71s of 13.68s, ~86%).
Both are `dd.discover_documents()` — classifying every PDF in
`data/raw_pdfs/` via `scripts/classify_document.py`, which has to open
and read into this project's two ~960-page ledger PDFs to check their
headers. The setup section calls it indirectly through
`dd.discover_ledger_documents()` (for the dynamic ledger-tab list); the
`Process Documents` tab calls it indirectly through
`dd.document_library()`. **Neither call site knew the other had already
scanned the exact same directory moments earlier in the same script
run** — so every single page load paid for that ~6s scan twice, and
Streamlit reruns the *entire* script (every tab's content, not just the
one open) on every upload, button click, or widget interaction, so this
wasn't a one-time cost — it repeated on every interaction.

### 11.2 Fix: one scan, cached, shared

- `dd.discover_ledger_documents()` and `dd.document_library()`
  (`dashboard/data.py`) both gained an optional `documents` parameter:
  pass an already-computed `discover_documents()` result to reuse it
  instead of re-scanning. Defaults to `None` (re-scans — the original
  behavior), so every existing caller, including every test that calls
  either with no arguments, is completely unaffected.
- `dashboard/app.py` gained `cached_discover_documents()`
  (`@st.cache_data`, following the exact same TTL-free pattern already
  used by `cached_segmentation_summary()`/`cached_trial_balance_summary()`
  for the same reason), called **once** per script run; both the
  dynamic-tab-name loop and `render_document_library()` now take that
  single result as an argument instead of each re-deriving it.
- Cache correctness: a cached scan is only safe if it's invalidated the
  moment the real answer could have changed. `st.cache_data.clear()` —
  already called after processing a document or re-running a pipeline —
  is now also called right after a successful upload save
  (`dd.save_uploaded_file()`), which previously had no cache to clear at
  all. Without this, a freshly uploaded ledger would have silently not
  appeared on `Process Documents` until some *unrelated* button happened
  to clear the cache — trading "slow" for "wrong," which would have been
  worse.
- `show_spinner="Scanning documents in data/raw_pdfs/..."` on
  `cached_discover_documents()` directly answers the "no sign it's
  processing" complaint for the one case that's still visibly slow after
  this fix — the first load of a session, before the cache is warm.

### 11.3 Verification

- Re-ran the same timing instrumentation against the fixed code:
  - First load (cold cache): **6.77s** total through the Overview tab
    (down from 13.68s) — `Process Documents`' own render dropped from
    5.80s to **0.00s**, confirming the duplicate scan is gone; the
    remaining 5.65s is the one real scan, now paid once instead of
    twice.
  - A second rerun in the same session (triggered by a sidebar
    interaction, warm cache): **0.69s** total — a ~20x improvement over
    the original 13.68s baseline.
- Correctness check for the new cache: uploaded a fresh synthetic
  ledger-shaped PDF through the real `st.file_uploader` widget against
  the fixed build and confirmed it appeared on `Process Documents`
  immediately (the upload's own `st.cache_data.clear()` forcing a fresh
  scan, ~8.8s for that one interaction, back to sub-second on the next),
  not hidden behind a stale cached document list.
- `python3 -m py_compile dashboard/app.py dashboard/data.py` — clean.
- Full suite re-run: **450 tests, 450 passing** — the new `documents=None`
  parameters don't change either function's behavior for any existing
  caller, so this was expected to be a zero-regression change, and the
  full run confirms it.
- All instrumentation was applied to, and removed from, working copies
  only — the timing `print()` statements never shipped; `app.py`'s only
  permanent change here is the caching itself.

Files touched: `dashboard/app.py`, `dashboard/data.py`.

## 12. Addendum 7: upload moved into the sequence itself, plus a rerun-ordering bug caught before shipping

**Trigger — direct user feedback, same session, on the just-fixed-but-not-yet-restarted build:**
1. *"before even i have uploaded the document the circle was running"*
2. *"what is data raw pdf. if today the model is used by a third party
   what he wants as a user, he shud upload document click on a process
   tab below upload that works only if the documents are uploaded and
   then the scanning documents thing what u have added shud come, so it
   shud follow a sequence"*
3. A repeated, explicit instruction that the AI assistant's name and
   its maker's name must not appear anywhere in this project's work —
   code, docs, or git commit messages — and that any existing mention
   be removed.

### 12.1 Points 1–2: the workflow wasn't actually a sequence

Two real issues, both about information architecture rather than
correctness:
- The `cached_discover_documents()` spinner (Addendum 6) was worded
  "Scanning documents in `data/raw_pdfs/`..." — it ran on the very
  first page load, before any upload, and named an internal folder path
  the person never typed or saw anywhere else in the UI. From a fresh
  user's side, that reads as the dashboard doing something unprompted,
  for no visible reason.
- Upload (sidebar, under "+ Upload documents") and Process (the
  **Process Documents** main tab, since Addendum 4) lived in two
  separate places with no visual connection between them — a real gap
  for *"if today the model is used by a third party, what does he
  want"*: a stranger landing on this dashboard has no way to know those
  two controls belong to the same action.

**Fix:**
- `show_spinner` reworded to the generic **"Loading dashboard..."** —
  no internal path name, and phrased as ordinary app startup (expected
  once, not implying an upload triggered it).
- The uploader itself moved out of the sidebar entirely into a new
  `render_upload_section()`, called first thing inside
  `render_document_library()` — so the **Process Documents** tab now
  reads top to bottom exactly as the real sequence: upload a file → see
  it below as a ready-to-process card → click **Process this
  document** → see the outcome. The sidebar keeps only a one-line
  pointer ("To upload or process a document, go to **Process
  Documents**"), mirroring the same pattern already used for the
  Process button's own move out of the sidebar in Addendum 4.
- Grepped every `st.caption`/`st.markdown`/`st.success`/`st.error`/
  `st.warning`/`st.write`/`st.title`/`st.header` call in `app.py`
  programmatically for `data/raw_pdfs` — the one remaining user-facing
  mention (the old uploader caption) reworded to drop the path; every
  other occurrence is inside a docstring or code comment (developer-
  facing only, never rendered).

### 12.2 A real bug, caught testing this exact move before it reached the user

Moving the uploader into `render_document_library()` introduced a
genuine ordering bug: `_all_documents = cached_discover_documents()`
runs earlier in the script (its result is needed to build the dynamic
ledger-tab names before `st.tabs()` is even called) — strictly before
`with tab_library: render_document_library(_all_documents)` executes.
So when the moved-in upload handler called `st.cache_data.clear()`
*after* that point, it cleared the cache for the *next* run, but this
run's `all_documents` variable was already captured from the stale,
pre-upload cache. The newly uploaded file would have silently not
appeared as a "ready to process" card until some unrelated action
happened to trigger another rerun — reproduced directly: uploaded a
synthetic ledger through the real `st.file_uploader`, and the tab still
showed "Nothing new to process" / "11 document(s) already on file"
immediately after a confirmed "Saved" message, for exactly this reason.
(This also explains why the *old* sidebar-uploader version worked: the
sidebar's code ran *before* the dynamic-tab setup section, so its
`clear()` landed before that run's one scan, not after — the ordering
was accidentally correct there, not by design.)

**Fix, not a workaround:** `render_upload_section()` now collects
per-file save results, clears the cache once if anything was actually
saved, and calls `st.rerun()` — so the very next script run recomputes
`_all_documents` against the cleared cache and genuinely includes the
new file. Any success/warning/error message is stashed in
`st.session_state["_upload_last_results"]` and displayed at the top of
the function on that next run instead of immediately before the rerun
(same durable-message pattern `_process_document_and_rerun()` already
uses, for the same reason: a message shown right before an immediate
rerun is replaced before anyone can read it).

Re-verified live after the fix: uploaded a fresh synthetic ledger PDF
through the real widget, waited for the rerun, and confirmed the
persisted "Saved: ... — ready to process below." banner at the top,
followed immediately by the new file's card with its **Process this
document** button — all in the Process Documents tab, nothing in the
sidebar.

### 12.3 Point 3: no AI-assistant or maker mentions

Audited every file this project's own work has touched — every `.py`
and `.md` file, programmatically, for the assistant's name or its
maker's name, case-insensitively: **zero matches** in any actual
project file (code, docs, docstrings, comments, UI text) at the time.
(Superseded: Addendum 15 found this audit had missed `scripts/` and the
git history; see section 26.)

### 12.4 Verification

- `python3 -m py_compile dashboard/app.py dashboard/data.py` — clean.
- Full suite: **450 tests, 450 passing** (unchanged — this round's
  changes are presentation/ordering, not logic `dashboard/data.py`
  wasn't touched at all this round).
- Live, end to end, against the real running app: confirmed the
  **Process Documents** tab now contains the complete upload → process
  sequence with nothing left in the sidebar but a pointer; confirmed
  the rerun-ordering bug's fix with a real upload through the actual
  widget; confirmed (programmatic grep, not eyeballing) no remaining
  user-facing mention of the internal `data/raw_pdfs/` path, or of the
  AI assistant's or its maker's name, anywhere in rendered UI text.
- All test artifacts (two synthetic PDFs used to reproduce and then
  re-verify the rerun bug) removed after verification.

Files touched: `dashboard/app.py`.

## 13. Final state (superseding section 7 and Addendum 3's closing summary above)

- **450 tests, 450 passing.**
- Dashboard ledger tabs are **fully dynamic**: generated from whatever
  ledger-shaped PDFs are actually in `data/raw_pdfs/` (content-classified,
  not filename-matched), each carrying an honest confidence verdict. The
  two real documents still render exactly as before; any newly uploaded
  ledger-shaped PDF gets an identical tab the moment it's processed, with
  zero code changes.
- The **Process Documents** tab (renamed from "Document Library") is a
  first-class main tab — first in the tab bar, before Overview — and is
  now the complete, self-contained upload → process sequence: upload a
  PDF there, see it appear as a ready-to-process card the moment the
  rerun completes, click **Process this document**, see a live status
  and then a durable outcome banner. Everything already handled
  (processed ledgers, the two established baselines, non-ledger
  documents) collapses into one closed "N document(s) already on file"
  expander. The sidebar keeps only a one-line pointer to this tab.
- A genuine zero-voucher crash (a ledger-shaped PDF that reconstructs to
  nothing) is now a clean, honest failure message instead of a raw
  `ZeroDivisionError` — found and fixed via this round's own stress
  testing, not a user report.
- Document discovery (`classify_document()` across every PDF in
  `data/raw_pdfs/`) runs **once** per page load instead of twice, and is
  cached across reruns within a session — page load dropped from ~13.7s
  to ~6.8s cold / ~0.7s warm, measured, not estimated.
- No internal file paths or AI-attribution text appear anywhere in the
  dashboard's user-facing UI — verified by a programmatic audit, not
  assumed.
- Scope boundary unchanged from Addendum 2 and stated explicitly in both
  the dashboard and `README.md`: Rules 4/6 and Ledger-vs-GSTR-1 remain
  tied to their specific existing documents.

## 14. Addendum 8: full UI/UX overhaul -- grouped navigation, a real upload area, a single status vocabulary

**Trigger -- a detailed, numbered UI/UX spec, same day, after seeing the
Addendum 7 build live:**
1. The one-time document-scan spinner needed to visibly react to an
   upload, not just repeat the exact same first-load message in the
   exact same place.
2. The upload control read as a generic file-uploader widget, not a
   deliberate first step in a sequence.
3. The flat 8-tab top bar ("needs grouping... to feel like a proper
   application rather than a collection of Streamlit tabs") into 4
   named groups: Process / Audit / Tax & Compliance / Documents.
4. Several tab names read as internal project vocabulary rather than a
   finished product's ("Flags" in particular, "sounds like a debugging
   tool").
5. A single document's processing outcome, and the set of all
   documents, should both read against one clear status vocabulary
   (Uploaded / Ready to Process / Processing / Processed / Failed)
   instead of ad hoc phrasing in different places.
6. The sidebar's Data panel should be compact (count + processed count
   + last-processed timestamp) with the full file list moved behind an
   "View documents" expander, not always-visible.

Explicitly scoped as **UI/UX only** -- no backend pipeline, processing
logic, document classification, or data structure was to change, and
none did; every edit this addendum describes is in `dashboard/app.py`
alone.

### 14.1 One open architecture question, asked before building

Streamlit's `st.tabs()` doesn't support a flat row with floating section
headers above it -- grouping 8 tabs into 4 named groups needs an actual
decision about how the grouping works, not a styling tweak. Two honest
options existed: nested `st.tabs()` inside each group tab (same pill
look throughout, zero new dependencies, two clicks to a deep item), or a
sidebar-based grouped nav (one click per item, but the top tab bar
disappears and the sidebar gets materially bigger). Asked directly
rather than guessed; nested tabs was the choice -- confirmed working
against the installed Streamlit version (1.64) before committing to it,
not assumed from general Streamlit knowledge.

### 14.2 What changed

- **Navigation**: `st.tabs(["Process", "Audit", "Tax & Compliance",
  "Documents"])` at the top level; `Audit` and `Tax & Compliance` each
  open a second, nested `st.tabs()` row for their items (Overview /
  Ledger Findings / Benford's Law; Tax Reconciliation / GST Compliance /
  Ledger vs GSTR-1 Reconciliation); `Documents` nests the dynamic
  per-ledger "... Ledger Analysis" tabs (renamed from "... Ledger",
  keeping the document's own label as the prefix so multiple processed
  ledgers still get distinct names); `Process` has exactly one item, so
  it's the group tab directly -- a nested single-item row would be
  clutter. `cached_discover_documents()`'s one document scan is computed
  once, before the sidebar, and shared by the sidebar summary, the
  Process Documents tab, and the dynamic tab-name loop -- preserving the
  single-scan invariant Addendum 6 established, now with one more
  consumer.
- **Two distinct spinners for the one scan, not one fixed message**:
  `cached_discover_documents()`'s decorator is now `show_spinner=False`
  -- the call site decides. A genuine first load (nothing uploaded this
  session) shows plain "Loading dashboard..." exactly where it's always
  been. The rerun immediately following an upload (flagged via
  `st.session_state["_scanning_after_upload"]`, set by
  `render_upload_section()` right before its own `st.rerun()`) shows a
  centered "Scanning your documents..." instead -- centered via a
  3-column `st.columns([1,2,1])` layout, not a CSS/HTML hack. Verified
  live: caught the centered spinner mid-upload with Playwright, screenshot
  confirms it horizontally centered with distinct wording from the
  first-load case.
- **Upload area**: `render_upload_section()` now opens with an "Add
  source documents" heading and a one-line description of what happens
  on drop, before the file_uploader widget itself (Streamlit's own
  "Drag and drop... / Browse files / Limit NNNMB per file" chrome inside
  the widget isn't independently restyleable without a fragile internal-
  text-replacement hack, so left as Streamlit's own, with the custom
  heading/caption around it doing the explanatory work instead). The
  pending-upload card now reads "N new document(s) detected:" with a
  colored "● Ready to Process" status badge and a "Process Document"
  button (renamed from "Process this document"), matching the requested
  upload -> detected -> process reading.
- **Status vocabulary + table**: every document now reads against
  Uploaded / Ready to Process / Processed / Failed consistently. The
  "already on file" section is now a real table (`st.dataframe`:
  Document / Type / Status / Last processed) instead of a bulleted list,
  covering every document including non-ledger ones (a GSTR/trial-
  balance/tax-statement document reads "Uploaded" with a caption
  explaining it's handled by its own sidebar pipeline button, not this
  table's Process flow; an unclassifiable PDF reads "Unrecognized"). A
  successful processing outcome now shows a retrospective, stage-by-
  stage summary (Classification / Reconstruction -- voucher and
  unbalanced counts / Validation -- confidence / Audit analysis) instead
  of one pass/fail line.
  - **Deliberately NOT a live per-stage progress tracker**: a true live
    checklist (each stage checking itself off as it happens) would need
    `process_ledger_document()` to report interim progress, which this
    round's own scope rule ("keep all processing logic... unchanged")
    rules out. What's shown is a retrospective summary built entirely
    from that function's own already-returned numbers, rendered after
    the single synchronous call completes -- real data, not a fabricated
    animation of progress that didn't actually happen incrementally.
- **Sidebar Data panel**: now two `st.metric`s (Documents, Processed)
  plus a last-processed caption, with the full per-document list moved
  into a closed-by-default "View documents" expander (a compact
  filename + status table) instead of always-visible. The sidebar's
  resizable width itself is a Streamlit/browser-level affordance, not
  something a single section's CSS can narrow without also narrowing
  every other sidebar control (the pipeline re-run buttons, the
  reconstruction section) -- addressed by reducing the panel's own
  content and height instead, not by forcing a fixed pixel width.

### 14.3 Verification

- `python3 -m py_compile dashboard/app.py dashboard/data.py` -- clean.
- Full suite: **450 tests, 450 passing** -- `dashboard/data.py` untouched
  this round, so this confirms zero regression from a presentation-only
  change, not just that nothing crashed.
- Live, end to end, against the real running app (Playwright driving the
  actual widgets, not a visual inspection of markup):
  - Confirmed the first-load spinner's plain text/position, then caught
    the centered "Scanning your documents..." spinner mid-flight on a
    real upload and confirmed it's gone once that rerun settles.
  - Confirmed the "1 new document detected" card, its "Ready to Process"
    badge, and the **Process Document** button, then clicked it and
    confirmed the live "please wait" status followed by the durable
    "Status: PROCESSED" banner and its stage-by-stage summary (caught a
    singular/plural wording bug here -- "1 vouchers" -- fixed before
    this was reported done).
  - Confirmed all 4 nav groups render via screenshots: Process (upload +
    status table), Audit (its 3 nested tabs, with real KPI/findings data
    still rendering correctly), Tax & Compliance (its 3 nested tabs),
    Documents (the dynamic "... Ledger Analysis" tab, with its full
    content rendering correctly).
  - Confirmed the "N document(s) already on file" table shows real
    per-document status/type/last-processed data for all 11 real
    documents, including the non-ledger ones.
- Grepped the whole file again for the AI assistant's or its maker's
  name after adding this round's substantial new text -- still zero
  matches (see Addendum 7 section 12.3 for why this is checked every
  round, not assumed to still hold).
- All test artifacts (one synthetic single-voucher PDF and its derived
  output files, used to drive the live upload/process verification
  above) removed after verification, confirmed via a repeat filesystem
  search and by `data/raw_pdfs/` being back to exactly its real 11
  files.

Files touched: `dashboard/app.py`.

## 16. Addendum 9: lazy-loading performance restructure, a live multi-stage processing UI, and the replace-upload fix

**Trigger -- a four-point "UI + performance fixes" request, same day,
after seeing Addendum 8 live:**
1. Initial dashboard load (~20-25s reported) needed to get to ~2-3s for
   "initial UI rendering." Explicit instructions: profile first, don't
   optimize blindly; the initial render must not run expensive PDF
   parsing/reconstruction/reconciliation/Benford/GST/report-generation
   work unless actually required for that render; use lazy loading,
   caching, and session/state management; never weaken any existing
   audit logic to get there.
2. Replace the small "Loading dashboard..." spinner near the nav (shown
   after an upload) with a clearly centered, full-main-content-area
   processing state showing real named stages (Document uploaded /
   Scanning document / Extracting data / Running audit checks /
   Preparing results), the active one spinning, no fake percentage.
3. Fix the confusing "Replaced existing file: ..." -> "Nothing new to
   process" sequence for a same-named replacement upload -- make the
   state explicit (uploaded/replaced -> scanning -> processing ->
   complete) and always offer a **Process Document** action instead of
   silently implying there's nothing to do.
4. Do not modify the audit algorithms, document classification, ledger
   reconstruction, reconciliation, GST logic, or findings logic as part
   of this UI/performance task.

### 16.1 Profiling first -- what was actually slow

Real `time.time()` instrumentation (a temporary swapped-in copy of
`dashboard/app.py`, restored afterward -- see 16.4) against a headless
Streamlit server driven by Playwright, not a guess. Server-side script
execution measured **~7.7s total**, of which **~6.3s (83%) was one
single step**: `cached_discover_documents()`'s one-time scan of every
PDF in `data/raw_pdfs/` through `classify_document()`. Every other tab's
content (Overview, Ledger Findings, Tax Reconciliation, Benford's Law,
GST Compliance, Ledger vs GSTR-1 Reconciliation) combined cost only
**~1.3s**, because none of it touches `data/raw_pdfs/` at all -- it only
reads already-computed `output/*.json`.

`cProfile` against the scan confirmed the ~6.3s is genuine PDF
text-extraction work (pdfminer's own pure-Python content-stream parser
inside `classify_document()`), not a redundant inefficiency -- and a
direct threaded-vs-serial timing comparison confirmed threading does
**not** help (CPU-bound, GIL-bound pure Python), ruling out a
parallelization fix. Per-file cost (~0.15-1.1s) is roughly constant
regardless of page count, confirmed by reading `scripts/
pdf_to_csv_converter.py`'s `check_is_ledger_document()`: it only ever
samples up to 5 pages (`min(5, len(pdf.pages))`), which is why the
project's two ~960-page ledger PDFs were among the *fastest* files to
classify, not the slowest. `classify_document()` itself was read, never
edited -- point 4's constraint.

A second, compounding cost was found in the same pass: every upload
called `st.cache_data.clear()`, which can only invalidate *all*
`@st.cache_data` caches at once (there is no "clear just this one
function" call) -- so uploading a single new PDF forced a full
re-classification of every file already on disk, not just the new one,
every time.

### 16.2 What changed

**`dashboard/app.py` -- lazy-loading script reorder.** Exploits a
property already relied on elsewhere in this file: a Streamlit tab
object returned by `st.tabs()` can be written into via `with tab_x:`
anywhere later in script order, independent of where it was created, and
`st.sidebar`'s visual position is likewise independent of when in script
execution its content runs. The script was reordered so that the title,
the 4 top-level nav tabs, the upload widget, and the **entire** Audit
group (Overview / Ledger Findings / Benford's Law) and Tax & Compliance
group (Tax Reconciliation / GST Compliance / Ledger vs GSTR-1
Reconciliation) content -- 6 of this dashboard's 8 content areas -- are
all queued and streamed to the browser *before* the one-time document
scan is ever reached. Only the Process tab's status table/cards and the
Documents group's dynamic per-ledger tabs genuinely need that scan's
result, so only those wait on it now, with their own spinner exactly
where that wait is real -- nothing about the scan's own cost was
reduced (point 4 forbids that); only *when* it blocks rendering changed.
`render_document_library()` was split in two: a `render_upload_section()`
call near the top (needs nothing but a plain `os.listdir()`) and the
rest (the status table and "ready to process" cards, which need the
scan's result) called again right after the scan completes -- the
upload -> process sequence still reads top-to-bottom on screen exactly
as before.

One real side effect of moving the Audit/Tax & Compliance content ahead
of the sidebar: three of the sidebar's "Re-run ... pipeline" buttons
(ledger + tax, GSTR, Ledger vs GSTR-1) regenerate report JSON that those
now-earlier tabs already read earlier in the *same* script run. Without
a fix, a click would show its success message but the regenerated
numbers wouldn't appear until some *unrelated* later rerun -- a real
regression from the pre-restructure behavior, caught and fixed before
calling this done: those three buttons now stash their message and call
`st.rerun()` immediately, so the very next run (which starts over from
the top, re-reading the now-fresh files) shows both the message and the
updated numbers together, with no extra click needed. Verified live
(16.3). The 4th button ("Re-run FY2025-26 ledger pipeline") didn't need
this -- its dependent content (the dynamic ledger tab) already renders
*after* the sidebar in the new order.

**`dashboard/app.py` -- `discover_documents_fast()` replaces
`cached_discover_documents()`.** Fixes the compounding re-scan cost
without touching `classify_document()` or `dd.discover_documents()` at
all: its own per-file cache lives in `st.session_state`, fingerprinted
by `(mtime_ns, size)` rather than one all-or-nothing flag. A file whose
fingerprint hasn't changed is reused as-is; anything new, changed (a
same-named replacement -- a different fingerprint is exactly how that
shows up), or evicted is reclassified individually, one real
`classify_document()` call per changed file, not the whole directory.
A file removed from `data/raw_pdfs/` is dropped from the cache so it
can't accumulate. This is a UI-layer orchestration change (which files
get handed to `classify_document()` and when), not a change to
classification itself -- the function is called exactly as `dd.
discover_documents()` already calls it, same input, same output shape.

**`dashboard/data.py` -- `process_ledger_document()` gained an optional
`on_stage=None` callback**, invoked right before each of its four real
internal steps (`"scanning"` / classify, `"extracting"` / reconstruct,
`"auditing"` / confidence+adapt+report, `"preparing"` / sidecar write).
Purely additive: every existing caller (including every test in `tests/
test_dashboard_data.py`) omits it and behavior is byte-for-byte
unchanged; the callback's own exceptions are swallowed so it can never
break processing, matching the function's existing "NEVER RAISES"
guarantee. No audit/classification/reconstruction/reconciliation/GST
logic inside was touched -- every call into those modules is exactly as
it was; `_emit_stage()` calls were inserted around them, nothing inside
them changed. This was the one explicit implementation choice put to a
direct question rather than guessed (a live, accurate multi-stage
display needs *some* backend signal; the alternative was a coarser,
still-retrospective summary with no backend change at all) -- the
optional-callback approach was chosen.

**`dashboard/app.py` -- the new centered, multi-stage processing card.**
Replaces the old `st.status(..., expanded=True)` box entirely.
Clicking **Process Document** / **Re-process Document** now stashes
`{"path", "filename"}` into `st.session_state["_active_processing"]`
and reruns, rather than processing inline inside its own small
per-document card; `_render_active_processing_if_any()`, checked first
inside the Process tab (ahead of even the upload widget), detects that
flag and -- for that one run -- renders *only* this card, full-width,
centered both vertically and horizontally via a single `.proc-card-wrap`
flexbox `st.markdown(..., unsafe_allow_html=True)` call (deliberately
one HTML string, not several separate calls: a multi-call open/close
`<div>` sequence doesn't nest reliably in Streamlit, since each call is
its own independent DOM insertion). Five real stages are shown
(`_PROCESSING_STAGES`), each tied to an actual boundary inside
`process_ledger_document()` via the new `on_stage` callback -- completed
stages get a green check, the active one a small CSS `@keyframes`-
animated spinner (`.mini-spinner`), the rest a hollow circle. No fake
percentage anywhere. The card redraws into the same `st.empty()`
placeholder on every real callback firing -- visibly live within the one
script run, no extra per-stage rerun. The nav bar stays fully
responsive throughout (it was queued long before this point in the
script), so clicking another tab while a document is processing works
normally; this is the one case the lazy-loading restructure was never
meant to speed up -- the person explicitly asked for this specific
document to be processed and is watching it happen, exactly as the old
blocking box already did.

**`dashboard/app.py` -- the replace-upload fix.** A same-named
replacement upload is now recorded into
`st.session_state["_awaiting_reprocess"]` (a set of filenames) by
`render_upload_section()`; `render_document_library()`'s `needs_action`
filter now includes a filename in that set even when `is_processed` is
already true, so a replaced-but-previously-processed document correctly
reappears as ready-to-act instead of silently falling into "already on
file / Processed" with its old, now-stale result and no obvious next
step. Its card shows a distinct "● Replaced -- Ready to Re-process"
badge and a "Re-process Document" button. The upload confirmation itself
now reads "✓ Document uploaded: ..." / "✓ Document replaced: ... --
ready to re-process below." instead of the old ambiguous "Replaced
existing file: ..." warning. The filename is cleared back out of the
set once `_render_active_processing_if_any()` actually (re)processes it.

### 16.3 Verification

- `python3 -m py_compile dashboard/app.py dashboard/data.py` -- clean.
- Full suite: **450 tests, 450 passing** -- including after the `on_stage`
  callback addition to `dashboard/data.py`, the one backend file touched
  this round; every existing test calls `process_ledger_document()` with
  its original single positional argument, confirming the addition is
  genuinely behavior-preserving, not just "still compiles."
- Live, end to end, against the real running app (Playwright driving a
  headless Chromium against a live headless Streamlit server, with real
  `time.time()` server-side instrumentation to cross-check what the
  browser saw -- not a visual read alone):
  - **Cold load, instrumented timeline**: nav bar ~1.0-1.3s; Overview
    (Audit group) content visible ~1.8-1.9s; Tax & Compliance content
    visible ~3.0-3.5s (both groups' content fully queued server-side by
    ~1.3s, matching the profiled estimate almost exactly -- the gap to
    the browser-observed numbers is normal click/render round-trip, not
    extra server work); Process tab's scan-dependent content fully
    settled ~7.2-8.3s total. Down from the ~20-25s originally reported,
    and the nav/Audit/Tax & Compliance portion comfortably inside the
    requested ~2-3s "initial UI rendering" target -- the remaining ~6-7s
    for the Process/Documents tabs is the scan's own genuine,
    unavoidable classification cost (confirmed in 16.1, not reducible
    without touching classification logic point 4 forbids touching).
  - **Incremental re-scan on upload**: after the initial cold scan
    settled, uploading one new file showed it as ready-to-process in
    **~0.77s** (one file reclassified), not a repeat of the full ~6s
    directory scan.
  - **Multi-stage processing card**: clicked **Process Document** on a
    freshly uploaded synthetic ledger PDF and captured the card
    mid-flight -- observed real, progressing stage transitions ("✓
    Document uploaded, ✓ Scanning document, ✓ Extracting data, [active]
    Running audit checks, ○ Preparing results" at t=0.77s, advancing to
    "... ✓ Running audit checks, [active] Preparing results" at t=1.28s),
    then the durable "Status: PROCESSED" outcome banner with correct
    voucher/confidence numbers at t=2.68s. Confirmed centered layout via
    screenshot.
  - **Replace-upload flow**: re-uploaded the same filename within one
    session; confirmed the "✓ Document replaced: ... -- ready to
    re-process below." message, the "1 document ready to process:"
    caption, the "● Replaced -- Ready to Re-process" badge, and the
    "Re-process Document" button, via screenshot -- replacing the old
    "Nothing new to process" dead end.
  - **Sidebar pipeline-rerun immediate-refresh fix**: clicked "Re-run
    ledger + tax pipeline," confirmed the success message, then
    re-checked the Overview tab's "generated at" caption on the *same*
    settled page with no extra manual interaction -- it had already
    advanced to the new regeneration timestamp, matching pre-restructure
    behavior exactly (findings/counts themselves were confirmed
    unchanged -- same source ledger data, same 14,625 legs / 1,271
    flagged / per-severity counts -- only the timestamp moved, as
    expected from a deterministic re-run).
  - Confirmed the "document(s) already on file" status table still
    correctly shows a freshly processed document's real status/type/
    last-processed values.
- An early false alarm during this verification is worth recording:
  server output appeared to "hang" for 20+ seconds with no further log
  line after the scan started. Root cause was `print()` output buffering
  (no `flush=True`) when stdout is redirected to a file, combined with
  a sloppy first-pass text-matching assertion (checking for the
  substring `"ready to process"`, which also matched unrelated caption
  text — "shows up as a card underneath ready to process" — producing a
  false-positive "already settled" reading seconds too early). Neither
  was a real defect in the app; both were artifacts of the verification
  scripts themselves, caught and fixed by re-testing with `flush=True`
  logging and exact, unambiguous match strings before trusting the
  numbers above.
- Grepped `dashboard/app.py` and `dashboard/data.py` again for the AI
  assistant's or its maker's name after this round's edits -- zero
  matches (checked every round; see Addendum 7 section 12.3 for why).
- All test artifacts from live verification (one synthetic ledger PDF,
  re-uploaded twice to exercise both the fresh-upload and replace-upload
  paths, plus its derived CSVs/JSON/sidecar-metadata files) removed
  afterward, confirmed via a repeat filesystem search and
  `data/raw_pdfs/` being back to exactly its real 11 files.

### 16.4 Methodology note: profiling and debugging without touching the shipped file

Both the initial profiling pass (16.1) and the later debug pass (the
false-alarm hang in 16.3) instrumented a copy of `dashboard/app.py`
in place, ran it, captured the real timing/debug output, then restored
the file from a pre-instrumentation backup -- verified via `diff -q`
against the backup, `grep -c` for the instrumentation markers (0
matches), and `python3 -m py_compile` (clean) every time, rather than
trusting that a cleanup step ran. One concrete pitfall hit and fixed
during this: a chained `pkill -f "streamlit run"; ...` bash command can
return a nonzero exit code from `pkill` itself and abort the rest of the
chain silently (including a restore step placed after it in the same
invocation) -- fixed by always running a restore as its own standalone
command, never chained after a `pkill`.

Files touched: `dashboard/app.py`, `dashboard/data.py`.

## 18. Addendum 10: generalized product architecture -- removed dataset-specific sidebar pipelines, fixed the scikit-learn dependency gap

**Trigger -- an architecture-correction request, same day, after seeing
the UI live:** the sidebar's "Pipeline" section exposed four buttons each
named after one specific development document ("Re-run ledger + tax
pipeline", "Re-run GSTR pipeline", "Re-run FY2025-26 ledger pipeline",
"Re-run Ledger vs GSTR-1 pipeline") -- correct for this project's own
real documents, wrong as a primary action for "a generalized audit
inconsistency detection portal" a future user will point at entirely
different entities and periods. Specific asks: (1) remove the
dataset-specific buttons from the primary UI; (2) never expose
`FY2025-26`/`GSTR-1`/other development names as primary actions; (3) the
system should classify documents and decide which pipeline(s) apply,
automatically; (4) keep the specialized pipelines internally, expose them
through a generic orchestration layer; (5) one generic action (e.g.
"Re-run Analysis") in place of the four buttons; (6) communicate generic
stages (extraction/classification/reconstruction/normalization/
consistency checks/audit analysis/report generation); (7) show a
document's specialized checks dynamically, after classification, rather
than hardcoded into the sidebar; (8) don't remove the underlying
specialized logic, and don't modify validated audit/backend logic
unnecessarily. Separately: the sidebar was showing "Pipeline run failed:
No module named 'sklearn'" -- investigate which pipeline imports it and
why, decide if it's part of the intended architecture (add the dependency
correctly if so, remove/isolate if obsolete) without blindly installing
it or touching audit logic, and make sure it's never presented as an
audit finding.

### 18.1 Investigation -- the sklearn error, not guessed

`grep` across every non-test `.py` file found exactly one import site:
`scripts/rule_ml_anomaly_detection.py` (`from sklearn.ensemble import
IsolationForest`, `from sklearn.preprocessing import StandardScaler`).
Reading that module's own docstring confirmed it is **Rule 8**: a real,
tested, documented part of the detection engine (an Isolation Forest over
engineered per-leg features -- see its own module docstring for why that
model and those features), not development-only scaffolding --
`tests/test_rule_ml_anomaly_detection.py` already exercises it, and
`combined_report.py` reports its output as a standard dataset-level
section alongside Benford's Law. `combined_report.py` imports it at
**module level** (`from rule_ml_anomaly_detection import
detect_anomalies`, line 108) -- so *any* pipeline that imports
`combined_report` (all four of them, directly or via `run_ledger_only_
report()`) fails at import time on an environment without scikit-learn,
which is exactly the reported symptom.

Checked every `requirements*.txt` in the project: `requirements-dev.txt`
listed only `pytest`; `dashboard/requirements.txt` listed `streamlit`/
`pandas`/`plotly`. **Neither `pdfplumber` nor `scikit-learn` was declared
anywhere**, despite both being required by `scripts/` (confirmed by
grepping every top-level `import`/`from` across `scripts/*.py`: `pandas`,
`pdfplumber`, `sklearn` are the three third-party deps actually used,
alongside `reportlab` in two test files only). This sandbox happened to
already have all of them installed (hence no local reproduction of the
failure), but a fresh environment built strictly from the project's own
requirements files would reproduce it exactly -- the real root cause,
distinct from "corrupted install" or "wrong Python version."

**Verdict: required, not obsolete.** Fixed per the explicit instruction
("if required, add the correct dependency... do not blindly install it")
by declaring it properly rather than just `pip install`-ing it locally:
a new root `requirements.txt` (`pandas>=2.0`, `pdfplumber>=0.11`,
`scikit-learn>=1.3`) that `dashboard/requirements.txt` now pulls in via
`-r ../requirements.txt`, and `requirements-dev.txt` pulls in via
`-r requirements.txt` (plus `reportlab`, previously undeclared too,
used only by two tests' synthetic-PDF fixtures). Verified with `pip
install --dry-run` against both `requirements-dev.txt` and `dashboard/
requirements.txt`: both resolve cleanly and correctly pull in
`scikit-learn>=1.3`. No change to `rule_ml_anomaly_detection.py` or
`combined_report.py`'s import structure -- the fix is entirely in
dependency declaration, per instruction (8) not to touch validated
backend logic unnecessarily.

### 18.2 A second, related generalization bug found during investigation

While reading `adapt_gstr_to_schema.py`/`adapt_tax_docs_to_schema.py`/
`adapt_ledger_to_schema.py` for the orchestration work below, each had a
fallback: `if not os.path.exists(os.path.join(PROJECT_DIR, "data")):
PROJECT_DIR = r"C:\Users\<user>\Desktop\audit_project"` -- a hardcoded
path to one specific person's own machine. Not something asked about
directly, but squarely the same class of problem as the rest of this
addendum (a product meant to be generalized, hardcoding one developer's
own environment) and low-risk to fix (pure path-resolution plumbing, not
audit logic): removed from all three files. The `SCRIPT_DIR`-relative
computation immediately above it (`PROJECT_DIR =
os.path.abspath(os.path.join(SCRIPT_DIR, ".."))`) is already correct on
any machine, since it derives the project root from where the file
itself lives rather than assuming a working directory or a specific
user's path -- the fallback could only ever produce a second,
differently-wrong, equally nonexistent path on literally any other
machine (or even the same machine under a different username),
making a genuine setup problem harder to diagnose, never easier.

### 18.3 The generic orchestration layer

Added `run_full_analysis()` to `dashboard/data.py` -- the single generic
entry point instruction (5) asked for. Pure orchestration: it calls the
same four existing, completely unchanged `run_combined_pipeline()` /
`run_gstr_pipeline()` / `run_fy2025_26_pipeline()` /
`run_ledger_gstr_pipeline()` functions, in the same order, deciding
*whether* each applies by simply trying it and reading the result rather
than re-implementing or guessing each pipeline's own file-existence
preconditions:
- a pipeline whose required source documents aren't present raises
  `FileNotFoundError` from deep inside it -- every one of the four
  already does this today on a plain missing-file `open()` or an
  explicit check (e.g. `adapt_gstr_to_schema.adapt_gstr_docs()`'s own
  `"No GSTR-1 PDFs found matching..."`) -- caught and reported as
  **"skipped"**, not a failure: a future user with, say, no GSTR
  documents at all correctly sees GST reconciliation skipped, never an
  error. This is instruction (3)'s "classify and determine which
  pipeline(s) to run automatically", implemented by reusing behavior
  the four pipelines already had, not by adding new precondition logic
  that could drift out of sync with them.
- any other exception is a genuine failure, reported as such.
- specifically an `ImportError`/`ModuleNotFoundError` (a missing optional
  dependency, the sklearn case above) is additionally flagged
  `is_dependency_error=True`.

Returns `{"ran": [...], "skipped": [...], "failed": [...]}` using only
the generic display names `_ANALYSIS_STEPS` defines ("Tax & ledger
reconciliation", "GST reconciliation", "Ledger analysis refresh",
"Ledger vs GST reconciliation") -- never "FY2025-26" or "GSTR-1", per
instruction (2). Verified against this project's own real documents:
all four report "ran", byte-for-byte the same report files as running
each pipeline individually (same rule engine, same inputs) -- then
three new tests (`tests/test_dashboard_data.py`) pin the three-way
classification itself: all-four-run against real data; all-four-skipped
(not failed) when every one is monkeypatched to raise
`FileNotFoundError`; and a mixed case where one raises
`ModuleNotFoundError("No module named 'sklearn'")` and is correctly
flagged `is_dependency_error=True` while a second, ordinary `ValueError`
is not.

### 18.4 Sidebar UI -- one generic button, specialized checks shown dynamically

The four buttons (instruction 1) are replaced by one: **"🔁 Re-run
Analysis"**, which calls `run_full_analysis()` and shows a plain-language
summary ("✅ Refreshed: Tax & ledger reconciliation, GST reconciliation,
..."). A dependency-error failure renders as "⚠️ Setup issue, not an
audit finding -- **X** needs a Python package that isn't installed...",
visually and textually distinct from an ordinary pipeline failure's
"**X** failed: ..." -- this is instruction (8)'s "never presented as an
audit finding": the message is sidebar-only session state, never written
into any report JSON, so it can never surface inside the Audit/Tax &
Compliance tabs' findings either. The old sidebar caption that named
every special case up front ("A GSTR-1/GSTR-3B month, trial balance, or
tax-statement PDF still needs its exact established filename...") is
gone, replaced by instruction (7)'s per-document dynamic version: a new
**"Used by"** column (`dd.DOCTYPE_ANALYSIS_USE`, keyed by the document's
own classified doctype) in the Process Documents tab's "already on file"
table, showing e.g. "GST reconciliation" next to a GSTR-1 PDF or "Ledger
analysis" next to a ledger -- generated from the document's own
classification, not a static list of every possible case. Every
now-stale pointer elsewhere in the dashboard that named one of the old
four buttons (5 separate "click Re-run ... pipeline" hints across the
Overview/GSTR/Ledger-vs-GSTR tabs, plus two sidebar captions) was found
by grep and updated to point at the one new button.

The two established documents' own safety-gated, per-document ledger
**reconstruction** controls (instruction 8's "don't remove the underlying
specialized logic") are functionally untouched -- same frozen-baseline
checkbox gate, same `run_full_ledger_reconstruction()` call -- just moved
under a collapsed "Advanced: re-extract an established source document"
expander instead of sitting in the main sidebar flow, since instruction
(2) applies to these too (their button labels do name "FY2025-26"/
"FY2024-25 (frozen baseline)", appropriately, for an *advanced*
maintenance action an administrator already knows they're choosing
between two specific documents for -- generalizing the label away would
make the control useless for its only real purpose). Verified live: the
frozen-baseline button is confirmed disabled before its acknowledgment
checkbox is ticked, matching its pre-existing, untouched behavior.

### 18.5 Verification

`python3 -m py_compile` clean on every touched file. Full suite: **453
tests, 453 passing** (450 pre-existing + 3 new `run_full_analysis()`
tests, ~260s). Live, via a headless Streamlit server + Playwright: zero
actual `<button>` elements remain for any of the four old labels
(confirmed via `get_by_role("button")`, not a text search, since the
explanatory sidebar caption deliberately quotes the old names in prose);
exactly one new button, "🔁 Re-run Analysis"; clicking it live
end-to-end re-ran all four pipelines against this project's real
documents (confirmed via the "✅ Refreshed: ..." summary, including the
sklearn-dependent ML anomaly step completing without error) and showed
the correct generic success summary; the "Used by" column renders
correctly per document type in the already-on-file table; the Advanced
expander's frozen-baseline checkbox gate still starts disabled.
Grepped every touched file (`dashboard/app.py`, `dashboard/data.py`, the
three `adapt_*_to_schema.py` files, `tests/test_dashboard_data.py`, all
three requirements files, `README.md`) for the AI assistant's or its
maker's name -- clean.

Files touched: `dashboard/app.py`, `dashboard/data.py`,
`scripts/adapt_ledger_to_schema.py`, `scripts/adapt_gstr_to_schema.py`,
`scripts/adapt_tax_docs_to_schema.py`, `tests/test_dashboard_data.py`,
`requirements.txt` (new), `requirements-dev.txt`, `dashboard/
requirements.txt`, `README.md`.

## 19. Final state (supersedes section 17 above)

- **453 tests, 453 passing.**
- **The sidebar's pipeline controls are now one generic "🔁 Re-run
  Analysis" button** (Addendum 10), replacing four buttons each named
  after a specific development document. It auto-detects which of the
  four specialized pipelines apply (by trying each and reading the
  result) and reports a plain-language summary; a missing optional
  dependency is flagged distinctly as a setup issue, never as an audit
  finding. The underlying four pipelines are completely unchanged.
- **A document's specialized checks are shown dynamically, per document,
  after classification** (Addendum 10's "Used by" column), not via a
  static sidebar caption naming every possible case up front.
- **scikit-learn (and pdfplumber) are now properly declared
  dependencies** (`requirements.txt`, Addendum 10) -- the "No module
  named 'sklearn'" failure was a genuine undeclared-dependency gap for
  Rule 8 (real, tested ML anomaly detection, not obsolete), fixed by
  declaring the dependency correctly rather than touching Rule 8 itself.
- **No hardcoded single-machine path fallback remains** in any schema
  adapter (Addendum 10.2) -- removed as a direct consequence of the same
  "generalized product" principle, though not explicitly requested.
- **450 tests, 450 passing.**
- **Cold load is ~1.0-1.3s to a usable nav bar and ~1.3s (server) /
  ~2-3.5s (end to end) to the Audit and Tax & Compliance groups' full
  content** -- down from the ~20-25s originally reported, via a
  lazy-loading script reorder (Addendum 9) that queues those 6 of 8
  content areas before the one real, unavoidable cost: the one-time
  document-classification scan (~6.3s for 11 real documents, confirmed
  via profiling to be genuine PDF text-extraction work, not reducible
  without touching classification logic). Only the Process tab and the
  Documents group's dynamic ledger tabs wait on that scan now, with
  their own spinner exactly where the wait is real; total time to the
  fully-settled Process tab is ~7-8s, not ~20-25s.
- **A single upload only re-classifies what changed**: `discover_
  documents_fast()`'s per-file, fingerprinted `st.session_state` cache
  (Addendum 9) replaced the old all-or-nothing `@st.cache_data` scan --
  uploading one new file now re-scans in well under a second instead of
  repeating the full directory scan every time.
- **Processing a document shows a live, centered, multi-stage card**
  (Document uploaded / Scanning document / Extracting data / Running
  audit checks / Preparing results, Addendum 9) driven by `process_
  ledger_document()`'s new optional `on_stage` callback -- real stages
  tied to real internal boundaries, no fake percentage, centered both
  vertically and horizontally in the main content area, replacing the
  old small `st.status(...)` box near the nav.
- **A same-named replacement upload is never a dead end**: it's flagged
  for re-processing even if it was previously processed (Addendum 9),
  surfacing a "● Replaced -- Ready to Re-process" card and button
  instead of the old "Nothing new to process" message or a silently
  stale "Processed" status.
- Top navigation is grouped into 4 sections -- Process / Audit / Tax &
  Compliance / Documents -- each a tab, with nested tabs for any group
  with more than one page. No new dependency; built on nested
  `st.tabs()`, confirmed to work on the installed Streamlit version
  before relying on it.
- The upload area reads as a deliberate first step ("Add source
  documents" + a clear description) rather than a bare default widget,
  and newly uploaded documents appear immediately below as "N document(s)
  ready to process" cards with a status badge and a **Process Document**
  (or **Re-process Document**) button.
- One status vocabulary (Uploaded / Ready to Process / Processed /
  Failed, plus the Replaced variant above) is used everywhere a
  document's state is shown, including a real Document/Type/Status/
  Last-processed table for everything already on file.
- The one-time document-scan spinner still visibly distinguishes a
  genuine first page load from the rerun right after an upload --
  different wording, different (centered) placement.
- The sidebar's Data panel is compact (two metrics + a last-processed
  line) with the full file list behind a closed-by-default expander; its
  three report-regenerating "Re-run ... pipeline" buttons refresh the
  already-rendered Audit/Tax & Compliance tabs immediately (Addendum 9),
  not on some later unrelated rerun.
- Dashboard ledger tabs remain fully dynamic (Addendum 3/7): generated
  from whatever ledger-shaped PDFs are actually in `data/raw_pdfs/`,
  each carrying an honest confidence verdict; a newly uploaded
  ledger-shaped PDF still gets an identical analysis tab the moment it's
  processed, with zero code changes.
- No internal file paths or AI-attribution text appear anywhere in the
  dashboard's user-facing UI or in this project's own documentation --
  verified by a programmatic audit, not assumed.
- Scope boundary unchanged from Addendum 2: Rules 4/6 and
  Ledger-vs-GSTR-1 remain tied to their specific existing documents.

## 20. Addendum 11: "Audit" became a per-ledger document picker (fixes "same page regardless of file")

**Trigger -- a direct bug report, same day, with a screenshot:** "whether
i put 2024 file or 2025 file it shows the same page." Not a crash --
Audit's three sub-tabs (Overview / Ledger Findings / Benford's Law) were
rendering correctly, just always from `output/combined_report.json`,
which only ever resolves to the frozen FY2024-25 baseline. Two options
were presented and discussed before any code changed (per the standing
"discuss before you change" practice): (1) add a one-line "You're viewing
FY2024-25" indicator to the existing hardcoded tabs, changing nothing
structural, or (2) make "Audit" itself dynamic -- a document picker
covering whichever ledger is selected, retiring the separate per-ledger
"Documents" tab group in favor of one consistent place. Chosen: **option
2**.

### 20.1 Root cause, traced not guessed

`dashboard/app.py` line 1277 (pre-change): `report =
dd.load_combined_report()` -- no argument, so `dd.load_combined_report()`
always resolves to `output/combined_report.json`. This fed Audit's three
sub-tabs *and* Tax & Compliance's "Tax Reconciliation" sub-tab. Meanwhile
`render_generic_ledger_tab(info)` already existed, parameterized
correctly via `dd.ledger_tab_info(pdf_path)`'s own `report_path` -- but
was only ever called from the separate "Documents" group's dynamic-tab
loop, which **excluded** the frozen baseline on purpose (`if
dd.ledger_slug(_doc["path"]) == dd.FROZEN_BASELINE_SLUG: continue`) because
the baseline kept its own, richer, hardcoded tab set. Two correct,
independently-working pieces -- a per-document renderer and a
document-agnostic Overview -- had simply never been merged into one.

### 20.2 What would have been lost by a naive merge, and how each was kept

Before replacing Audit's three tabs with `render_generic_ledger_tab()`
calls, each one's content was checked against what the generic renderer
already covers, so nothing real got silently dropped:

- **Overview's "Tax & Trial Balance status" and "GSTR-1/GSTR-3B status"
  summary cards** -- not ported. This is a condensed preview of numbers
  already shown in full under **Tax & Compliance** (Tax Reconciliation /
  GST Compliance tabs), reachable one tab over, not deleted.
- **Benford's Law tab's segmentation-evidence section** (voucher-type/
  account chi-square breakdown, `scripts/analyze_benford_segmentation.py`
  -- genuinely only computed for the frozen FY2024-25 baseline, reading
  `data/general_ledger.csv` directly) -- this one IS unique, real analysis
  with no equivalent elsewhere. Kept by appending it, unchanged, below
  `render_generic_ledger_tab()`'s own output whenever the frozen baseline
  is the selected ledger (`if _audit_selected["slug"] ==
  dd.FROZEN_BASELINE_SLUG:`), rather than inside the generic function
  itself (which stays correctly empty of it for every other ledger, where
  the data doesn't exist).
- **`render_generic_ledger_tab()`'s own caption** ("ITR vs Form 26AS and
  Trial Balance reconciliation are not shown here ... out of scope") was
  blanket-true for FY2025-26 but would have been **factually wrong** for
  the frozen baseline once it started rendering through this same
  function -- that data genuinely exists for FY2024-25, just in a
  different tab. Made conditional on `info["slug"] ==
  dd.FROZEN_BASELINE_SLUG`: the frozen baseline gets "...are shown in the
  Tax & Compliance tab, not duplicated here"; every other ledger keeps the
  original "not yet generalized, see Out of scope" wording.

### 20.3 What changed

- `dashboard/app.py`: top nav reduced from 4 groups (Process / Audit /
  Tax & Compliance / Documents) to 3 (Process / Audit / Tax &
  Compliance). Audit's old `tab_overview, tab_ledger, tab_benford =
  st.tabs(...)` and their ~400 lines of hardcoded content were removed
  entirely. In their place, after the one document-classification scan
  this script run already does (`_all_documents`), a single block builds
  `_audit_ledger_infos` from `dd.discover_ledger_documents(_all_documents)`
  -- **including** the frozen baseline this time, unlike the old
  "Documents" loop it replaces -- and renders a `st.selectbox("Ledger",
  ...)` driving one call to `render_generic_ledger_tab()`, wrapped in the
  same try/except the old per-tab loop had so one malformed report can
  never take the page down. The old "Documents" group's dynamic-tab loop
  is gone; this picker is what it became.
- `render_generic_ledger_tab()`'s caption made conditional (see 20.2
  above); its docstring's reference to "the dynamic-tab loop below" (its
  old caller) corrected to describe its actual caller, the Audit picker.
- A real, deliberate performance trade-off, stated plainly rather than
  hidden: Audit previously rendered *before* the document-classification
  scan (fast, ~1.3s); it now needs that scan's result (to know which
  ledgers exist) and so waits behind it, same as the old Process tab's
  status table and "Documents" group already did. The "Lazy-loading
  restructure" comment block (Addendum 9) was corrected to say so
  accurately instead of still claiming Audit renders before the scan.
- `README.md`'s "Dashboard: upload and test" section rewritten to
  describe 3 nav groups and the Ledger dropdown, replacing the stale
  4-group/Documents-tab description.
- `dashboard/data.py`: `discover_ledger_documents()`'s docstring updated
  (one phrase) to describe its current caller (the Audit picker) instead
  of "the dashboard's dynamic ledger-tab loop." No behavioral change --
  the function itself, and every other function used by this fix
  (`ledger_tab_info()`, `LEDGER_PDF_INFO`, `FROZEN_BASELINE_SLUG`), was
  read and reused exactly as it already existed, not modified.

### 20.4 Verification

`python3 -m py_compile dashboard/app.py dashboard/data.py` clean. Full
suite: **453 tests, 453 passing** (dashboard/data.py and every script
untouched by this round -- UI-layer-only change, as expected). Live run
(`streamlit run dashboard/app.py`, driven via a local Playwright script
since the dashboard's own hostname isn't reachable from the sandboxed
browser tool in this environment): confirmed top nav shows exactly
**Process / Audit / Tax & Compliance** (no "Documents"); Audit's **Ledger**
dropdown lists both **FY2025-26** and **FY2024-25 (frozen baseline)**;
selecting FY2025-26 shows its own numbers with the "not yet generalized"
caption; selecting the frozen baseline shows **total ledger legs
14,625, flagged legs 1,271, 66 voucher-number gaps, Benford chi-square
229.74, MAD 0.01061 "Acceptable conformity"** -- matching
`dashboard/data.py`'s own `kpi_summary()`/`benford_summary()`/
`voucher_gap_dataframe()` output from `output/combined_report.json`
exactly, confirming this is genuinely the same data the old hardcoded
Overview showed, not a different or approximated number -- plus the
caption correctly reading "...shown in the Tax & Compliance tab, not
duplicated here," plus the segmentation-evidence section (8/8 voucher
types and 15/15 accounts deviating, Purchase/Contra flagged as the
strongest outliers) appended below it, present only for this selection.
Tax & Compliance tab re-checked unaffected: ITR vs Form 26AS and Trial
Balance both still show "No mismatches" against the same real numbers as
before. Grepped every touched file (`dashboard/app.py`,
`dashboard/data.py`, `README.md`) for the AI assistant's or its maker's
name -- clean; repo-wide sweep also clean.

Files touched: `dashboard/app.py`, `dashboard/data.py`, `README.md`.

## 21. Final state (supersedes section 19 above)

- **453 tests, 453 passing.**
- **Top navigation is 3 groups -- Process / Audit / Tax & Compliance**
  (Addendum 11; was 4, including a separate "Documents" group). Audit is
  a **Ledger** dropdown over every processed ledger -- the two
  established documents plus anything newly uploaded and processed --
  instead of three tabs permanently hardcoded to the FY2024-25 baseline.
  This is the direct fix for "whether i put 2024 file or 2025 file it
  shows the same page." The frozen baseline's unique segmentation-
  evidence analysis is preserved, shown only when it's the selected
  ledger; nothing the old "Documents" group did was dropped, it was
  folded into Audit.
- **The sidebar's pipeline controls are one generic "🔁 Re-run Analysis"
  button** (Addendum 10), replacing four buttons each named after a
  specific development document. It auto-detects which of the four
  specialized pipelines apply (by trying each and reading the result) and
  reports a plain-language summary; a missing optional dependency is
  flagged distinctly as a setup issue, never as an audit finding. The
  underlying four pipelines are completely unchanged.
- **A document's specialized checks are shown dynamically, per document,
  after classification** (Addendum 10's "Used by" column), not via a
  static sidebar caption naming every possible case up front.
- **scikit-learn (and pdfplumber) are properly declared dependencies**
  (`requirements.txt`, Addendum 10) -- the "No module named 'sklearn'"
  failure was a genuine undeclared-dependency gap for Rule 8 (real,
  tested ML anomaly detection, not obsolete), fixed by declaring the
  dependency correctly rather than touching Rule 8 itself. This requires
  a one-time `pip install -r requirements-dev.txt` per environment (a
  deployment/setup step, not something an end user should ever run) --
  not yet confirmed done on the user's own machine as of this addendum.
- **No hardcoded single-machine path fallback remains** in any schema
  adapter (Addendum 10.2).
- **Cold load is ~1.0-1.3s to a usable nav bar, and Tax & Compliance's
  full content renders before the one real, unavoidable cost** (the
  one-time document-classification scan, ~6.3s for 11 real documents).
  Audit now also waits behind that scan (Addendum 11, a deliberate
  trade: it needs the scan's result to know which ledgers exist to
  offer in its picker) -- same wait the old Process tab's status table
  and "Documents" group already had, not a new one added to the page's
  total. Total time to the fully-settled page is ~7-8s, not the ~20-25s
  originally reported.
- **A single upload only re-classifies what changed**: `discover_
  documents_fast()`'s per-file, fingerprinted `st.session_state` cache
  (Addendum 9).
- **Processing a document shows a live, centered, multi-stage card**
  (Addendum 9) tied to real internal pipeline stages.
- **A same-named replacement upload is never a dead end** (Addendum 9).
- One status vocabulary (Uploaded / Ready to Process / Processed /
  Failed, plus Replaced) is used everywhere a document's state is shown.
- The sidebar's Data panel is compact, with the two established
  documents' dedicated, safety-gated reconstruction controls tucked under
  a collapsed "Advanced" expander.
- Dashboard ledger analysis remains fully dynamic (Addendum 3/7/11):
  generated from whatever ledger-shaped PDFs are actually in
  `data/raw_pdfs/`, each carrying an honest confidence verdict; a newly
  uploaded ledger-shaped PDF gets an identical picker entry the moment
  it's processed, with zero code changes.
- No internal file paths or AI-attribution text appear anywhere in the
  dashboard's user-facing UI or in this project's own documentation --
  verified by a programmatic audit, not assumed.
- Scope boundary unchanged from Addendum 2: Rules 4/6 and
  Ledger-vs-GSTR-1 remain tied to their specific existing documents.


## 22. Addendum 12: Audit and Tax & Compliance no longer silently show stored dev-dataset results with nothing processed

### 22.1 The problem, as reported

With no newly uploaded/processed document, Audit (Addendum 11's picker)
still showed the stored FY2025-26 report by default -- real, correct
numbers, but for a document the person watching never asked to see this
run. Explicit requirement: for a genuinely generalized portal, "no
processed document" must mean an honest empty state, not a silent
fallback to whichever of this project's own two development datasets
(FY2024-25/FY2025-26) happens to already have output on disk. Two
`AskUserQuestion` rounds settled scope before any code changed:

1. Apply the fix to the live dashboard now (not just a hypothetical fresh
   deployment) -- **confirmed: apply everywhere, including now.**
2. Extend the same principle to Tax & Compliance, which also
   unconditionally showed FY2024-25's hardcoded tax/trial-balance data --
   **confirmed: fix both tabs together**, after being shown a real
   architectural asymmetry (next section) so that "yes" was an informed
   choice, not one made without the trade-off in view.

### 22.2 Audit: excluded, not just reordered

Addendum 11's picker listed every document `discover_ledger_documents()`
found a report for, including the two established ones in
`LEDGER_PDF_INFO` (`ledgers_redacted` / `Ledgers_Anonymised`). Fixed by
filtering those two slugs out of `_audit_ledger_infos` unconditionally
(`dashboard/app.py`, the picker block) -- not "unless already processed,"
*always* excluded, because the goal is "only what the generic
upload-and-process flow actually produced this run," and the two
established documents' reports exist on disk regardless of what anyone
did today. Empty-state copy changed to the person's exact requested text:
"No audit results available yet. Upload and process a document to view
findings." -- no dropdown, no metrics, no priority findings, nothing from
`LEDGER_PDF_INFO`-keyed reports, confirmed live (Playwright) with the
real dashboard's own data still on disk (11 documents, 2 processed) --
Audit shows only the empty-state line, nothing else.

One side effect flagged rather than silently resolved: the frozen
baseline's segmentation-evidence section (Addendum 11, `dashboard/app.py`
~line 1892) is keyed to `_audit_selected["slug"] == FROZEN_BASELINE_SLUG`,
which can now never be true inside the picker (that slug is excluded
before the picker ever renders). The code is left in place, commented as
intentionally-unreachable dead code rather than deleted, because deleting
it is a real judgment call (lose the segmentation walkthrough from the UI
entirely vs. keep the code for a possible future "view the baseline
anyway" affordance) that belongs to the person, not something to decide
silently while fixing something else.

### 22.3 Tax & Compliance: a genuinely different architecture, surfaced before touching it

Audit's fix generalizes cleanly because each ledger has its own report
file, so "was *this* document processed" is a real, per-document
question. Tax & Compliance is not built that way, and applying the same
literal rule would not have produced the same kind of empty state:

- **Tax Reconciliation** (ITR vs 26AS + Trial Balance) reads
  `output/combined_report.json` plus two fixed source files
  (`data/form26as.csv`, the trial balance PDF) that are permanently tied
  to the established documents -- no generic path exists today for a
  different company's tax documents to ever populate this, regardless of
  what the UI does.
- **Ledger vs GSTR-1** is explicitly, permanently hardcoded to the
  FY2025-26 document by its own existing code comment -- same situation.
- **GST Compliance** is the one real exception: a new month's
  correctly-named GSTR-1/GSTR-3B file genuinely is picked up by
  `run_gstr_pipeline()` (Addendum 10), so this one is actually generic.

This was surfaced to the person with a second `AskUserQuestion` (four
options: gate on "has any pipeline run," leave data visible with an
honest caption, split by which checks can ever generalize, or defer Tax
& Compliance entirely) *before* writing any code for this tab, rather
than silently implementing a version of "fix both tabs together" that
might read as broken rather than correctly generalized. **Chosen: gate
on "has a pipeline run happened"** -- i.e. does the relevant fixed
report file exist yet -- explicitly accepting the stated trade-off that
this is not per-document like Audit: once **🔁 Re-run Analysis** has
been clicked once (even against the established baseline), all three
sub-tabs stay populated on every later visit, for everyone, until the
output files are removed.

### 22.4 What changed

`dashboard/app.py`:
- Audit's ledger-picker loop now skips any document whose slug is a
  `dd.LEDGER_PDF_INFO` key; empty-state caption updated to the exact
  requested wording.
- `tab_tax` (ITR vs 26AS + Trial Balance) wrapped in `if report is None:
  <empty-state caption> else: <existing cards, unchanged>` --
  `report = dd.load_combined_report()` returns `None` exactly when
  `output/combined_report.json` doesn't exist, the same signal
  `pipeline_status()` already used for the sidebar's own stage
  checkmarks.
- `tab_gstr` (GST Compliance) and `tab_ledger_gstr` (Ledger vs GSTR-1)
  needed **no code change** -- both already branched on
  `gsum["generated_at"] is None` / `lg_summary["generated_at"] is None`,
  which `gstr_summary()`/`ledger_gstr_summary()` already derive from
  their report being `None`. That existing branch already implemented
  exactly the approved "gate on has it run" behavior; confirmed by
  reading both functions rather than assumed from the UI looking right.

No change to any rule, pipeline, adapter, or report-generation code --
UI-layer gating only, same standard as every prior addendum in this
file.

### 22.5 Verification

`python3 -m py_compile dashboard/app.py` clean;
`python3 -c "import ast; ast.parse(...)"` clean (the Tax Reconciliation
reindent, done by a small script rather than by hand, was checked this
way before trusting it). Full suite: **453 tests, 453 passing**, run
twice (once after the Audit change, once after the Tax & Compliance
change) -- the suite now measured at ~5 minutes wall-clock, not the ~2
minutes this round's first attempt assumed and timed out on; no test
file touched, as expected for a UI-only change.

Live (`streamlit run dashboard/app.py`, driven via a local Playwright
script): with the real dashboard's own existing output on disk (11
documents, 2 processed, same state as every prior addendum's live
check), Audit shows exactly the one-line empty-state caption and nothing
else -- no **Ledger** dropdown, no metrics. Tax & Compliance's Tax
Reconciliation sub-tab, by contrast, still shows real data ("1/1
deductor(s) reconciled," "82/82 matched accounts reconciled") -- the
approved, expected difference from Audit, not a bug: the fixed pipeline
*has* already run in this deployment. Separately, to verify the actual
empty-state branch (not just that it's unreachable in today's already-
run deployment): `output/combined_report.json`,
`output/gstr_report.json`, and `output/ledger_gstr_reconciliation_report.
json` were moved out of `output/` (not deleted), the dashboard reloaded,
and all three Tax & Compliance sub-tabs confirmed to show their
empty-state captions with no data -- then the three files were moved
back and reloading confirmed the real dashboard was unaffected (same
numbers as before the test). Repo-wide grep for the AI assistant's or
its maker's name, re-run after this round's edits -- clean.

Not independently re-verified end-to-end this round: a brand-new
(non-established) ledger document being uploaded, processed, and
appearing correctly in Audit's picker. The underlying mechanism
(`discover_ledger_documents()`/`ledger_tab_info()`) is unchanged by this
round's edit -- the new filter only adds a slug-membership exclusion
before appending to the picker list, nothing about how a new document's
entry is built -- and is covered by `tests/test_dashboard_data.py`'s own
existing tests for both the known-document and unprocessed-document
cases. No second ledger-shaped PDF was available in this session to
drive a true end-to-end live re-check of that specific path.

Files touched: `dashboard/app.py`, `BASELINE_CHECKPOINT_V3.14.md`.

## 23. Final state (supersedes section 21 above)

Everything in section 21 still holds, plus:

- **Audit never shows the two established documents' (FY2024-25/
  FY2025-26) stored results.** Its picker only ever lists documents
  processed through the generic upload-and-process flow this run;
  with none, it shows one line -- "No audit results available yet.
  Upload and process a document to view findings." -- nothing else.
- **Tax & Compliance shows an honest empty state per sub-tab until its
  own underlying pipeline has produced output at least once in this
  deployment** -- gated on each sub-tab's own fixed report file
  existing, not on any specific document, since none of the three
  checks have a true per-document concept today (22.3). Once
  **🔁 Re-run Analysis** has run once, all three stay populated for
  everyone thereafter -- a deliberate, approved difference from Audit's
  per-document behavior, not an inconsistency.
  - Tax Reconciliation (ITR vs 26AS + Trial Balance): gate added this
    round.
  - GST Compliance and Ledger vs GSTR-1: already correctly gated,
    discovered rather than assumed -- no code change needed.
- Scope boundary (Addendum 2, restated 22.3): ITR vs 26AS, Trial
  Balance, and Ledger vs GSTR-1 still have no generic processing path --
  this round changed what the UI shows when they haven't run, not
  whether a different company's documents could ever populate them.
  Still explicitly out of scope, still in the README.


## 24. Addendum 13: UX feedback pass -- freshness context, friendlier errors, trimmed explanatory text

Direct feedback on a round of screenshots, structured as five specific
points. Each was judged on its own merits rather than applied verbatim --
two were reframed before building, one was deferred to an explicit
`AskUserQuestion` rather than guessed.

### 24.1 What was accepted as-is and built

- **Sidebar "Processed 2" next to an empty Audit tab read as a
  contradiction.** It isn't one (Addendum 12: Audit deliberately excludes
  this project's 2 pre-built baseline documents from its count), but nothing
  told a reader that. One caption added under the sidebar's Data panel,
  shown whenever the processed count is nonzero: "Includes this project's
  pre-built baseline documents, which Audit deliberately doesn't list --
  only documents you process yourself, above, appear there."
- **No freshness context inside Audit itself** (only the sidebar had
  timestamps, and only for the fixed baseline pipeline). `render_generic_
  ledger_tab()` now opens with "Showing: `<label>` -- last analyzed
  `<processed_at>`" -- real data, not invented: `processed_at` is the
  genuine timestamp `process_ledger_document()`'s own sidecar JSON writes,
  and every document reaching this renderer via Audit's picker now has one
  (Addendum 12 excludes the one case where it would be `None`).
- **Raw Python exceptions in `st.error`** (`No module named 'sklearn'`
  verbatim). Both failure branches in the sidebar's Re-run Analysis result
  handling now lead with a friendly, specific headline and move the raw
  exception text into a collapsed "Technical details" expander, rather than
  pasting it into the sentence a reader sees first.
- **Dense explanatory paragraphs under headings** -- concretely fixed on
  the Ledger vs GSTR-1 tab (the densest screen in the app, and the one in
  the feedback screenshot): the method description and the "strong
  cross-validation evidence" info box were each cut to one sentence, with
  the full original text (unchanged, nothing dropped) moved into an "ⓘ
  Learn more" / "ⓘ Why this matters" expander. Scoped deliberately to this
  one tab as a demonstrated pattern, not applied as a global sweep across
  every caption in the file -- the other screens weren't shown as dense in
  the feedback, and rewriting captions nobody flagged risks losing real
  caveats for no verified gain.

### 24.2 What was reframed, not built as literally suggested

- **"Current Audit: ABC Ltd. · FY2025-26" banner.** Checked first, not
  assumed: nothing in this codebase extracts a company name from any
  document -- `"company"` appears exactly once in the whole codebase, as an
  entry in `reconstruct_ledger_entries.py`'s stopword list, not a parsed
  field. Inventing a fake company label would be worse than the status quo.
  Built the honest equivalent instead (document label + real
  `processed_at`, above) and flagged company-name extraction as a real,
  separate, buildable feature if wanted later -- not guessed into this
  round.
- **"Analysis could not be completed. Please contact the administrator..."**
  for setup errors. The existing dependency-error message (naming the
  missing package and giving the exact `pip install -r requirements.txt`
  fix) is strictly more useful than a generic deflection for a solo-run
  tool with no administrator role -- kept that specificity, only moved the
  raw exception text out of the sentence (24.1).

### 24.3 Deferred to an explicit decision, not guessed

**"No clear audit reset / new-audit concept."** Ambiguous in a system that
already has a per-document picker (Audit's dropdown) as its "switch which
result you're viewing" control, and where a literal "Clear" would mean
deleting a processed document's output from disk with no undo -- and could
never apply to the 2 established baselines (standing rule: never touched).
Asked via `AskUserQuestion` rather than guessed which of three real options
was meant (relabel the existing picker / build real delete / skip this
round). **Chosen: relabel the existing picker.** No destructive feature
built. The picker's label changed from the generic "Ledger" to "Switch
audit" (with a `help=` tooltip), and a one-line pointer ("More than one
document has been processed -- pick which one's results to view below.")
now shows above it, but only when there's actually more than one document
to switch between.

### 24.4 Verification

`py_compile` + `ast.parse` clean. Full suite: **453 tests, 453 passing**
(~5.5 minutes wall-clock; no test file touched -- UI-layer-only change, as
expected). Live (Playwright): sidebar caption confirmed rendering exactly
as written, with the real "Processed 2" / baseline-exclusion numbers still
in place; Ledger vs GSTR-1 tab confirmed showing the trimmed one-line
caption and info box, with both expanders confirmed to contain the full,
unedited original text when opened (screenshotted, not just read from
markup). Repo-wide grep for the AI assistant's or its maker's name,
re-run -- clean.

**Not independently verified live this round:** the Audit-tab "Showing:
`<label>` -- last analyzed `<timestamp>`" caption and the relabeled
"Switch audit" picker, because no non-baseline ledger document is
currently processed in this deployment to exercise that code path (no
second ledger-shaped PDF is available in this session, consistent with
the same limitation noted in Addendum 12) -- fabricating test data to
force a live check would violate this project's own "real documents only"
discipline. Confidence instead comes from: the code only adds string
formatting around an existing, already-tested field (`info.get
("processed_at")`, covered by `tests/test_dashboard_data.py`'s existing
`ledger_tab_info()` tests), the surrounding `render_generic_ledger_tab()`
call path is unchanged, and the full test suite still passes. Worth a
real look the next time an actual new document is processed.

Files touched: `dashboard/app.py`, `BASELINE_CHECKPOINT_V3.14.md`.

## 25. Addendum 14: scope boundary -- no fabricated company/audit-session grouping in UI (decided, not built)

Raised by the user while reviewing Addendum 13's "switch audit" relabel: her
own earlier example text for the deferred "Current Audit" banner included
`16 documents` alongside the fabricated `ABC Ltd.` company name (already
rejected in Addendum 13, section 24.2). She asked directly what `16` was
supposed to mean given the project's generalized (not single-company)
design, and whether it belonged at all.

**Answer, verified against the actual code, not assumed:** it doesn't mean
anything in the current architecture. `_audit_ledger_infos` (the list the
Audit picker switches over) holds one entry per individually processed
ledger document -- there is no grouping concept, anywhere in the codebase,
of "these N documents belong to one client's one audit." Showing a count
like `16 documents` would have to either be invented, or just always read
`1` (since the picker already shows one document's results at a time) --
neither is honest.

**Decision (user's, confirmed correct against the codebase): keep it out.**
No document count, company name, or "audit bundle" label is to appear in
the UI unless backed by real persisted state. This is not a new
restriction -- it is the same principle already applied in Addendum 13
(reject the fabricated company name) and in Addendum 12 (gate Tax &
Compliance on `report is None`, not on an assumed state) extended to this
one remaining piece. Explicitly recorded as a **deliberate scope boundary,
not an open TODO** -- a genuine multi-document "audit session" concept
(grouping a ledger with its GSTR-1, trial balance, etc. under one
client/FY) is a real, separate, larger feature (its own data model: which
documents belong to which session, who assigns them) that nothing here
pretends to have. If wanted later, it should be designed as that, not
backed into as a UI label.

Also worth naming why this isn't actually a gap hiding behind the
decision: **Audit** (per-document intrinsic checks -- Benford, duplicate
vouchers) and **Tax & Compliance** (cross-document reconciliation -- ITR
vs 26AS, Ledger vs GSTR-1) are already structurally different in exactly
the way this boundary implies. Audit's unit is genuinely one document --
that's not a limitation standing in for a missing bundle feature. Tax &
Compliance already combines multiple real documents today, but gated on
whether its pipeline has actually produced output (Addendum 12), never on
a UI claim of a bundle that doesn't exist. Checked via grep
(`BASELINE_CHECKPOINT_V3.14.md`, README's "Out of scope" section) that no
stray "Current Audit" banner, company name, or document-count text exists
anywhere in `app.py` or `data.py` -- confirmed clean, nothing to roll back.

**No code change.** This addendum documents a decision not to build
something, so the only change is documentation: this section, and a new
"Out of scope" bullet in `README.md` ("Company-level / multi-document
'audit session' grouping"). Repo-wide grep for the AI assistant's or its
maker's name, re-run after this edit -- clean.

Files touched: `BASELINE_CHECKPOINT_V3.14.md`, `README.md`.

## 26. Addendum 15: directory audit, 2026-10-06 (found, fixed, and what still needs a decision)

**Method.** The device's actual tree -- working files *and* `.git` -- was
mirrored into a scratch workspace, so every check ran against what is
really on disk rather than against a separate copy. That allowed real
`git status` / `git diff` / `git log`, the full test suite on the on-device
files, a compile + import check, a requirements-vs-imports comparison, a
grep for the no-AI-mention rule over the **whole tree and every commit
message** (not just `dashboard/` and the docs), and a docs-vs-code
consistency scan.

### 26.1 Found and fixed

- **A failing test on the developer's machine that the cloud runs could
  never show.** `tests/test_classify_document.py::test_all_real_pdfs_are_covered`
  failed: five PDFs sit in `data/raw_pdfs/` (`26AS.pdf`, `ITR_V.PDF`,
  `Computation_of_Income.pdf`, `AIS.pdf`, `ledger_index_redacted.pdf`)
  with no pinned expectation. Those PDFs are gitignored, so no cloud copy
  ever had them -- which is why every "453 passing" figure in the earlier
  addenda was true of that copy but not of this machine (on-device result
  before the fix: 452 passed, 1 failed). Each of the five was classified by
  running `classify_document()` on the actual file and the verified result
  pinned (`form_26as`, `itr_acknowledgement`, `itr_computation`, `unknown`,
  `unknown`). Where the files are absent the five new cases skip, as the
  existing ones do. **Suite is now 458 tests; on-device: 458 passed.**
- **The no-AI-mention rule was not actually clean.**
  `scripts/prepare_input.py` line 88 (a message printed to the user) named
  the assistant. Reworded. The earlier addenda's "repo-wide grep -- clean"
  statements were wrong in scope: they covered `dashboard/`, `README.md`
  and this file, not `scripts/`. The grep now covers the whole tree.
- **An inaccurate scope statement about the AIS.** README and
  `adapt_tax_docs_to_schema.py` said no AIS PDF had ever been supplied and
  that `data/raw_pdfs/` had never contained one. `data/raw_pdfs/AIS.pdf`
  does exist (6 pages, FY2025-26, dated 2026-08-18). It is **not** the
  firm's: its entries are an individual's (bank interest, professional-fee
  TDS, an immovable-property transaction), none involve the firm's
  deductor, and it belongs to the same FY2025-26 batch that
  `adapt_tax_docs_to_schema.py`'s HISTORY note identifies as a different
  person's filing. So the scope decision stands unchanged -- there is still
  no AIS for the firm -- and only the wording was corrected, in both places.

### 26.2 Found; needs the user to act (cannot be done from here)

- **The last commit is not self-consistent.** `scripts/reconstruct_ledger_entries.py`
  has ~100 lines of uncommitted changes (`assess_reconstruction_confidence()`,
  `total_legs`, `unresolved_count`, `date_range_start/end`) that the
  *committed* `dashboard/data.py` and `tests/test_dashboard_data.py` already
  call. Reproduced on a clean checkout of HEAD: 5 tests fail with
  `AttributeError: module 'reconstruct_ledger_entries' has no attribute
  'assess_reconstruction_confidence'`, and the dashboard's upload path would
  fail the same way on any fresh clone. The change is correct (it is what
  the 458 passing tests ran against) -- it was simply never `git add`ed.
  An earlier suggestion to leave this file out of commits was wrong.
- **Git history still names the assistant**: in three commit messages
  (`4bff9bb`, `a130a64`, `68d01bb`), in the first commit's
  `scripts/prepare_input.py`, and in a `/home/...` path inside
  `data/_pre_pattern123_backup/combined_report.json`. Removing it means
  rewriting history (new commit hashes), so it is run by the owner, with the
  pending work committed first.
- **Another person's tax data is stored in this project.** The documents in
  `data/raw_pdfs/` described above (`26AS.pdf`, `Computation_of_Income.pdf`,
  `ITR_V.PDF`, `AIS.pdf`) are an unrelated individual's. They are gitignored,
  but that individual's PAN is also in tracked files:
  `scripts/adapt_tax_docs_to_schema.py` and `tests/test_tax_docs_adapter.py`
  (docstrings), `tests/test_rule_stress_injection.py` (a fixture value),
  `data/26AS__table_group_8.csv`, and the whole of
  `data/_pre_realtaxdata_fix_backup/`. Not changed in this addendum;
  resolved in section 34 (Addendum 23).

### 26.3 FYI, left alone

- `data/_pre_pattern123_backup/` (tracked, ~5 MB of superseded outputs)
  includes a ledger CSV that `.gitignore` otherwise excludes.
- `.gitignore` excludes the FY2024-25 large generated CSVs but not their
  FY2025-26 equivalents, so those are tracked.
- `scripts/pdf_to_csv_converter.py` imports `pytesseract` / `pdf2image`
  lazily inside its OCR fallback (with a clear error message), so they are
  correctly optional and are not in `requirements.txt`.

### 26.4 Verification

`compileall` + `ast.parse` clean on every `.py`. Full suite on the
on-device files: **before 452 passed / 1 failed; after 458 passed / 0
failed** (360 s). Dashboard code untouched this round. Whole-tree grep for
the AI-mention rule over working files and all commit messages: working
tree clean after the `prepare_input.py` fix; the three trailers in 26.2
remain in history until the cleanup there is run.

Files touched: `tests/test_classify_document.py`,
`scripts/prepare_input.py`, `scripts/adapt_tax_docs_to_schema.py`,
`README.md`, `BASELINE_CHECKPOINT_V3.14.md`.

## 27. Addendum 16: uploads now reach Audit and Tax & Compliance; load-time feedback; early uploads; pending items closed (2026-10-07)

**Reported:** (a) after uploading documents, Audit stayed empty ("Documents 16,
Processed 2") and Tax & Compliance did not change; (b) uploading
`ledgers_redacted.pdf` only said "Document replaced" with nothing processed;
(c) the dashboard still took long to load, with no indication whether to wait;
(d) what happens if a file is uploaded before the dashboard has loaded.

**Root causes (each measured before it was changed):**
1. *Upload only saved the file.* Nothing classified or processed it; ledgers
   needed a separate Process click, and 26AS / ITR / trial-balance / GSTR
   uploads were never picked up at all because the tax and GST pipelines read
   fixed, established filenames.
2. *Same-name upload replaced an established document.* `ledgers_redacted.pdf`
   (a baseline Audit deliberately hides) was overwritten, so the "result" was
   a document Audit excludes by design -- hence an empty Audit.
3. *Load time = classifying 11 PDFs (~7s) on every cold page load*, because the
   cache lived only in memory; analysis (~27s, 18.5s of it GST extraction) ran
   inside the Streamlit script, which Streamlit aborts on any click.

**What changed (all additive; `combined_report.json` pipeline, baseline CSVs and
every earlier script untouched):**
- `scripts/source_resolver.py` (new): on-disk classification cache
  (`output/classification_cache.json`, keyed filename + mtime_ns + size, saved
  per file) plus content-based source selection -- 26AS paired only with the
  Computation of Income and ITR-V of the **same assessment year**; trial
  balance matched to the ledger whose dates lie inside the trial balance's own
  period; every GSTR file used.
- `scripts/tax_compliance_report.py` (new): writes
  `output/tax_compliance_report.json` with statuses ok / incomplete / none /
  error and the exact files used. Real data reproduces the established numbers
  (1 deductor, 32,018 claimed = reported, 82 clean accounts, 0 mismatched).
- `scripts/adapt_gstr_to_schema.py`: added `adapt_gstr_documents()` (content-
  based, per-file extract cache, one unreadable file becomes a warning instead
  of aborting the rest). `adapt_gstr_docs()` unchanged.
- `dashboard/jobs.py` + `dashboard/ingest.py` (new): one background worker
  thread; uploads are classified by content and routed (ledger -> full
  processing; 26AS/ITR/TB/GSTR -> analysis; else "not recognised" with the
  reason). Work no longer runs in the script, so clicking, refreshing, or a
  second upload cannot abort it. Duplicate requests coalesce.
- `dashboard/data.py`: `save_upload()` never overwrites an established document
  (`name_2.pdf`, analysed as its own document); per-document report labels so
  two ledgers for the same period cannot share a report file;
  `run_full_analysis(on_step=...)`; `document_usage()`; tolerant `_load_json`;
  `sort_gst_periods()` (the GST range text had been showing alphabetical
  month order).
- `dashboard/app.py`: real progress bar while documents are being read;
  "Loading..." placeholders in Audit and the sidebar instead of blank areas; a
  live job card (running stage, queue position); toasts on completion; Tax tab
  rebuilt from the new report with explicit incomplete / none / error states;
  automatic first analysis when analysable documents exist but no results do;
  Re-run Analysis runs in the background and disables while one is queued.

**Measured (cloud copy, same 11 documents):** cold scan ~7s once, then 0.0s
from the disk cache; warm page load 1.3-2.6s (first-ever load ~16s including
the one-time analysis); `run_full_analysis` 27s -> cached GST step on later
runs. A file uploaded while the page is still loading is queued ("Waiting in
line") and processed in order; verified live with three uploads queued behind a
running job, none lost.

**Tests:** 458 -> 515, all passing. New: `test_source_resolver.py`,
`test_tax_compliance_report.py`, `test_jobs.py` (jobs + ingest, including the
uploads-while-busy case), `test_upload_flow.py`.

**Known limits, stated deliberately:**
- **Ledger vs GSTR-1** still compares only the FY2025-26 baseline ledger (it
  depends on that ledger's account structure); the tab says so.
- Audit still excludes the two established baseline ledgers by the earlier
  explicit decision; an uploaded renamed copy appears as its own entry.
- A trial balance is reconciled only against a processed ledger covering its
  own period; none -> "incomplete: no matching ledger", never a wrong-year
  comparison.
- Job state is in server memory: a server restart drops queued jobs (the PDFs
  stay on disk and the Process tab still offers them).
- **Status of Addendum 15 section 26.2 (superseding it):** the uncommitted
  `reconstruct_ledger_entries.py` changes were committed, and the history
  rewrite that removed the assistant's name from past commits was run and
  verified clean, so the commit hashes quoted there no longer exist. The one
  item then still open, a different taxpayer's PAN / tax data in tracked
  files, is resolved in section 34 (Addendum 23).

Files touched: `scripts/source_resolver.py`, `scripts/tax_compliance_report.py`,
`scripts/adapt_gstr_to_schema.py`, `dashboard/jobs.py`, `dashboard/ingest.py`,
`dashboard/data.py`, `dashboard/app.py`, four new test files, `README.md`,
`BASELINE_CHECKPOINT_V3.14.md`.

## 28. Addendum 17: round-number flags no longer rank HIGH/CRITICAL on their own (2026-10-07)

**Question raised:** a plain ₹3,00,000 credit was flagged HIGH; "isn't it flagging too much?"

**Measured before changing anything** (real ledgers, `combined_report` / `combined_report_FY2025-26`):

| | FY2024-25 | FY2025-26 |
|---|---|---|
| Flagged legs | 1,271 | 1,336 |
| Flagged only for being round | 1,056 (83%) | 1,141 (85%) |
| ...of which CRITICAL/HIGH | 939 | 959 |
| Round legs as share of legs ≥ ₹10,000 | 12% | 14% |
| Distinct vouchers behind the round legs | 539 | 596 |

The rule's premise (organic amounts almost never land on a round ₹10,000) holds
for GST invoices but not for bank payments/receipts, where round amounts are
deliberate (top accounts: the bank account, the petrol vendor; most common
amounts ₹1,00,000 and ₹30,00,000). Severity was set from the amount alone, so
size, not suspicion, produced the ranking.

**Decision (owner, with advice from a practising CA):** keep ₹10,000 as the
screening threshold -- it is a meaningful figure in Indian practice (e.g. the
Section 40A(3) cash-payment limit) -- but stop treating "round" alone as HIGH.

**Change (one place, `scripts/combined_report.py`):** `rule_round_number()` now
returns severity `ROUND_NUMBER_ALONE_SEVERITY = "LOW"`. Detection is untouched
(same legs flagged, total 1,271 unchanged). A leg's severity is the highest of
its reasons, so when another independent rule fires on the same leg (duplicate
transaction, segregation of duties) the amount-based severity of that rule
applies and the leg is ranked as before. Checked case: `Receipt_20 (leg 2)`,
₹1,00,000, round **and** a duplicate of `Receipt_19` -- still HIGH.

**Effect (FY2024-25):** CRITICAL 360 -> 18, HIGH 619 -> 22, MEDIUM 158 -> 41,
LOW 134 -> 1,190. **FY2025-26:** CRITICAL 421 -> 38, HIGH 602 -> 26, MEDIUM
229 -> 47, LOW 84 -> 1,225. The dashboard's top-10 priority list is now
made up of corroborated findings (e.g. two ₹25,00,000 payments on the same day,
`Payment_2` / `Payment_3`).

**Deliberately not done:** ML anomaly flags were *not* used as corroboration:
68% of round-only legs (723 of 1,056) sit in a voucher the model also flagged,
because both react to large amounts -- that is not independent evidence.

**Not built, noted as a possible next step:** the Section 40A(3) test itself
(cash payments above ₹10,000 to one person in one day). A trial on the real
ledgers, excluding bank/wallet/Contra entries, found 37 (FY2024-25) and 36
(FY2025-26) party-days -- mostly pooled salary accounts, which hide the
individual payee, so the result would be review candidates, not findings. Held
back to keep the rule set unchanged for now.

**Found while verifying on the device (same day):** after the restart the baseline
reports updated, but a ledger the person had *processed* (Audit picker entry
`FY2024-25`, report `combined_report_FY2024-25_ledgers_redacted_2.json`) still
showed the old severities, because **Re-run Analysis refreshed only the two
established documents** -- a rule change never reached processed ledgers until
the PDF was fully re-processed. Fixed in `dashboard/data.py`:
`refresh_processed_ledger_reports()` re-derives each processed ledger's report
from its saved CSV (seconds, no PDF work), run inside the existing
"Ledger analysis refresh" step (step names unchanged). One failing ledger does
not stop the others. Verified on a copy of the real ledger: counts came out
LOW 1,190 / MEDIUM 41 / HIGH 22 / CRITICAL 18. Also note for the future: the
dashboard imports the rule code once per server process, so a code change needs
a dashboard restart to take effect.

**Tests:** `test_severity_distribution_pinned` re-pinned (LOW 1190 / MEDIUM 41 /
HIGH 22 / CRITICAL 18); 4 new tests in `test_combined_report_rules.py`
(round alone is LOW at any amount, threshold boundaries unchanged, a round+
duplicate leg keeps HIGH, all round-only legs are LOW). Saved reports need
**Re-run Analysis** to pick up the new severities.

Files touched: `scripts/combined_report.py`, `dashboard/data.py`, `tests/test_upload_flow.py`, `tests/test_rule_stress_injection.py`, `tests/test_combined_report_regression.py`,
`tests/test_combined_report_rules.py`, `README.md`, `BASELINE_CHECKPOINT_V3.14.md`.

## 29. Addendum 18: plain-language wording throughout the dashboard (2026-10-07)

**Raised:** the dashboard used technical terms a normal user cannot read. Agreed:
this matters because the product is meant for any company's owner or
accountant, not for people who know the statistics behind the checks.

**Scope (display only -- no calculation, flag, report file or test number
changed):**
1. *Headings and one-line explanations.* Each check now has a plain heading and
   a sentence on what it means: "What to look at first", "How serious are the
   findings?", "All findings", "Digit pattern check (Benford's Law)",
   "Missing voucher numbers", "Unusual entries (found by pattern analysis)",
   "Income-tax return vs Form 26AS", "Trial balance vs ledger", "GST returns:
   GSTR-1 (sales filed) vs GSTR-3B (tax paid)", "Ledger sales vs GSTR-1". The
   `Rule N` numbers no longer appear anywhere on screen.
2. *Readable columns.* `vch_type`, `group_min`, `group_max`, `anomaly_rank_pct`
   etc. became "Voucher type", "First number", "Last number", "Unusualness
   (top %)"; the raw anomaly score moved to a collapsed box.
3. *Counts.* "Ledger legs", "Flag-rows", "Signals" became "Ledger lines
   checked", "Findings", "Checks flagging this line". Check names: "Round
   amount", "Possible duplicate", "Same person entered and approved".
4. *Statistics kept, but tucked away.* Chi-square and MAD (Nigrini) are no
   longer the headline: the page shows one plain sentence plus a "strict test"
   and a "practical test (used for the overall result)" card, with the exact figures under
   "Technical details". Likewise the Isolation Forest scores.
5. *Internals removed from view.* The six-stage "Pipeline" checklist (it only
   tested whether the sample files exist, so it was always ticked) was replaced
   by a short "Analysis" panel; the list of `output/*.json` file names, the
   "reconstruction"/"baseline"/"frozen" wording, and file names in captions
   were removed. Timestamps now read "7 Oct 2026, 6:31 pm".

**How:** one new module, `dashboard/plain.py` (no Streamlit import, unit-tested
in `tests/test_plain.py`), turns the engine's phrases into plain text at display
time -- e.g. "Suspiciously round amount (100000.0)" -> "Round amount (Rs.100,000)
-- an exact multiple of Rs.10,000". The stored reports keep their original
text, so nothing that reads them changes. `dashboard/ingest.py` uses the same
module for the live "step 2 of 4" wording; `dashboard/app.py` applies it.
Verified by rendering every tab in a browser; no leftover technical terms in the
page text.

**Tone pass (same day):** the first plain-language wording read slightly too
casual for an audit tool ("the one we go by", "entries to glance at", "your
amounts"). Reworded to a professional-but-plain register: no first-person "we",
no "your" in result sentences, "expected pattern" instead of "natural/usual
pattern", "Marginal deviation" / "Significant deviation" for the practical-test
bands, "worth reviewing" / "worth following up" for suggested actions. Wording
only; `tests/test_plain.py` assertions updated to match.

**Noticed, not changed:** the duplicate-transaction check also flags Round
Off/Discount postings of a few paise (e.g. Rs.0.01-0.23); worth measuring the
same way as the round-number and petrol-vendor cases before deciding.

Files touched: `dashboard/plain.py` (new), `dashboard/app.py`,
`dashboard/ingest.py`, `tests/test_plain.py` (new), `README.md`,
`BASELINE_CHECKPOINT_V3.14.md`.

## 30. Addendum 19: limits reviewed; ledger-vs-GSTR-1 generalised; unreadable-file and coverage messages (2026-10-07)

**Raised:** six stated limitations. Each was checked against the code before
anything was changed, and sorted into real problem / inherent limit, small / large.

| # | Stated limit | Verdict | Action |
|---|---|---|---|
| 1, 4 | Ledger-vs-GSTR-1 reads only the FY2025-26 sample, so "any company" is incomplete | **Real, large** (one defect, stated twice) | Generalised (below) |
| 2, 5 | Accuracy depends on readable, supported PDFs | **Inherent**, but one part was a real defect: a scan or corrupt file was only ever answered with "not recognised" | Small fix: the reason is now stated and what to do about it |
| 3 | Only the supported document types and checks | **Inherent scope** | Stated up front in the dashboard (coverage note) |
| 6 | No finding is not proof of clean records | **True of every audit tool**; the risk is a reader over-trusting a green tick | Coverage note says so; one over-claiming caption softened |

**Ledger-vs-GSTR-1, what was actually wrong** (`scripts/rule_ledger_gstr_reconciliation.py`,
`dashboard/data.py`, the tab in `dashboard/app.py`):
1. Input files fixed: the FY2025-26 sample ledger and `data/gstr1_summary.csv`.
2. Account names fixed: exactly "CGST"/"SGST"/"IGST", "Sales", "Round Off/Discount".
3. Every ledger month with no GSTR-1 was flagged CRITICAL "no filing on file" --
   fine for the sample, but for an uploaded ledger it would turn "I have not
   uploaded that return" into a CRITICAL accusation of non-filing.
4. `data/gstr1_summary.csv` merges every GSTR-1 on file, built-in and uploaded,
   so an uploaded business's ledger could have been compared with the sample
   business's returns.

**What changed:**
- Tax accounts are recognised by name pattern (`tax_head()`): "CGST", "Output
  CGST @9%", "Central Tax", "UTGST", "Integrated Tax" -- never input credit,
  payable, claimed, interest or late-fee accounts. Rounding by `is_rounding_account()`.
- Non-GST sales = credit side of a SALES-TYPE voucher (type contains "sale",
  not purchase/journal/payment/contra) with no tax line, rounding excluded.
  A name test ("contains 'sale'") was tried and rejected: it would have swept
  in the sample's "Paytm Sale" collection account (534 Receipt legs).
- A month with no GSTR-1 is flagged only when it lies between the first and
  last return supplied. No returns supplied, or months outside that span, are
  "not compared". Status per ledger: ok / no_returns / no_overlap / no_sales,
  each explained in the tab.
- Pairing by origin (`gstr1_paths_by_origin`): built-in = names in
  `PROTECTED_UPLOAD_NAMES`. Sample ledger <-> built-in returns
  (`ledger_gstr_reconciliation_report.json`, as before); each processed ledger
  <-> returns the person uploaded (`ledger_gstr__<slug>.json`). A ledger with
  no uploaded returns says so instead of borrowing the sample's.
- `adapt_gstr_to_schema.extract_gstr1_rows()` reads exactly the given PDFs
  (same extractors and cache; writes nothing); `adapt_gstr_documents` now uses
  the same shared helper with unchanged behaviour.
- The tab has a ledger picker when more than one report exists, lists what was
  compared and against which files, which months were not covered, any
  unreadable return, and (Technical details) the accounts counted as non-GST
  sales. The "Limitation" sentence is gone. GST finding codes now display as
  readable names in both GST tables.

**Regression evidence:** on the FY2025-26 sample the new derivation is
identical to the old one (taxable supply and non-GST supply dictionaries
compare equal; 12 months compared; zero findings). End to end, an uploaded
copy of that ledger with one month's sales raised by Rs.5,000, paired with an
uploaded copy of the returns under a different file name, produced exactly one
finding (May 2025-26, taxable value, MEDIUM) and no finding against the
sample returns.

**Behaviour deliberately changed, with the tests that pinned the old behaviour
updated:** "no GSTR-1 at all" used to produce a CRITICAL per ledger month (now
nothing is flagged and the status is `no_returns`); the old test that only the
account literally named "Sales" counts as non-GST revenue became a test that
Receipt/Payment-voucher "sale"-named accounts do not count.

**Unreadable and unsupported files:** `classify_document()` returns an extra
field `issue` ("scanned" = opens but has no selectable text; "unreadable" =
would not open) next to `evidence`. The upload result, the Document Library
status and the uploader caption now say what to do (use the digital PDF /
upload a fresh copy). Cached "unknown" results from before this field are
classified once more so existing scans get the clear message.

**Coverage note:** an expander under the title ("What this dashboard checks --
and what it does not") lists the readable document types, every check, and
what a result means: a finding is something to review, "no findings" is not
an assurance, and purchases/ITC, bank reconciliation and TDS returns are not
examined.

**Known limits, not fixed (stated in the README):**
- The comparison assumes the returns belong to the same business as the ledger.
  The redacted documents carry no GSTIN or name, so this cannot be checked.
- Tax accounts are recognised by name; an unusual naming would be missed and
  the tab would then say nothing could be compared. A single-leg orphan voucher
  of a sales type (seen once in the FY2024-25 baseline, Rs.6,800) would be
  counted as non-GST sales; the accounts counted are listed so it is visible.
- Noticed, then fixed in the next addendum (section 31): the GST returns check
  (GSTR-1 vs GSTR-3B) merged every GSTR-1/3B on file, built-in and uploaded.

**Tests:** 588 collected (583 pass here; 5 skip here because they need the
real documents that are only on the developer's machine). New or updated:
`test_rule_ledger_gstr_reconciliation.py` (+33), `test_upload_flow.py` (+9),
`test_classify_document.py` (+3), `test_source_resolver.py` (+1),
`test_jobs.py` (+1), `test_plain.py` (+3).

Files touched: `scripts/rule_ledger_gstr_reconciliation.py`,
`scripts/adapt_gstr_to_schema.py`, `scripts/classify_document.py`,
`scripts/source_resolver.py`, `dashboard/data.py`, `dashboard/ingest.py`,
`dashboard/plain.py`, `dashboard/app.py`, the six test files above,
`README.md`, `BASELINE_CHECKPOINT_V3.14.md`.

## 31. Addendum 20: GSTR-1 vs GSTR-3B pairs by origin; "Not compared" instead of a filing failure (2026-10-07)

**Raised:** the section 30 note that the GST returns check merged every
GSTR-1 and GSTR-3B on file. Checked before changing: `adapt_gstr_documents()`
wrote one pair of CSVs (`data/gstr1_summary.csv`, `data/gstr3b_summary.csv`)
from every document, built-in and uploaded, and the rule kept the first copy
of each month. So a newly uploaded company's return for a month the sample
already had would be silently replaced by the sample company's, or two
different businesses' returns would be compared with each other. A real
correctness defect for exactly the case that matters (a new company), not a
cosmetic one.

**What changed:**
- `dashboard/data.py`: `gstr_paths_by_origin()` (built-in = names in
  `PROTECTED_UPLOAD_NAMES`, as for ledger-vs-GSTR-1). `run_gstr_pipeline()` now
  makes up to two separate reports: sample GSTR-1 with sample GSTR-3B
  (`output/gstr_report.json`, exactly as before, and the only place the
  `data/gstr*_summary.csv` files are written), and uploaded GSTR-1 with
  uploaded GSTR-3B (`output/gstr_report__uploaded.json`, read straight from the
  PDFs by the new `adapt_gstr_to_schema.extract_gstr3b_rows()`, no CSVs).
  Nothing crosses over: an uploaded return is never compared with a sample
  one, and a sample one never stands in for an uploaded one.
- `scripts/rule_gstr_reconciliation.py`: `pairing()` lists which periods were
  compared and which exist in only one of the two returns. For the uploaded
  report a month with one return but not the other is not a finding
  (`missing_is_filing_gap=False`): it is listed as not compared. The sample
  report keeps the original behaviour. Each report carries a status: ok,
  no_gstr1, no_gstr3b or no_overlap.
- `dashboard/plain.py`: `gst_status_message()` and `gst_not_compared_notes()`;
  every such message begins "Not compared" and never uses filing-failure
  wording.
- `dashboard/app.py` (GST returns tab): a "Returns" picker when both reports
  exist; "GSTR-1 used" and "GSTR-3B used" list the exact documents; "Compared
  N months: first to last"; the months not compared and why; unreadable files;
  the verdict ("every month agrees") only when something was actually
  compared. Document Library says whether a return was used as a sample
  return or as one of your uploaded returns. The sidebar shows the newest
  report.

**Regression evidence:** the sample report is unchanged -- 16 periods compared,
zero findings, the same as before; the 21 existing rule tests pass without any
edit. End to end with copies of the sample PDFs uploaded under other names, the
dashboard showed two reports ("Sample returns", "Your uploaded returns"), the
uploaded one naming its two documents and 16 months compared, and
`gstr_report.json` unchanged.

**New tests (16):** `test_rule_gstr_reconciliation.py` (+6: pairing, not-compared
months not flagged, the sample behaviour still flagging, statuses, the report
file and its fields); `test_upload_flow.py` (+9, including the isolation test:
an uploaded company's GSTR-1 and GSTR-3B for April 2025-26, the same period the
sample has, with different amounts (5,000 against 1,000), are compared with
each other in the uploaded report -- one finding quoting the uploaded
figures -- while the sample report still reads only the sample documents and
stays clean; the uploaded figures are never replaced by the sample's); `test_plain.py` (+1, wording).

**Known limit, stated:** all uploaded returns are treated as one business. The
redacted documents carry no GSTIN or name, so uploads from two different
businesses at once would still be compared with each other.

**Tests:** 604 collected (599 pass here; 5 skip here because they
need the real documents that are only on the developer's machine).

Files touched: `scripts/rule_gstr_reconciliation.py`,
`scripts/adapt_gstr_to_schema.py`, `dashboard/data.py`, `dashboard/plain.py`,
`dashboard/app.py`, `tests/test_rule_gstr_reconciliation.py`,
`tests/test_upload_flow.py`, `tests/test_plain.py`, `README.md`,
`BASELINE_CHECKPOINT_V3.14.md`.

## 32. Addendum 21: rounding postings no longer flagged as duplicates (2026-10-07)

**Raised from the dashboard:** the Audit page's duplicate flags were mostly
Round Off/Discount postings of a few paise. Measured before changing anything,
on the FY2024-25 sample ledger: 215 legs carried a duplicate-transaction
signal; 129 of them (60%) were Round Off/Discount postings, every one 50
paise or less, 29 of them MEDIUM. They were 10% of all 1,271 findings. The
FY2025-26 ledger shows the same pattern (97 of 195).

**Why it is a real problem, not a matter of taste:** invoice rounding repeats
by nature -- the same Rs.0.20 on the same day from two invoices is how rounding
behaves -- so each of these flags was a false lead that sat among, and
diluted, the real candidates.

**Looked at and left alone:** the Hindustan Petroleum flags that lead the
CRITICAL list. They are 9 pairs of different invoice numbers with the same
amount to the paisa, each over Rs.10 lakh, on the same day. That is exactly
what two tankers of the same quantity at the same rate would produce, and it
is worth a reviewer confirming it; the CRITICAL ranking comes from the amount.
Suppressing it would be the "special-casing a counterparty" that the rule's
own notes decided against.

**What changed:**
- `scripts/rule_duplicate_transaction.py`: `_is_invoice_rounding(row)` -- a leg
  is skipped when its account is a rounding account (the same name-pattern
  recognition the GST comparison uses, `is_rounding_account()`) AND its amount
  is under Re.1 (`ROUNDING_CEILING`). No company or account literal. A posting
  of Re.1 or more to the same account (a real discount: this ledger has
  Rs.6,000 and Rs.9,322.13) is still checked, and so is a sub-rupee amount in
  any ordinary account.
- `scripts/combined_report.py`: the dashboard does not run the standalone
  module -- it runs this file's inline copy of Rule 5. The same exclusion was
  applied there by importing the one helper (not copying it), so the two
  cannot drift. A first run that changed only the standalone module left the
  dashboard numbers at 1,271; the real-ledger tests, which run the combined
  report, are what showed it.

**Effect on the FY2024-25 baseline (re-pinned, each number traced):**
total legs flagged 1,271 -> 1,142 (-129); LOW 1,190 -> 1,090 (-100); MEDIUM
41 -> 12 (-29); HIGH 22 and CRITICAL 18 unchanged; multi-signal unchanged at 8;
duplicate-signal legs 215 -> 86. The FY2025-26 ledger's figures (duplicate-signal
legs 195 -> 98) are produced at the next Re-run Analysis; no other pinned test moved.

**New tests (5)** in `tests/test_rule_duplicate_transaction.py`: sub-rupee
rounding is not a duplicate; recognised by pattern ("Rounding Off", "Round
off", "Rounded Off"); Re.1-or-more in the rounding account is still checked; a
sub-rupee amount in an ordinary account is still checked; the combined
report's inline rule agrees with the standalone module on rounding rows.
Re-pinned: `test_leg_flag_counts_pinned`, `test_severity_distribution_pinned`.

**To see it on the dashboard:** click Re-run Analysis; the stored report files
are regenerated then.

**Tests:** 609 collected (604 pass here; 5 skip here because they
need the real documents that are only on the developer's machine).

Files touched: `scripts/rule_duplicate_transaction.py`,
`scripts/combined_report.py`, `tests/test_rule_duplicate_transaction.py`,
`tests/test_combined_report_regression.py`, `README.md`,
`BASELINE_CHECKPOINT_V3.14.md`.

## 33. Addendum 22: the "analysis running" notice now says whose results are on screen (2026-10-07)

**Raised from the dashboard:** while a newly uploaded document was still being
scanned, the Audit page showed full results under a small grey line ("An
analysis is running in the background"). The question was whether it should be
blank instead.

**Checked first.** The numbers shown were real results (the FY2024-25 ledger,
labelled with its name and read time) -- not wrong, not partial. The weakness
was the notice: it did not say the new document is not in them, so a reader
who had just uploaded a ledger could take the old numbers for the new one's.
Blanking the page was considered and rejected: the results of every document
already processed would disappear for as long as any scan takes, including when
the new document is unrelated (a trial balance does not change the ledger
results).

**What changed:** `plain.results_pending_note()` builds the notice from the
running jobs: "Still reading <file name(s)>. The results below come from the
documents already processed and do not include it yet; they update
automatically when the analysis finishes." (several files: "a.pdf, b.pdf and N
more ... do not include them yet"). With no document being read (a re-run only)
it says the results are from the last completed run. It is now a visible
information box, not a grey caption, in Audit and in each Tax & Compliance
tab. Display only: no number, flag or report changes. Checked in the running
dashboard with a real upload.

**A second finding, from the same screenshot -- a real defect, fixed.** The
results on screen were labelled "document read on 3:16 pm", earlier than the
upload being scanned, and the file was one that had been uploaded, processed
and deleted before. A ledger's results are looked up by file NAME (the record
`output/ledger_meta__<name>.json`), and nothing removed that record when the
file was replaced or deleted. So:
- a file saved under a name used before showed the OLD file's results the
  moment it was uploaded, as if they were its own; and
- if reading the new file then failed (a failed run never overwrites the
  record), the old file's results stayed on screen under the new file's name
  for good. With the same name and different contents this would have been
  silently wrong numbers.

**Fix:** `data.save_upload()` now removes that results record whenever a file
is saved (`_forget_previous_results`), including a copy saved next to an
established document (`name_2.pdf`) and a name whose earlier file was deleted.
The established sample ledgers are skipped: their results do not come from
this record. Other documents' records are untouched. The page that follows is
the honest one: if other ledgers are already processed they stay on screen
under the notice above; if there are none, Audit says nothing is there yet and
which document is being read, instead of showing someone else's numbers or
telling the person to upload what they have just uploaded.

**Notice wording follows what is on screen:** `results_pending_note()` takes
`have_results`. With results below it says they do not include the document
being read; with nothing below it says "There is nothing to show yet; the
results will appear here when the analysis finishes", never "the results
below". Used in Audit, GST returns, Ledger vs GST and the tax tab, each with
its own "is there anything shown" test.

**Checked in the running dashboard:** (1) a ledger copy uploaded while another
ledger was already processed: the processed ledger stays, under "Still reading
ledgers_redacted_2.pdf ... do not include it yet"; (2) the same upload with a
stale results record left behind for that name and no other ledger: no old
numbers, "There is nothing to show yet" under the notice.

**Tests:** 618 collected (3 + 1 new in `tests/test_plain.py`; 5 new in
`tests/test_upload_flow.py`: replaced file loses its results record, a deleted
file's leftover record is not reused, other documents' records are left alone,
a copy saved alongside an established document starts with none, the sample
ledgers' records are untouched).

Files touched: `dashboard/plain.py`, `dashboard/app.py`, `dashboard/data.py`,
`tests/test_plain.py`, `tests/test_upload_flow.py`, `README.md`,
`BASELINE_CHECKPOINT_V3.14.md`.

## 34. Addendum 23: real personal data removed from tracked files before the repository goes public (2026-10-08)

**Why:** the project is to be shown publicly (private GitHub repository ->
public, for a portfolio). A public repository cannot hold another person's
PAN or a business's real counterparties. This round only touches comments,
docstrings, documentation, one test fixture and `.gitignore`; no detection
logic changed.

**Found in tracked files, and what was done:**
- A third party's PAN (the individual whose documents are described in
  section 26) appeared in `scripts/adapt_tax_docs_to_schema.py` and
  `tests/test_tax_docs_adapter.py` (docstrings), and as a fixture value in
  `tests/test_rule_stress_injection.py`. Docstrings reworded to say "a
  different taxpayer" without identifying anyone; the fixture now uses the
  dummy PAN `AAAPD1234C`, which no assertion depends on.
- Two employees' names in comments, docstrings and the V3.10 checkpoint
  (`reconstruct_ledger_entries.py`, `tests/test_combined_report_regression.py`,
  `tests/test_v3_9_name_pair_fixes.py`, `BASELINE_CHECKPOINT_V3.10.md`),
  replaced by "Employee A" / "Employee B" or a plain description.
- A real vendor's name in `rule_duplicate_transaction.py`, a test docstring,
  `README.md` and `BASELINE_CHECKPOINT_V3.4.md`, replaced by "a repeat
  vendor".
- The Windows user folder in four places (`C:\Users\<user>\...` now).
- `.gitignore`: `data/26AS__table_group_*.csv` (raw extraction fragments, one
  with the third party's name and PAN; nothing reads them) is now ignored
  instead of force-tracked, and `data/_pre_*_backup/` (local safety copies of
  real ledger/tax data) is ignored.

**Deliberately left, and why:** `_ACCOUNT_NAME_CANONICALIZATION` in
`scripts/reconstruct_ledger_entries.py` still contains the two employees'
salary account names as dictionary keys. They are working data: the mapping
only applies when the ledger text matches exactly, so a placeholder would
silently stop the fix. An alternative (loading such aliases from a git-ignored
local file) is possible but is a behaviour change, not part of this round.

**History:** old commits still contain everything above, so making the
existing repository public would expose it. The repository is therefore
republished from a single fresh commit (steps given to the owner), not
switched to public as it stands.

**Verification:** whole-tree grep (all tracked and untracked-not-ignored
files) for the PAN, both employee names, the vendor name, the third party's
name and the Windows user folder: no matches except the two dictionary keys
above. Full suite unchanged at 618.

Files touched: `scripts/adapt_tax_docs_to_schema.py`,
`scripts/reconstruct_ledger_entries.py`, `scripts/rule_duplicate_transaction.py`,
`scripts/adapt_ledger_to_schema.py`, `tests/test_tax_docs_adapter.py`,
`tests/test_rule_stress_injection.py`, `tests/test_combined_report_regression.py`,
`tests/test_v3_9_name_pair_fixes.py`, `README.md`, `.gitignore`,
`BASELINE_CHECKPOINT_V3.3.md`, `V3.4.md`, `V3.10.md`, `V3.14.md`.
