# Input Normalization Layer — Version Checkpoint (V3.9 Frozen)

**Date**: 2026-10-02
**Commit**: `703cb54`
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.8
**Freeze basis**: Two independent, deterministic, evidence-backed fixes completed in one continuous pass per explicit instruction: (1) the FEES/COMPANY narration-split bug, and (2) full resolution of the 77-voucher customer/debtor name-fragmentation issue first logged in V3.7 §6 and classified (but left unfixed) in V3.8 §6. All 77 are now resolved. No fuzzy matching, no guessed identities, no bundled unrelated bugs.

---

## 1. Relationship to V3.8

V3.8 (`BASELINE_CHECKPOINT_V3.8.md`, commit `4947c5d`) is **not invalidated** — it remains correct on every metric it was frozen against. V3.8 §7 logged two deferred items: the FEES narration-continuation bug, and the 77-voucher issue (7 distinct name-pairs, left unfixed). This checkpoint resolves both.

---

## 2. Fix #1 — FEES/COMPANY Narration-Split Bug

### Root cause

`collect_occurrences()`'s per-line classification loop checked **"wrapped particulars continuation"** (`is_wrapped_part`) *before* **"narration"** (`is_narration`). When a line was classified as narration (e.g. `"WEIGHT AND MEASUREMENT"`, matching `_NARRATION_PREFIXES`), and the very next physical line was a bare continuation token already in `_PARTICULARS_CONTINUATION_TOKENS` (e.g. `"FEES"`, `"COMPANY"`), the code treated that next line as continuing the **active leg's** particulars/counterparty, instead of recognizing it as the second half of the narration phrase just started (`"weight and measurement fees"`, `"aggarwal and company"`). This is a general priority-ordering bug, not FEES-specific — it also affects the `"company"` continuation token.

### Population (exhaustive, zero false positives)

