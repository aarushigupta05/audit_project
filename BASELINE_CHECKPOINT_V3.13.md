# BASELINE CHECKPOINT V3.13 — FY2025-26 Header-Detection Bug Fix + Non-GST Reconciliation

## 0. Relationship to V3.12

V3.12 closed out GSTR-1/GSTR-3B multi-month parsing and added the first
ledger-vs-GSTR-1 reconciliation (`rule_ledger_gstr_reconciliation.py`),
reporting the FY2025-26 ledger's taxable supply as reconciling cleanly
with GSTR-1 across all 12 overlapping months, with non-GST (fuel/petrol-
diesel, Table 8) supply explicitly left unreconciled — the ledger's
"Sales" account showed a Dr/Cr pattern that didn't make sense and was off
from GSTR-1's `non_gst_value` by roughly 5-8x.

This checkpoint is the investigation into that exact open question. It
found the Dr/Cr pattern was never a real business irregularity — it was
100% an artifact of a page-header-detection bug in
`reconstruct_ledger_entries.py` that affected **92% of the FY2025-26
ledger's 13,981 voucher occurrences** (silently wrong or dropped account
attribution on 12,852 of them). Fixing it required four separate,
individually-verified sub-fixes, plus two new general consolidation
mechanisms, and converges exactly back to the FY2025-26 ledger's known
3/5,947 unbalanced-voucher baseline — while also, for the first time,
making the non-GST reconciliation the docstring above flagged as future
work actually reconcile to the rupee on all 12 months.

**The frozen FY2024-25 ledger and its `general_ledger.csv` are
byte-for-byte unchanged by every fix in this checkpoint** — reverified
after each of the 6 sub-fixes below, not just once at the end.

## 1. How the investigation started

Resuming the non-GST "Sales account" question from V3.12, direct
pdfplumber inspection of the FY2025-26 ledger's raw PDF text (not just
the reconstructed CSV) found that the page-header line
`"<Name> : <date> to <date> Page N"` was being looked for only at
`header_lines[0]` — true for the FY2024-25 ledger, but false for
FY2025-26, which prepends a constant `"ANONYMISED FIRM"` line to every
single page (confirmed: 100% of its 888 pages, vs. the FY2024-25 ledger's
firm-letterhead lines which appear on only a handful of pages). This
silently left `account_name` unset on **790 of 888 pages**.

Quantified before writing any fix (per this project's standing
discipline): 12,852 of 13,981 occurrences affected, split into 4,173
Shape-A header legs silently dropped, and 8,679 that fell through
`collect_occurrences()`'s "safety net" fallback (using the printed
counter-party name instead of the page's own account). Reported to the
user via a full before/after scope summary before any code changed;
approved to fix fully, including re-verifying the frozen FY2024-25
baseline.

## 2. The header-detection fixes (4 sub-fixes, each independently verified)

All in `_detect_page_header_and_account()`, `scripts/reconstruct_ledger_entries.py`.

**2.1 — Shape 2: scan every header line, not just index 0.** Generalized
the account+date-range line search to loop over all `header_lines`
instead of assuming position 0. Verified this still matches at index 0,
with identical results, on every FY2024-25 ledger page that relied on
this shape.

**2.2 — Shape 3 (new layout).** A third real header layout, found only
in the FY2025-26 ledger: `"<Name>"` / `"<date> to <date>"` / `"Page N"`
each on their own line, no "Ledger Account"/"Book" marker at all — used
for a page that is the first and only page of its account (first-time
coded counterparty accounts like "P006"). Accepted only when **exactly
one** candidate name-line remains after filtering known preamble/artifact
lines — the same "0 or 2+ candidates, don't guess" rule used throughout
this project. This correctly rejected the FY2024-25 ledger's 2 genuinely-
redacted pages (477, 546 — a different, 2-line firm-letterhead preamble
that an unconditional exclusion would not have caught) and accepted every
genuine 1-line case in the new ledger. A second residual — the literal
"Ledger Account" marker text getting character-dropped into fragments
("Ledg nt", "Le t", "Ledg") on exactly 3 pages — is caught by a new
`_is_garbled_ledger_account_marker()` same-order-subsequence check.

