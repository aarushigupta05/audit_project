# BASELINE CHECKPOINT V3.12 — GSTR-1/GSTR-3B Multi-Month Parsing + Taxable-Value Fix

## 0. Relationship to V3.11

V3.11 closed out the FY2025-26 ledger diagnostic and added Rule 7
(voucher-sequence-gap). This checkpoint is a separate unit of work: the
same day's follow-up upload added two new PDFs —
`GSTR1_All_Months_Anonymised.pdf` and `GSTR3B_All_Months_Anonymised.pdf`
— each packing 16 months of filings (April 2025 – July 2026, spanning
FY2025-26 and FY2026-27) into one PDF per form, instead of one PDF per
month. `scripts/adapt_gstr_to_schema.py` previously only understood the
one-PDF-per-month shape (and had only ever seen one real month). This
checkpoint extends it to the multi-month shape, and in doing so surfaced
and fixed a real extraction gap that 15 months of data had been too
uniform to expose.

No ledger code changed in this checkpoint. No new dependency was added.

## 1. What changed, in one paragraph

`adapt_gstr_to_schema.py`'s field-extraction regex logic is unchanged and
reused as-is (`_extract_gstr1_fields` / `_extract_gstr3b_fields`) — what's
new is (a) a page-range segmentation step that slices a multi-month PDF
into one text block per month before handing each block to that same
extractor, (b) a merge-with-dedup step in `adapt_gstr_docs()` so the
16-month upload and the pre-existing one-off June-2026 PDF don't double-
count their one overlapping period, (c) a correctness fix to what
`taxable_value`/`igst`/`cgst`/`sgst`/`cess` are derived from, and (d) a
tolerance fix for a pdfplumber text-extraction quirk hit on 2 of the 16
real months. (c) and (d) are both real findings from the new data, not
preemptive hardening — see §3 and §4.

## 2. Segmentation: reusing the extractor, not duplicating it

Both All_Months PDFs repeat the same per-month page layout the
single-month PDFs already use; each new month's first page is identified
by a fixed marker (`"Tax period <Month>"` for GSTR-1, `"Year <YYYY-YY>"`
for GSTR-3B). `_segment_month_blocks()` finds every page matching the
marker and slices the page list into one join-the-pages-between-markers
block per month; `extract_gstr1_all_months()` / `extract_gstr3b_all_months()`
then call the *exact same* `_extract_gstr1_fields()` / `_extract_gstr3b_fields()`
used by the single-month path, once per block. Verified before writing
any of this: a full scan of every month in both PDFs confirmed all three
of GSTR-1's required regex anchors (Table 7, Table 8, Total Liability)
and GSTR-3B's three (Table 3.1(a), Table 3.1(e), Table 6.1 section) match
on all 16 months — this is a genuine reuse, not a parallel parser that
happens to look similar.

`adapt_gstr_docs()` now reads both shapes and merges them: All_Months
sources are read first, then any standalone single-month PDFs, then
`_dedup_by_period()` keeps the first-seen row for each
`(financial_year, period)` key and drops the rest with a printed note.
This matters concretely here: the pre-existing `gstr1_june_2026_redacted.pdf`
/ `gstr3b_june_2026_redacted.pdf` describe the *same* filing as the
"2026-27 June" block inside the All_Months PDFs (confirmed by identical
ARN dates, `06/07/2026` and `17/07/2026` respectively) — without the
dedup step this would have silently produced a 17th, duplicate row.

Result: `data/gstr1_summary.csv` and `data/gstr3b_summary.csv` now hold
16 rows each (previously 1), reproducibly (`adapt_gstr_docs()` run twice
in a row produces byte-identical output, pinned by
`test_adapt_gstr_docs_merges_and_dedups`).

## 3. Fix: `taxable_value` was Table 7 (B2CS) alone — now the filing's own "Total Liability" total

### The finding

On 15 of the 16 real months, GSTR-1's Table 7 (B2CS — supplies to
unregistered persons) was this filer's *only* nonzero taxable-supply
table, so reporting Table 7 alone as `taxable_value` happened to equal
GSTR-3B's Table 3.1(a) every time — which is why this was never caught
with only one real month on file. June 2025-26 breaks that pattern: it
also has a nonzero Table 4 (B2B) entry (confirmed directly in the PDF:
`"Total 1 Invoice 76,271.18 0.00 6,864.41 6,864.41 0.00"`). Reporting
Table 7 alone for that month (Rs.90,813.99 / Rs.8,173.21 CGST+SGST)
under-counts GSTR-3B's Table 3.1(a) (Rs.167,085.17 / Rs.15,037.62) by
exactly the missing B2B amount — a real extraction gap, not a genuine
cross-filing discrepancy (confirmed: 90,813.99 + 76,271.18 =
167,085.17 to the rupee, and 8,173.21 + 6,864.41 = 15,037.62).

