# Input Normalization Layer — Version Checkpoint (V3.10 Frozen)

**Date**: 2026-10-02
**Commit**: `9aa6b3d6a9230908ffed4639cb1c2ef44da9ae03`
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.9
**Freeze basis**: Investigating the 29 unbalanced vouchers left over from V3.9. Found 5 distinct root-cause patterns. Patterns 1–3 resolved via the existing, already-proven canonicalization/per-voucher-alias mechanisms (no new logic). Pattern 4 resolved via one new, narrow mechanism in `merge_occurrences()`, implemented only after an exhaustive, read-only, whole-document bounded simulation proved it safe. Pattern 5 (a single header-regex ambiguity) deliberately left unfixed and documented — the blast radius of touching a shared regex isn't justified by one occurrence. 27 of the 29 are now resolved; 2 remain, by design.

---

## 1. Relationship to V3.9

V3.9 (`BASELINE_CHECKPOINT_V3.9.md`, commit `703cb54`) is **not invalidated** — it remains correct on every metric it was frozen against. V3.9 §8 logged the 29 remaining unbalanced vouchers as an unexamined, pre-existing, unrelated issue. This checkpoint investigates and resolves 27 of them.

---

## 2. Investigation: the 29 Unbalanced Vouchers

A full leg-breakdown of all 29 vouchers (via the reconstructed-entries CSV) revealed 5 distinct root-cause patterns, reported for approval before any code change:

| # | Pattern | Vouchers | Mechanism used |
|---|---|---|---|
| 1 | `Employee A -Salary A/c` / `Employee A-Salary A/c` header-spelling variant | subset of the 21 | `_ACCOUNT_NAME_CANONICALIZATION` |
| 1 | `Employee B -Salary A/c` / `Employee B-Salary A/c` header-spelling variant | subset of the 21 | `_ACCOUNT_NAME_CANONICALIZATION` |
| 1 | `EDLI Account` / `EDLI` header-spelling variant | subset of the 21 | `_ACCOUNT_NAME_CANONICALIZATION` |
| 2 | `DISCOUNT Account` / `DISCOUNT` — same-page same-voucher duplicate | subset of the 21 | `_ACCOUNT_NAME_CANONICALIZATION` + pre-existing Pass-1 redundant-header-leg dedup |
| 3 | `Tr. A/c` / `Ledger Tr. A/c` — the page-548 item deferred since V3.6 | 12 vouchers | `_PER_VOUCHER_NAME_ALIASES` |
| 4 | Redacted-account orphan legs (blanked counterparty on other accounts' pages) | 6 vouchers (`Journal_604/671/672/684/687/690`) | New orphan-absorption rule, gated on a bounded simulation |
| 5 | `Payment_2` / `Sales_Payment` header-regex ambiguity | 1 voucher (unbalanced on both sides, so 2 entry_ids) | Deferred — not fixed |

---

## 3. Patterns 1–3 — Resolved via Existing Mechanisms

### Pattern 1: Employee A / Employee B / EDLI header-spelling variants

Each pair proven safe by the same Carried-Over/Brought-Forward page-continuity technique established in V3.6/V3.9:

| Pair | Evidence |
|---|---|
| `Employee A -Salary A/c` → `Employee A-Salary A/c` | Page 14 ends `"Carried Over 2,70,000.00 continued ..."`; page 15 begins `"Brought Forward 2,70,000.00"` — exact match. |
| `Employee B -Salary A/c` → `Employee B-Salary A/c` | Page 304 ends with the matching Carried Over balance; page 305 begins with the identical Brought Forward balance (Rs.2,25,000.00) — exact match. |
| `EDLI Account` → `EDLI` | Page 318 ends with the matching Carried Over balance; page 319 begins with the identical Brought Forward balance (Rs.2,820.00) — exact match. |

Each variant form has **zero detail-leg occurrences** anywhere in the cross-variant population, and the full cross-variant population shows **zero amount conflicts** after canonicalizing (Employee A: 3/45 occurrences, 0 conflicts; Employee B: 3/45, 0 conflicts; EDLI: 5/55, 0 conflicts).

### Pattern 2: DISCOUNT Account / DISCOUNT

Different shape from the others: page 314 is DISCOUNT's own single ledger page, printing **one voucher** (`Sales GST_MPSS/24-25/259`) **twice** on the same page — once as the header-leg summary line, once as an explicit Shape-A detail-leg breakdown line also literally named "DISCOUNT". Canonicalizing `DISCOUNT Account` → `DISCOUNT` lets the **pre-existing** Pass-1 redundant-header-leg dedup rule in `collect_occurrences()` do the rest automatically — no new logic needed. (Confirmed this candidate correctly fails the `_PER_VOUCHER_NAME_ALIASES` disjoint-pages safety check, since both spellings share page 314 — which is exactly why it needed this different route.)

### Pattern 3: Tr. A/c / Ledger Tr. A/c

The "page 548 Ledger Tr. A/c" item deferred since V3.6. `Tr. A/c` has **zero header pages** anywhere (pure detail-leg text, 24 occurrences across pages 56–58, 902–906); `Ledger Tr. A/c` is the account's own genuine header-leg name (pages 547–548). Resolves 12 "Carriage Expenses Consolidated" vouchers (`Journal_125,126,127,195,312,362,415,480,537,566,567,666`). All 12 candidates verified: zero page overlap, exact amount match, zero ambiguity.

### Code changes

`_ACCOUNT_NAME_CANONICALIZATION` extended with 4 new entries:
(Employee names are placeholders here; the code holds the real ledger spelling.)
```python
"Employee A -Salary A/c": "Employee A-Salary A/c",
"Employee B -Salary A/c": "Employee B-Salary A/c",
"EDLI Account": "EDLI",
"DISCOUNT Account": "DISCOUNT",
```

`_PER_VOUCHER_NAME_ALIASES` extended with 1 new entry:
```python
"Tr. A/c": "Ledger Tr. A/c",
```

No new logic — both mechanisms are exactly the ones proven and frozen in V3.9.

---

## 4. Pattern 4 — Redacted-Account Orphan Absorption

### Root cause

The 2 redacted accounts (see `_resolve_headerless_pages()`) get a synthetic header leg on their **own** page(s) (477, 546) using the invented placeholder label (e.g. `"[Redacted account — TB opening Cr 2,05,51,866.76]"`) as particulars. But every time one of their vouchers is **also** reprinted on some other account's own page (where this account is the counterparty), the counterparty's name was blanked out in the source document itself, so it parses as an orphan leg (`particulars=None`) rather than the placeholder text — the placeholder is a label this project invented for the account's own header; it is never literally printed as body-line particulars anywhere in the source. The same physical leg therefore ends up counted twice: once under the placeholder, once as an orphan — which is exactly why these 6 vouchers were unbalanced.

### Bounded simulation (read-only, run BEFORE any code change)

Per explicit instruction, before touching `merge_occurrences()` or any other code, an exhaustive, read-only simulation checked every orphan leg in the full 926-page population against the proposed rule (same `entry_id`, exact amount, exact `dr_cr`, exactly one eligible named redacted-placeholder leg in that voucher):

| Metric | Result |
|---|---|
| Total orphan legs scanned | 22 |
| Candidate matches (exactly 1 eligible) | **22** |
| Ambiguous candidates (2+ eligible) | **0** |
| Unmatched (0 eligible) | **0** |
| Affected vouchers | **6** — `Journal_604, 671, 672, 684, 687, 690` |
| Anything beyond the known 6? | **No** |

**Decision gate: PASSED exactly** (all 22 resolved to exactly 1 candidate, 0 ambiguous, 0 unmatched, exactly the 6 known vouchers, no unexpected candidates) → implemented.

### Implementation

A new, narrow absorption step added to `merge_occurrences()`, after the existing per-voucher alias consolidation pass: for each orphan leg, if **exactly one** named leg in the same voucher has particulars starting with `"[Redacted account"` and the same `dr_cr` and the same exact `amount`, the orphan's source page is folded into that leg's `source_pages` (corroboration) and the orphan is removed from `orphan_legs` — it is the same physical leg, not a new one, so it must never be double-counted against the voucher's Dr/Cr balance. 0 or 2+ candidates leave the orphan untouched (still reported as `UNRESOLVED`) — no guessing.

### Code changes

New constant `_REDACTED_PLACEHOLDER_PREFIX = "[Redacted account"` with full rationale inline, and a new absorption loop at the end of `merge_occurrences()`. See source comments for the complete safety rationale and the simulation this was gated on.

---

## 5. Pattern 5 — Payment_2 / Sales_Payment: Deliberately Deferred

### Root cause (documented, not fixed)

`_STANDARD_VCH_TYPES` includes bare `Sales` as a valid voucher type. The 3 header regexes (`_HEADER_RE`, `_FUSED_HEADER_RE`, `_WRAPPED_PARTICULARS_HEADER_RE`) use a non-greedy `(?P<particulars>.+?)` before `vch_type`. Page 390 (J & K Bank Ltd's own ledger) contains the line `"By Bhagwati Sales Payment 2 6,800.00 ... Dr"` — a truncated counterparty name ("Bhagwati Sales Corporation" → "Bhagwati Sales") happens to contain the bare word "Sales" immediately followed by a real vch_type word ("Payment"). The regex misfires: `vch_type="Sales"`, `vch_no="Payment"` (swallowing the real vch_no "2" into leftover text), producing a bogus `entry_id="Sales_Payment"` instead of the correct `"Payment_2"` (which is also, separately and correctly, parsed on page 24, Bhagwati Sales Corporation's own ledger — hence 2 unbalanced entry_ids from 1 underlying voucher).

### Why deferred

An exhaustive scan for alphabetic-only `vch_no` values across all 926 pages found **exactly 1 occurrence** of this ambiguity shape in the entire document. Changing a shared header regex used by every voucher in the dataset is not justified by the evidence for a single case at this stage. Left unchanged, fully documented as a future investigation item.

---

## 6. Validated Metrics

| Metric | V3.9 | V3.10 | Changed? |
|---|---|---|---|
| Raw voucher occurrences | 14,625 | 14,625 | No |
| Canonical vouchers | 6,216 | 6,216 | No |
| Named legs | 14,649 | 14,625 | **Yes — exactly −24** (Patterns 1–3: 11 header-canon removals + 1 DISCOUNT dedup + 12 Tr. A/c per-voucher merges) |
| Orphan legs | 22 | 0 | **Yes — exactly −22** (Pattern 4 absorption) |
| Total GL rows | 14,671 | 14,625 | **Yes — exactly −46** (−24 + −22 above) |
| Unresolved + conflicts | 579 | 579 | No — zero new `leg_amount_mismatch_across_occurrences` |
| Unbalanced vouchers | 29 | **2** | **Yes — exactly −27**, zero newly introduced |
| State events | 1,720 | 1,720 | No |

**Trial balance reconciliation**: 82/82 matched accounts clean, unchanged.

**Reproducibility**: a clean from-scratch re-run of the full 926-page reconstruction + ledger adaptation produced byte-identical output files.

---

## 7. Downstream Impact — Rule 5 and Benford's Law (re-pinned)

**Benford's Law**: chi-square 228.15 → 229.74. Same mechanical cause as every prior fix in this project: the leg population shrank by 46 (see §6), each removed row a duplicate/double-represented copy of an amount already counted once elsewhere, not a new or lost transaction. Still far above the deviation threshold both before and after — dataset-level conclusion unchanged.

**Rule 5 / combined-report flags**: **1,282 → 1,271 (−11)**. Full key-level decomposition (`entry_id`, `account`, `amount`, `severity`, `source_rules`) against the V3.9 baseline: 11 removed, 0 added, zero unexplained, every one traced to its exact cause:

- **3×** `Employee A -Salary A/c` Rs.30,000 MEDIUM `round_number_bias` (`Journal_657/658/659`): before Pattern 1's canonicalization, this voucher's salary leg was double-counted as two separate named legs (one per spelling), each independently flagged; canonicalizing folds them into one leg under the canonical spelling, which was **already** flagged and remains flagged, unchanged.
- **8×** `UNRESOLVED` HIGH `round_number_bias` (`Journal_684`: 2×Rs.360,000 + 2×Rs.840,000; `Journal_687`: 2×Rs.210,000 + 2×Rs.490,000): these are exactly the orphan-leg occurrences Pattern 4 absorbs — each one a second, unparseable-counterparty copy of a leg already carried by an existing redacted-placeholder named leg of the identical amount, which was **already** flagged and remains flagged, unchanged.

Severity: LOW 134→134, MEDIUM 161→158 (−3, the Employee A trio), HIGH 627→619 (−8, the redacted-orphan octet), CRITICAL 360→360. Multi-signal set unchanged (8, identical entries).

Re-pinned in `tests/test_combined_report_regression.py` only after this decomposition was produced and reviewed.

---

## 8. The 29 Unbalanced Vouchers — Resolution

**27 of 29 resolved**, verified by direct before/after diff against the V3.9 unbalanced set (strict resolution, zero new imbalances introduced):

| Pattern | Vouchers resolved |
|---|---|
| Tr. A/c / Ledger Tr. A/c (per-voucher alias) | 12 — `Journal_125,126,127,195,312,362,415,480,537,566,567,666` |
| EDLI Account / EDLI (header canonicalization) | 5 — `Journal_691,694,695,696,697` |
| Employee A / Employee B salary A/c (header canonicalization) | 3 — `Journal_657,658,659` (each of these 3 vouchers carries both employees' salary legs jointly, so both pairs resolve the same 3 vouchers together, not 3 distinct sets) |
| DISCOUNT Account / DISCOUNT (header canonicalization + Pass-1 dedup) | 1 — `Sales GST_MPSS/24-25/259` |
| **Patterns 1–3 subtotal** | **21** |
| Redacted-account orphan absorption (Pattern 4) | 6 — `Journal_604,671,672,684,687,690` |
| **Total resolved** | **27** |

**2 remain, by design**: `Payment_2` and `Sales_Payment` (Pattern 5) — a single, isolated header-regex ambiguity, deliberately left unfixed per §5. This is not an oversight; the objective of this work was never to force the unbalanced count to zero, only to fix what is demonstrably safe.

---

## 9. Known Residual Issues — Deferred, Not Fixed

- **`Payment_2` / `Sales_Payment` header-regex ambiguity** (Pattern 5, §5) — single occurrence in the full 926-page population; the blast radius of touching the shared header regexes isn't justified by the evidence. Documented as a future investigation item.
- No other name-pair patterns or structural issues were found in this investigation; nothing else was bundled in.

---

## 10. New/Updated Regression Tests

`tests/test_pattern4_redacted_orphan_absorption.py` (new, 12 tests): all 6 Pattern-4-affected vouchers now balanced with zero remaining orphans; absorbed legs carry the exact expected amount/dr_cr; the set of vouchers carrying a redacted-placeholder leg is unchanged (14 / 11); synthetic-data unit tests of the absorption logic in isolation — exact match absorbs, dr_cr mismatch rejects, amount mismatch rejects, no-candidate rejects, ambiguous (2+) candidates rejects, cross-voucher isolation, multiple orphans absorbing into one candidate, and an end-to-end balance check mirroring the real `Journal_687` shape.

`tests/test_v3_9_name_pair_fixes.py`: `Tr. A/c` / `Ledger Tr. A/c` added to `PER_VOUCHER_PAIRS`; `test_no_new_unbalanced_vouchers_introduced` updated from the V3.9 baseline (29, unexplained) to the new, fully-resolved invariant (2, exactly `{Payment_2, Sales_Payment}`, with the full Patterns 1–4 explanation).

`tests/test_header_carryover_fix.py`: 3 tests updated to account for two independent, later, unrelated fixes legitimately growing some of these legs' `source_pages` past their own page (the Tr. A/c per-voucher alias for page 547, and Pattern 4's orphan absorption for pages 477/546) — each test now identifies its population by account label or page-membership rather than by an exact-string `source_pages` match that these fixes correctly, and expectedly, no longer satisfy. The true populations (14 / 11 / 29, total 54) are verified unchanged.

`tests/test_combined_report_regression.py`: `test_total_leg_count_pinned`, `test_benford_chi_square_pinned`, `test_leg_flag_counts_pinned`, `test_severity_distribution_pinned` re-pinned, each with the full per-flag decomposition documented in §7.

Full suite: **140 tests, 140 passing** (128 from V3.9 + 12 net new).

---

## 11. Preserved Architecture & Invariants

- Tally Direction Rule (`To`=Dr, `By`=Cr relative to the page's own account) — unchanged.
- Two-pass reconstruction (`collect_occurrences()` → `merge_occurrences()`) — unchanged in structure; `merge_occurrences()` gains one new, narrow, whitelist-only consolidation pass (orphan absorption) in addition to the per-voucher alias pass from V3.9.
- Shape A / Shape B voucher parsing — unchanged.
- `_ACCOUNT_NAME_CANONICALIZATION` and `_PER_VOUCHER_NAME_ALIASES` insertion points and safety bars — unchanged; entries added under the identical, previously-proven rigor.
- No fuzzy matching, no Levenshtein/similarity scoring, no manual overrides by entry_id — every merge in this codebase is still either exact-string canonicalization at a single proven insertion point, exact `entry_id`+`amount`+`dr_cr`+disjoint-pages matching, or (new in this freeze) exact `entry_id`+`amount`+`dr_cr`+unique-eligible-candidate matching for redacted-placeholder absorption.
- `Payment_2` / `Sales_Payment` header-regex ambiguity — deliberately left unfixed (§5, §9).

---

## 12. Git

**Commit**: `9aa6b3d6a9230908ffed4639cb1c2ef44da9ae03` (`C:\Users\<user>\Desktop\audit_project`, branch `master`).

**11 files changed** (53,327 insertions, 35 deletions): 4 modified (`scripts/reconstruct_ledger_entries.py`, `tests/test_header_carryover_fix.py`, `tests/test_v3_9_name_pair_fixes.py`, `tests/test_combined_report_regression.py`), 7 new (`BASELINE_CHECKPOINT_V3.10.md`, `tests/test_pattern4_redacted_orphan_absorption.py`, and the 5 files under `data/_pre_pattern123_backup/`).

**Not tracked, by existing project `.gitignore` convention**: `data/general_ledger.csv`, `data/ledgers_redacted_reconstructed_entries.csv`, `data/ledgers_redacted_reconstructed_state_events.csv`, `data/ledgers_redacted_reconstructed_unresolved.csv`, and the entire `output/` directory — these are derived/regenerated artifacts (reproducible from the raw PDF plus the scripts in this commit) and were already excluded before this freeze; this is consistent with how prior versions handled them, not a new exclusion introduced here.

**`git status` after commit**: `nothing to commit, working tree clean`.