**2.3 — Shape 2: bare "Book"/"Account" qualifier bug.** The qualifier-
word regex `(?:\s+Account|\s+Book)?` requires a *leading space* before
the qualifier, so when the second header line is the qualifier word
*alone* with nothing before it (e.g. `"Book"` or `"Account"` on its own
line), the regex cannot match that optional group and instead keeps the
whole word as `part2`, producing a bogus `"<Name> Book"` / `"<Name>
Account"` suffix. This affected **275 pages** (bare "Book") **+ 608
pages** (bare "Account") across both documents — the FY2024-25 ledger's
own bare-"Account" cases (268 of the 608) were invisibly masked the whole
time by 4 pre-existing `_ACCOUNT_NAME_CANONICALIZATION` entries (`"Sales
Account"`, `"Purchase Account"`, `"CGST Account"`, `"SGST Account"`) that
happened to cover exactly its affected accounts; every other FY2024-25
account hitting this bug carried the bogus suffix undetected the whole
time, with no visible effect because nothing else in that document ever
referenced the same account under a different spelling. Fixed by
special-casing a bare qualifier word to contribute nothing (`account_name
= part1`), generalizing what was previously a "Cash"-only special case.
Verified: FY2024-25 ledger's full reconstruction output stayed
byte-identical despite stripping the suffix from 268 more pages there too
— proving every one of those was already either masked or harmless.

## 3. `continuation_name_aliases` — new general mechanism

**Found:** even after 2.1-2.3, the FY2025-26 ledger still had **245
unbalanced vouchers** (was 3 before this investigation). Tracing one
(`Sales GST_INV /25-26/341`) found a spurious duplicate Dr leg: `"B.R
TRADING"` (page 26, its own header) alongside `"P023"` (pages 609, 683,
detail-leg references elsewhere) — same real account, reprinted under
two different spellings that `merge_occurrences()` never recognized as
the same leg, double-counting it.

**Root cause:** page 25 ("P023", a Shape-3 first page) ends `"Carried
Over 17,96,133.00"`; page 26 ("B.R TRADING", a Shape-2 page) begins
`"Brought Forward 17,96,133.00"` — an exact match. The account's own
ledger genuinely changes how it prints its name mid-stream: anonymized
code on its first page, real un-anonymized name on its continuation
page — an inconsistency in the source document itself, not a parsing
bug.

**Fix:** generalized `_resolve_headerless_pages()` (which already proved
exactly this kind of continuity for headerless pages) to also catch
adjacent page pairs where **both** headers parse fine but resolve to
*different* names, verified by the identical Carried-Over/Brought-Forward
amount-match proof. Exhaustive scan across the full page range of both
documents (not just the vouchers found via the symptom) found **34
genuine pairs in the FY2025-26 ledger** and **2 in the FY2024-25
ledger** — zero amount mismatches among any candidate pair, zero
ambiguity (no later-name ever proven to continue from two different
earlier names). The later name is always folded into the earlier one
(matching the direction already used by this project's hand-verified
`_ACCOUNT_NAME_CANONICALIZATION` continuation entries), since the earlier
name is what the rest of the document's other pages actually use to
reference the account.

**Scoping:** the 2 FY2024-25 candidates (`"Rent Expenses HPCL"` →
`"Rent Expenses"`, `"TDS Receivable HPCL"` → `"TDS Receivable"`) are
real and pass every check, but changing that document's committed output
was out of scope for this investigation's explicit approval (fix
FY2025-26, re-verify FY2024-25 untouched). `collect_occurrences()` now
takes `apply_continuation_name_aliases` (default `True`); `main()`
passes `False` specifically for `ledgers_redacted.pdf`, so the mechanism
is fully implemented and tested but inert for the frozen baseline until
those 2 pairs are separately reviewed and approved.

## 4. `_PER_VOUCHER_NAME_ALIASES` — restructured + 3 new entries

After §3, 57 vouchers remained unbalanced. All resolved to the SAME
overloaded-short-name class this mechanism was built for in V3.9: a
short string that is legitimately free-form detail-leg shorthand for one
specific account, verified via the established entry_id + dr_cr +
exact-amount + fully-disjoint-pages proof, re-derived fresh from the
post-§3 reconstruction output with **zero ambiguous cases**:

- `"CAMBRIDGE"` → `"P026"` — bare "CAMBRIDGE" is ALSO shorthand for a
  *different* account than the FY2024-25 entry's `"CAMBRIDGE
  INTERNATIONAL (GENSET A/C)"` (P027's own continuation name, via §3).
  This is exactly why `_PER_VOUCHER_NAME_ALIASES` was restructured from a
  `dict` to a `list` of `(short, long)` pairs — a dict cannot hold two
  different targets for the same key, and here the same short string
  genuinely needs two.