### Why not just also parse Table 4 directly

Table 4 has several sub-tables on this form (regular B2B, amended B2B,
SEZ with/without payment, deemed export, each repeated for "original"
and "amended"), each with its own "Total N Invoice" line — enumerating
and summing all of them by name is exactly the kind of per-case,
easy-to-miss-a-variant parsing this project's "never guess" discipline
avoids. The form already computes the number needed: the "Total
Liability (Outward supplies other than Reverse charge)" line sums every
taxable-supply table for the period. The one complication is that its
*value* column, unlike its tax columns, also bundles in Table 8's
non-GST value — confirmed by exact arithmetic: Total Liability value
(Rs.3,39,91,456.83) minus Table 8 non-GST value (Rs.3,38,24,371.66)
equals GSTR-3B's taxable value (Rs.167,085.17) to the rupee, on June
2025-26 and (trivially, since non-GST tax contribution is always zero)
on all other 15 months too.

### The fix

`taxable_value = Total Liability value − Table 8 non-GST value`;
`igst`/`cgst`/`sgst`/`cess` are now the Total Liability line's tax
columns directly (no adjustment needed — non-GST supplies carry no GST).
Table 7's raw B2CS-only figures are kept, renamed to `b2cs_taxable_value`
/ `b2cs_igst` / `b2cs_cgst` / `b2cs_sgst` / `b2cs_cess`, for anyone who
wants that narrower breakdown later.

### Verification