A body-line-only scan (lines after each page's own `body_start_idx`, using the real imported `_detect_page_header_and_account`, `_PARTICULARS_CONTINUATION_TOKENS`, `_NARRATION_PREFIXES`, `_resolve_headerless_pages`) of all 926 pages found exactly **9 real occurrences**:

| Page(s) | Narration start | Continuation token | Voucher |
|---|---|---|---|
| 31, 433 | WEIGHT AND MEASUREMENT | FEES | Payment_293 |
| 31, 154 | WEIGHT AND MEASUREMENT | FEES | Payment_446 |
| 153, 913 | BSNL WIFI INSTALLITION | FEES | Payment_436 |
| 392, 778 | AGGARWAL AND | COMPANY | Receipt_39 |
| 431, 778 | AGGARWAL AND | COMPANY | Receipt_805 |
| 649, 778 | AGGARWAL AND | COMPANY | Journal_568 |

(An earlier, cruder scan checking every line on every page produced 42 candidate hits; 33 were false positives from page-header-region text like `"Purchase Ledger : ... Page 562"` + `"Account"`, which are consumed entirely by `_detect_page_header_and_account()` before body processing ever runs and can never trigger the real bug. Rewriting the scan to only examine true body lines, using the real production constants, eliminated all false positives.)

### Fix

In `collect_occurrences()`, before the existing `is_wrapped_part` check: if the immediately preceding raw source line was itself classified as narration **and** is confirmed to be the literal last entry already appended to this occurrence's narration list, a continuation-token line now extends that narration entry instead of being glued onto the active leg. Guarded tightly — only fires when the preceding line is both narration-classified and exactly equal to `narration[-1]`, so a continuation token can never "reach back" across an intervening leg or header line.

### Verification

- All 9 target narrations now read as one clean phrase (`"WEIGHT AND MEASUREMENT FEES"`, `"BSNL WIFI INSTALLITION FEES"`, `"AGGARWAL AND COMPANY"`) with every original source page preserved.
- Zero standalone orphaned `"FEES"`/`"COMPANY"` narration entries remain anywhere in the 926-page population (exhaustive check).
- Zero corruption of the `particulars`/account column (was already confined to narration pre-fix; confirmed still the case, now clean rather than merely confined).
- No leg/voucher counts changed (named legs, orphan legs, unresolved count, state events all identical) — this is a narration-text-only fix.

---

## 3. Fix #2 — The 77-Voucher Issue: Full Resolution

### Re-investigation method

V3.8 classified 9 name-pairs (1 safe → CGST/SGST, fixed in V3.8; 8 unsafe → deferred). V3.9 re-examined all 8 deferred pairs with two sharper, still 100%-deterministic tests, neither involving fuzzy matching or guessed identity:

1. **Carried-Over/Brought-Forward page continuity** (same technique as the page 547/548 check in V3.6) — proves two adjacent header pages are ONE continuous physical ledger, not two accounts.
2. **Per-voucher `entry_id` + `amount` + `dr_cr` matching across source pages** — for a specific voucher, if one spelling's occurrence(s) and another spelling's occurrence(s) share the exact same amount and Dr/Cr direction but come from **different** pages, that is deterministic proof of one real reprinted leg, not a coincidence. Same-page overlap, or any other leg in the same voucher sharing that amount/direction, invalidates the match and blocks the merge.

### 2 pairs reclassified as SAFE, added to `_ACCOUNT_NAME_CANONICALIZATION`

| Pair | V3.8 call | V3.9 finding |
|---|---|---|
| `JANDIYAL ELECTRONICS` (page 383) / `JANDIYAL ELECTRONICS PARTNER` (page 382) | "Affirmative evidence of two different real accounts" | **Reversed.** Page 382 ends `"Carried Over 7,34,963.00 6,46,342.00 continued ..."`; page 383 begins `"Brought Forward 7,34,963.00 6,46,342.00"` — exact match, one continuous ledger. Page 383's 3-line wrapped header (`"JANDIYAL"` / `"ELECTRONICS Account"` / `"PARTNER"`) is mis-parsed by `_detect_page_header_and_account()`'s Shape-2 logic, which only reattaches a 3rd header line when it reads `"CO."`/`"A/C"`/`"LTD."` — `"PARTNER"` isn't in that list, so it's silently dropped. `"JANDIYAL ELECTRONICS"` has zero detail-leg occurrences anywhere (926-page scan). |
| `KC EDU.SOCIETY( GENSET AC` (pages 470–471) / `KC EDU.SOCIETY(GENSET AC` (page 469) | "Unverified continuity, both forms have header pages" | **Confirmed as continuation.** Page 469 ends `"Carried Over 2,92,702.00 continued ..."`; page 470 begins `"Brought Forward 2,92,702.00"` — exact match. Page 469's header prints unwrapped (no space before "GENSET"); pages 470–471 wrap it across two lines, which the parser rejoins **with** a space. `"KC EDU.SOCIETY( GENSET AC"` has zero detail-leg occurrences anywhere. |

Both pass the exact same bar already established for Sales/Purchase/Round-Off-Discount/CGST/SGST: continuous header-leg-only ledger run, zero detail-leg occurrences for the variant spelling, zero amount conflicts confirmed across the full cross-variant population.

### 6 pairs resolved via new, narrower `_PER_VOUCHER_NAME_ALIASES` mechanism

These do **not** qualify for `_ACCOUNT_NAME_CANONICALIZATION` — their short form has **zero header-leg occurrences anywhere** in the document (confirmed by direct header/detail-leg population check), so there is no page to canonicalize at that insertion point at all. The short form exists purely as free-form detail-leg text (`_LEG_RE`), independent of `current_account`, reprinted as a debtor/vendor counter-party name on other ledgers' pages.

| Pair | Short form's role | Long form's role | Common vouchers |
|---|---|---|---|
| `ARO OFFICE B-1204` / `...ROYAL PALMS C/O NAGBANI` | Detail-leg only (20 occ., 20 pages) | Header leg only (page 12–13) | 10 |
| `CAMBRIDGE` / `CAMBRIDGE INTERNATIONAL (GENSET A/C)` | Detail-leg only (14 occ.) | Header leg only (page 32) | 7 |
| `PRINCIPAL MHAC VEH. ACCOUNT` / `...VEH.ACCOUNT` | Detail-leg only (72 occ.) | Header leg only (pages 552–555) | 32 |
| `Principal MHAC School` / `...School Nagbani` | Detail-leg only (20 occ.) | Header leg only (pages 550–551) | 10 |
| `SGF INFRA PVT LTD` / `...GURHA BAKSHI NAGAR` | Detail-leg only (22 occ.) | Header leg only (pages 784–786) | 11 |
| `Veh. Running &` / `...& Maintenance Exp` | Detail-leg only (3 occ.) | Header leg only (page 919) | 1 |

(Common-voucher counts here are higher than the V3.8-era 77-voucher subset for 3 of these pairs — 32 vs. 26, 11 vs. 10 — because this fix operates on the *full* cross-variant population dataset-wide, not only the vouchers that happened to flip unbalanced in the V3.6→V3.7 transition. This mirrors exactly how the CGST/SGST fix in V3.8 resolved 766 vouchers outside the original 77.)

**Verification performed before implementing**: for every one of the 71 candidate vouchers across these 6 pairs, confirmed (a) short-form and long-form occurrences always originate from **different** pages — never the same page — and (b) the matched amount+dr_cr never collides with any other leg in the same voucher. Zero ambiguous/colliding cases found.

**Mechanism**: `merge_occurrences()` now runs a small, explicit, whitelist-only consolidation pass after the normal per-voucher leg grouping: for each of the 6 listed pairs, if both spellings exist under the same `entry_id` and `dr_cr`, their source pages are completely disjoint, and their amounts match exactly, the short-form leg is merged into the long-form (canonical) leg, combining source pages. If either safety condition fails for a given voucher, both legs are left untouched (defensive fallback — not observed to trigger in the real data, same philosophy as other safety nets in this codebase).

---

## 4. The Fix (code changes)

`_ACCOUNT_NAME_CANONICALIZATION` extended with 2 new entries:
```python
"JANDIYAL ELECTRONICS": "JANDIYAL ELECTRONICS PARTNER",
"KC EDU.SOCIETY( GENSET AC": "KC EDU.SOCIETY(GENSET AC",
```

New dict `_PER_VOUCHER_NAME_ALIASES` (6 entries) plus a new consolidation pass at the end of `merge_occurrences()` — see source comments for the full safety rationale.

`collect_occurrences()`'s per-line loop: narration-vs-wrapped-continuation priority fix (see §2).

**Scope discipline**: no other account names were touched. Page 548's `"Ledger Tr. A/c"` label remains out of scope (V3.6). No fuzzy matching was used anywhere — every merge is keyed on exact string equality plus exact amount/page-disjointness checks.

---

## 5. Validated Metrics

| Metric | V3.8 | V3.9 | Changed? |
|---|---|---|---|
| Raw voucher occurrences | 14,625 | 14,625 | No |
| Canonical vouchers | 6,216 | 6,216 | No |
| Named legs | 14,740 | 14,649 | **Yes — exactly −91** (2 header-canonicalized pairs + 6 per-voucher pairs, matches quantified population) |
| Total GL rows | 14,762 | 14,671 | **Yes — exactly −91** |
| Orphan legs | 22 | 22 | No |
| Unresolved + conflicts | 579 | 579 | No — **zero new `leg_amount_mismatch_across_occurrences`** |
| Unbalanced vouchers | 120 | 29 | **Yes — exactly −91**, zero newly introduced (full before/after diff: `after_unbalanced ⊆ before_unbalanced`) |
| Flip-77 vouchers resolved | 0 | **77 / 77** | All originally-flagged vouchers now balanced |
| State events | 1,720 | 1,720 | No |

**Trial balance reconciliation**: 82/82 matched accounts clean, unchanged.

**Source-page traceability**: every merged leg's `source_pages` is the union of both spellings' pages — no source information discarded. Verified by direct inspection (e.g. `ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI` / `Sales GST_MPSS/24-25/135`: pages `{12, 618, 675}`, all 3 preserved).

---

## 6. Downstream Impact — Rule 5 and Benford's Law (re-pinned)

**Benford's Law**: chi-square 227.06 → 228.15. Same mechanical cause as every prior canonicalization fix: the 91 consolidated rows were each counted twice before this fix (once per spelling, identical amount); correcting inflated duplicate counts. Still far above the deviation threshold both before and after — dataset-level conclusion unchanged.

**Rule 5/2 flags**: **1,282 → 1,282 (unchanged)**. Full key-level decomposition (`entry_id`, `account`, `amount`, `severity`, `source_rules`) against the V3.8 baseline: **0 removed, 0 added**. Explanation: unlike CGST/SGST (used by hundreds of unrelated vouchers, so consolidating them collapsed real duplicate-transaction matches), each of these 8 pairs' canonical name is a single debtor/vendor used by only that debtor's own vouchers — Rule 5 (`duplicate_transaction`, which matches across *different* vouchers sharing date+account+amount) never matched between a pair's short and long spelling in the first place, since they were different account strings even before the fix. Multi-signal set unchanged (8, identical entries).

Re-pinned in `tests/test_combined_report_regression.py` only after this decomposition was produced and reviewed.

---

## 7. The Original 77-Voucher Issue — Fully Resolved

**Resolved: 77 / 77.** Verified directly: every entry_id in `tests/fixtures_flip77_entry_ids.txt` is now `entry_balanced=True`. This closes the item first logged in V3.7 §6 and carried through V3.8 §6/§7.

**What else this fix resolved**: an additional 14 vouchers (91 total − 77 original) elsewhere in the dataset, for the 3 pairs whose dataset-wide population is larger than their flip-77 subset (`PRINCIPAL MHAC VEH.ACCOUNT` 32 vs. 26, `SGF INFRA PVT LTD` 11 vs. 10, plus `KC EDU.SOCIETY` and `JANDIYAL` contributing vouchers that weren't part of the original flip-77 set at all but were driven by the exact same underlying name-fragmentation).

---

## 8. Known Residual Issues — Deferred, Not Fixed

- **29 remaining unbalanced vouchers** — pre-existing, unrelated to the FEES bug or the 8 name-pairs; confirmed by direct before/after diff to be a strict subset of the 120 that were already unbalanced in V3.8 (zero new imbalances introduced by this work). Root cause not yet investigated — out of scope for this session's explicit instruction (FEES + the 7/8 deferred name-pairs only).
- **Page 548's `"Ledger Tr. A/c"` label** — unchanged, still out of scope (V3.6).
- No other name-pair patterns were found to be "directly related" and safe to fix in this pass; nothing else was bundled in.

---

## 9. New/Updated Regression Tests

`tests/test_v3_9_name_pair_fixes.py` (new, 13 tests): FEES/COMPANY fix verification (clean narration, no corruption, exhaustive no-standalone-token guard); both canonicalization-dict additions present; Carried-Over/Brought-Forward continuity re-verified directly against the raw PDF (independent of the reconstruction pipeline, so a future source-document change that breaks this proof fails loudly); `_PER_VOUCHER_NAME_ALIASES` dict contents pinned; short forms fully absorbed; a concrete sample voucher's merge verified leg-by-leg with exact source pages; two synthetic-data structural safety tests (same-page overlap refuses to merge; amount mismatch refuses to merge); all 77 flip vouchers now balanced; no new unbalanced vouchers introduced (29 pinned); trial balance still clean.

`tests/test_cgst_sgst_canonicalization_fix.py`: `EXCLUDED_PAIRS` updated — JANDIYAL/KC EDU.SOCIETY removed (now canonicalized), `Veh. Running &` added (was missing from this list, now complete); the "remains unbalanced" test re-purposed to "now resolved by V3.9" with a clear docstring explaining the supersession.

`tests/test_shape_b_fix.py`: FEES test docstring updated to reflect the bug is now fixed (assertion itself was already correct and unchanged — it only ever checked the `particulars` column, which was never corrupted).

`tests/test_combined_report_regression.py`: `test_total_leg_count_pinned` and `test_benford_chi_square_pinned` re-pinned; `test_leg_flag_counts_pinned` docstring updated to document the full key-level decomposition confirming zero change.

Full suite: **128 tests, 128 passing** (112 from V3.8 + 16 net new/changed).

---

## 10. Preserved Architecture & Invariants

- Tally Direction Rule (`To`=Dr, `By`=Cr relative to the page's own account) — unchanged.
- Two-pass reconstruction (`collect_occurrences()` → `merge_occurrences()`) — unchanged in structure; `merge_occurrences()` gains one new, narrow, whitelist-only post-processing pass.
- Shape A / Shape B voucher parsing — unchanged.
- `_ACCOUNT_NAME_CANONICALIZATION` insertion point and safety bar — unchanged; 2 new entries added under the identical, previously-proven rigor.
- No fuzzy matching, no Levenshtein/similarity scoring, no manual overrides by entry_id — every merge in this codebase is still either exact-string canonicalization at a single proven insertion point, or exact entry_id+amount+dr_cr+disjoint-pages matching.