- `"PRINCIPAL MHAC VEH. ACCOUNT"` → `"P118"` — the FY2025-26 ledger's own
  version of the FY2024-25 "vehicle sub-account" pattern.
- `"TDS"` → `"TDS RECEIVABLE SUPPLIER"` — a 3rd trailing header line
  ("SUPPLIER") that neither Shape 2 branch consumes, plus the same
  shorthand-elsewhere pattern as CAMBRIDGE.

`test_per_voucher_aliases_dict_contents` was updated to assert the
FY2024-25 pairs remain a *subset* of the (now list-typed) mechanism,
rather than exact equality, so FY2025-26-specific pairs don't require
re-enumerating the FY2024-25 set.

## 5. Result: back to exactly 3/5,947, fully explained

After §2-4, the FY2025-26 ledger reconstructs to **3/5,947 unbalanced
vouchers — `Journal_777`, `Journal_778`, `Journal_779`, with the
identical amounts as the original pre-investigation baseline.** Every
other voucher that was ever unbalanced during this investigation (266 at
the worst point, immediately after §2 alone) is now fully explained and
resolved, not just reduced. `general_ledger_FY2025-26.csv` was
regenerated from the corrected reconstruction.

Full regression, each re-run after every sub-fix above (not just once at
the end): FY2024-25 ledger's 3 reconstruction CSVs + `general_ledger.csv`
byte-identical to the pre-investigation backup; full test suite green
throughout.

## 6. Non-GST reconciliation — the original question, answered

With per-account attribution finally correct, the "Sales" account (the
non-taxable fuel/petrol-diesel revenue account) has **zero Dr legs
anywhere in the ledger** — all 2,145 occurrences are Cr — exactly the
clean shape a pure revenue account should have. Its Cr total, summed per
month over vouchers with no CGST/SGST/IGST leg, matches GSTR-1's
`non_gst_value` **exactly, to the rupee, on all 12 overlapping months**,
with zero exceptions:

| Month | Ledger | GSTR-1 |
|---|---|---|
| April 2025 | 4,08,27,290.18 | 4,08,27,290.18 |
| … | … | … |
| March 2026 | 4,03,79,094.86 | 4,03,79,094.86 |

(full table: every one of the 12 months matches; see
`tests/test_rule_ledger_gstr_reconciliation.py::test_real_data_non_gst_matches_gstr1_exactly_every_month`)

The "5-8x off, pattern not understood" limitation in
`rule_ledger_gstr_reconciliation.py`'s module docstring is resolved:
`derive_monthly_non_gst_supply()` (mirror image of
`derive_monthly_taxable_supply()` — same voucher grouping, opposite tax-
leg condition) and a new `"ledger_vs_gstr1_non_gst_supply"` check were
added to `check_ledger_gstr_reconciliation()`, with 9 new tests (4
synthetic derivation tests, 3 synthetic reconciliation tests, 2 real-data
tests) alongside the existing 14.

## 7. Files touched this checkpoint

`scripts/reconstruct_ledger_entries.py` (4 header-detection sub-fixes,
new `continuation_name_aliases` mechanism in
`_resolve_headerless_pages()`, `_PER_VOUCHER_NAME_ALIASES` restructured
dict→list + 3 new entries, `collect_occurrences()`'s new
`apply_continuation_name_aliases` parameter, `main()`'s frozen-baseline
gate), `scripts/rule_ledger_gstr_reconciliation.py` (new
`derive_monthly_non_gst_supply()`, `NON_GST_REVENUE_ACCOUNT` constant,
extended `check_ledger_gstr_reconciliation()` and `run_ledger_gstr_report()`,
docstring rewritten), `tests/test_header_carryover_fix.py` (3-tuple
unpacking), `tests/test_v3_9_name_pair_fixes.py` (dict→list assertion),
`tests/test_rule_ledger_gstr_reconciliation.py` (9 new tests),
`data/Ledgers_Anonymised_reconstructed_entries.csv` /
`_state_events.csv` / `_unresolved.csv` (regenerated),
`data/general_ledger_FY2025-26.csv` (regenerated). FY2024-25 files
(`data/ledgers_redacted_reconstructed_*.csv`, `data/general_ledger.csv`)
confirmed byte-identical, not regenerated in any way that changed them.

Full suite: **366 tests, 366 passing** (357 + 9 new).