Computed both ways for all 16 real months before accepting the change:
identical to the rupee on the 15 months that already agreed (zero
regression — pinned in `test_extract_gstr1_real_figures`, which still
checks the single real month this project's suite has always pinned),
and correct (matching GSTR-3B) on the 16th (pinned in
`test_extract_gstr1_b2b_month`). `rule_gstr_reconciliation.py` needed no
code change — reconciliation now genuinely passes (0 flags across 16
months) instead of needing a special case for one month.

## 4. Fix: stray-letter pdfplumber artifact in GSTR-3B Table 6.1

### The finding

`_extract_payment_row()`'s regex failed to match the "Integrated tax" row
on exactly 2 of the 16 real GSTR-3B months (April 2025-26, July
2026-27). Direct inspection of the extracted text showed the cause:
pdfplumber occasionally glues a single stray capital letter onto an
all-zero cell with no surrounding whitespace at all — e.g.
`"Integrated 0.00 0.00 0.00L0.00 0.00 0.00 - 0.00 0.00 -"` instead of the
normal `"... 0.00 0.00 0.00 0.00 ..."`. Scanned for the same pattern
across every other captured line in both forms (Table 7, Table 8, Table
3.1(a)/(e), Total Liability) on all 16 months — it never appears
anywhere else that matters to this adapter; the only other place it
showed up was on an unrelated, unparsed row (GSTR-3B's "(d) Inward
supplies liable to reverse charge" line), confirming this is a sporadic
rendering quirk, not a structural issue with this one row.

### The fix

`_extract_payment_row()`'s column separator (`_COL_SEP`) now matches
either ordinary whitespace or a single stray letter with no whitespace —
scoped to this one function (the only place the quirk was found to
matter), not loosened anywhere else in the module.

### Verification

Both previously-failing months now extract correctly, with Integrated
tax read as exactly zero (the true value) — pinned in
`test_extract_gstr3b_tolerates_stray_letter_artifact`. All 16 months'
GSTR-3B payment-completeness checks (Table 6.1, part of
`check_gstr_reconciliation()`) now run without error, where 2 of 16
previously would have raised `ValueError` and aborted the whole adapter
run.

## 5. Net effect on `rule_gstr_reconciliation.py`

Before this checkpoint: 1 real period on file, 0 flags (the only
possible outcome with a single matching month — not a meaningful test
of the rule's cross-check logic in anger). After: 16 real periods, 0
flags — now a genuine 16-month clean bill of health, achieved by fixing
real extraction bugs rather than by the rule's logic changing. The
rule's own docstring limitation note is updated to reflect this (still
no ledger-period overlap to cross-check against — that remains future
work, see §7).

## 6. Tests

`tests/test_adapt_gstr_to_schema.py` — rewritten (the old version's
`test_exactly_one_real_filing_pair_on_file` explicitly said to update it,
not treat a count change as a regression, once a second month arrived).
Now covers: the original single-month path (unchanged real-figure
pins), both All_Months extractors finding all 16 months with no
collapsed/duplicate periods, the B2B month's fixed figures, the
stray-letter tolerance on both affected months, all 16 months
reconciling cleanly end-to-end, the merge+dedup behavior reproducing
the committed CSVs exactly, `_dedup_by_period`'s first-seen-wins
behavior in isolation, and `_segment_month_blocks`'s marker-matching and
no-marker-found error path. 15 tests total (was 4).

`tests/test_rule_gstr_reconciliation.py` — one stale hardcoded assertion
updated (`test_run_gstr_report_shape_and_real_data` expected exactly 1
period; now checks for 16, including both a FY2025-26 and FY2026-27
period by name, without hardcoding the full list).

Full suite: **339 tests, 339 passing** (331 from V3.11 + 8 net new GSTR
tests).

## 7. What this does NOT do (scope boundary, read before extending)

- **No ledger-vs-GSTR cross-check exists.** `rule_gstr_reconciliation.py`
  only ever compares GSTR-1 against GSTR-3B for the same period — it has
  never read `general_ledger.csv`, despite an old docstring comment that
  implied otherwise. FY2025-26's GSTR data and the FY2024-25 ledger still
  don't share a period, so this remains correctly unbuilt, not merely
  "unblocked." Building it is real future work (a new rule, not a
  reuse of this one), to be done once an overlapping period exists to
  verify it against.
- **No Tally structured-data (XML/ODBC/CSV export) adapter was written
  here.** See the schema-boundary note below — this was a deliberate
  choice, not an oversight.

## 8. Tally structured-data readiness (no new code — a documented contract instead)

The project's existing architecture already separates concerns exactly
the way this question needs: `general_ledger.csv` is a plain, source-
agnostic schema (`entry_id, vch_type, vch_no, date, particulars,
dr_cr, amount, ...`) that every `rule_*.py` and `combined_report.py`
consumes — none of them know or care that the current adapter
(`reconstruct_ledger_entries.py` → `adapt_ledger_to_schema.py`) happens
to read a PDF. A Tally XML/ODBC/CSV export would be handled by writing a
*new* adapter that reads that export and produces the same
`general_ledger.csv` columns — zero changes needed anywhere downstream.

Why no such adapter was written this round: every file received so far
(`Ledgers_Anonymised.pdf`, the GSTR PDFs) is a PDF, including the ones
described as coming "from dad" — there is no actual Tally XML/ODBC/CSV
sample in hand to build or verify a parser against. Writing one on
spec, with nothing real to check it against, would be exactly the kind
of guessed, unverifiable code this project's whole discipline (quantify
before fixing, verify zero regression, never guess) exists to avoid —
and it would add real weight (a new parser, a new dependency surface)
for a format that may turn out to differ from assumptions in ways only
a real file would reveal. The moment an actual Tally export (XML,
ODBC-queried table, or Tally's own CSV export) is available, building
`adapt_tally_export_to_schema.py` against it is a contained, well-scoped
piece of work — same pattern as every adapter already in this project —
and nothing else in the detection pipeline needs to change to use it.

## 9. Preserved architecture & invariants

- Every extraction regex from before this checkpoint is byte-for-byte
  unchanged except `_COL_SEP` (§4) — no fuzzy matching introduced.
- `_extract_gstr1_fields` / `_extract_gstr3b_fields` are now the single
  source of truth for both the single-month and multi-month paths; the
  only thing that differs between them is how `text` is obtained
  (`_page_texts` + `_segment_month_blocks` vs. reading a whole PDF).
- `_dedup_by_period`'s first-seen-wins ordering is deliberate (All_Months
  processed before single-month globs) and documented inline, not an
  incidental consequence of glob order.

---

## 10. Follow-on: the FY2025-26 ledger is now in the detection pipeline too

Same session, next step on the "what next" roadmap: get the V3.11-diagnosed
FY2025-26 ledger (`data/Ledgers_Anonymised_reconstructed_entries.csv`,
13,981 rows, clean per V3.11) actually running through the same rules the
FY2024-25 ledger gets, not just sitting diagnosed-but-unused.

**Schema step (no new code):** `adapt_ledger_to_schema.py`'s `adapt_ledger()`
was already parametrized by `input_path`/`output_path` — ran it as-is
against the FY2025-26 entries to produce `data/general_ledger_FY2025-26.csv`
(13,981 rows, same schema as `general_ledger.csv`).

**Why NOT just point `run_combined_report()` at the new file:**
`run_combined_report()` also runs Rule 4 (ITR vs Form 26AS) and Rule 6
(trial balance vs ledger) — both need their OWN matching-FY source
document (`data/form26as.csv`/`itr_summary.csv`, `trial_balance_redacted.pdf`)
to compare against, and neither exists for FY2025-26 yet (no FY2025-26
trial balance or ITR/26AS PDF has been provided). Silently running those
two rules against the FY2024-25 copies of those documents would compare
the new ledger against the WRONG year's tax/trial-balance data — wrong in
a way that's easy to miss, since both rules would still run and print
something. Never did that.

**The fix: split, don't duplicate.** `combined_report.py` is refactored so
the five rules that only ever needed `general_ledger.csv` itself (1
segregation-of-duties, 2 round-number bias, 3 Benford's Law, 5 duplicate
transactions, 7 voucher-sequence-gap) now live in one function,
`compute_ledger_intrinsic_flags(gl_rows)`, callable on ANY ledger in this
schema. `run_combined_report()` calls it and then layers Rules 4/6 on top
exactly as before — verified byte-identical output (full JSON diff
against the previously-committed `output/combined_report.json`, differing
only in the `generated_at` timestamp) and the full existing test suite
(339 tests) still passing unmodified. A new `run_ledger_only_report(gl_file,
report_label)` entry point calls the same shared function for a SEPARATE
ledger, under a report-label-suffixed output filename
(`output/combined_report_FY2025-26.json`, etc.) so it can never collide
with or overwrite the FY2024-25 report.

**Real result, FY2025-26 ledger (Rules 1/2/3/5/7 only):**
- Benford's Law: chi-square 176.06 (deviates, same as FY2024-25 — large-n
  threshold miscalibration, not a new finding), MAD 0.01026 ("Acceptable
  conformity", also consistent with FY2024-25).
- Rule 7 (sequence gaps): 74 missing numbers across the same 5 series that
  had gaps in FY2024-25 (Journal, Payment, Receipt, Sales Cash, Sales GST)
  — Purchase's vendor invoice numbers correctly excluded again by the
  digit-length heuristic, with zero vch_type-name hardcoding needed for
  this second, independent ledger. This is exactly the generalization the
  heuristic was designed for (see V3.11 §7) — confirmed working on a
  second real dataset, not just the one it was built against.
- 1,337 legs flagged (Rules 1/2/5 combined, 8 multi-signal) — not yet
  individually reviewed; this report exists so that review can start,
  the same way the FY2024-25 report has been available for review
  throughout this project.

**Dashboard + tests:** `dashboard/app.py`'s GSTR Status tab had a stale
"only one real month on file" info box (predates this session's 16-month
GSTR work) — updated to the current count and a more accurate note on why
it's still a separate tab (zero overlap with the FY2024-25 ledger shown
elsewhere — that's a different claim from "only one month exists" now).
`tests/test_dashboard_data.py`'s matching pinned assertion (periods ==
["June 2026-27"]) updated the same way the GSTR adapter's own tests were.
3 new tests (`tests/test_run_ledger_only_report.py`) pin: the shared
function's output matches `run_combined_report()`'s on the FY2024-25
ledger exactly; `run_ledger_only_report()`'s result shape and real figures
against the FY2025-26 ledger; and the same 5-series/Purchase-excluded
gap-detection pin as FY2024-25, now independently confirmed on FY2025-26.
Full suite: **342 tests, 342 passing**.

**What this does NOT do:** no Rule 4/6-equivalent check runs for
FY2025-26 — that needs a real FY2025-26 trial balance and/or ITR/26AS
document, which hasn't been provided. Nothing here merges or compares the
FY2024-25 and FY2025-26 ledgers against each other (e.g. a combined
Benford's Law run, or a unified voucher-number-gap view across both FYs)
— that's a real open design question (does a fresh FY's voucher numbering
restart make a merged view meaningful, or misleading?), deliberately left
as a decision point rather than guessed at here.

**Important discovery, not yet acted on:** the FY2025-26 ledger's date
range (1-Apr-25 to 9-Mar-26) overlaps 12 of the 16 GST months parsed in
§2 above (April 2025 - March 2026). This is the first time ANY ledger
data in this project has shared a period with filed GSTR-1/GSTR-3B data
— until now, `rule_gstr_reconciliation.py`'s "no ledger period to
cross-check against" limitation (see its docstring and §7 above) was
simply true. A genuine ledger-vs-GSTR reconciliation rule is now
buildable for real, for the first time — but is real, nontrivial new
work (it needs a careful, verified mapping from this company's own chart
of accounts to "outward taxable supply", not a guessed one) and was
deliberately not started without being asked for, rather than rushed in
the same pass as this checkpoint's other changes.

---

## 11. Follow-on: the ledger-vs-GSTR reconciliation rule now exists — and reconciles cleanly

Immediately following §10's discovery (12 months of real overlap between
the FY2025-26 ledger and the filed GSTR-1/GSTR-3B data), built the rule
that overlap makes possible: `scripts/rule_ledger_gstr_reconciliation.py`.

**Deriving "taxable supply" from the ledger without hardcoding account
names:** a chart of accounts is company-specific, so rather than hardcode
this company's actual GST-attracting revenue accounts ("Sales GST",
"Pollution Checking Charges" — confirmed by direct inspection), the rule
derives the taxable-revenue account set STRUCTURALLY: any credit leg
sharing a voucher with a CGST/SGST/IGST credit leg is, by definition,
part of that voucher's taxable supply. The only exclusion needed is
Tally's own standard "Journal" voucher type (used here for period-end
GST liability set-off entries — Input CGST/SGST, GST Payable — which are
bookkeeping, not sales); this was verified exhaustively before writing
any code, by listing every single credit account that ever co-occurs
with a tax leg across the whole ledger and confirming each one is either
real revenue or a Journal-type settlement entry, never left to guesswork.

**Result:** computed this way, the ledger's monthly taxable value, CGST
and SGST match `gstr1_summary.csv`'s figures EXACTLY (to the rupee) on
all 12 overlapping months (April 2025 - March 2026) — a strong, genuine
cross-validation: the FY2025-26 ledger reconstruction (V3.11) and the
GSTR-1 All_Months extraction (this checkpoint, §1-6) were each built and
verified independently, from two completely different source documents,
and agree with each other to the rupee on 12 separate months once put
side by side for the first time.

**What's deliberately NOT reconciled:** non-GST (fuel/petrol-diesel)
supply. The ledger's "Sales" account (the non-taxable fuel revenue
account, by the same Table-7/Table-8 mirror logic the GST-side already
uses) appears with BOTH debit and credit legs across different vouchers
in a pattern not yet understood — summed naively it is off from GSTR-1's
non_gst_value by roughly 5-8x, far too large to be rounding and not
something to force a match on without understanding why. This is flagged
as real future work in the rule's own module docstring, not papered over.

**Tests:** `tests/test_rule_ledger_gstr_reconciliation.py`, 14 tests —
the FY-boundary date logic (`_period_key`, including the Jan/Feb/Mar
"still last FY" edge case), the structural derivation (Journal exclusion,
multiple revenue accounts summed, Round-Off/Discount and tax-less
vouchers excluded), synthetic answer-keyed mismatch detection at each
severity tier, a ledger-period-with-no-filing check, a GSTR-period-with-
no-ledger-data non-check (coverage gap ≠ discrepancy), and the real data:
exactly 12 overlapping months derived, zero flags, and the export
wrapper's shape. Full suite: **356 tests, 356 passing**.

This rule is standalone (own `run_ledger_gstr_report()`, own
`output/ledger_gstr_reconciliation_report.json`) rather than merged into
`combined_report.py` or `run_ledger_only_report()` — same reasoning as
`rule_gstr_reconciliation.py` itself: it spans a set of periods, not the
single ledger-period shape the rest of that report assumes.

---

## 12. Files touched this checkpoint

`scripts/adapt_gstr_to_schema.py` (rewritten), `scripts/rule_gstr_reconciliation.py`
(docstring only), `scripts/combined_report.py` (refactored + new entry
point), `scripts/adapt_ledger_to_schema.py` (unchanged, just re-run with
new arguments), `scripts/rule_ledger_gstr_reconciliation.py` (new),
`dashboard/app.py` (stale text), `tests/test_adapt_gstr_to_schema.py`
(rewritten), `tests/test_rule_gstr_reconciliation.py` (one assertion),
`tests/test_dashboard_data.py` (one assertion), `tests/test_run_ledger_only_report.py`
(new), `tests/test_rule_ledger_gstr_reconciliation.py` (new),
`data/gstr1_summary.csv` / `gstr3b_summary.csv` (regenerated, 16 rows
each), `data/general_ledger_FY2025-26.csv` (new), `output/gstr_report.json`
/ `combined_report_FY2025-26.json` / `ledger_leg_flags_FY2025-26.csv` /
`voucher_sequence_gap_flags_FY2025-26.csv` /
`ledger_gstr_reconciliation_report.json` (new/regenerated).
