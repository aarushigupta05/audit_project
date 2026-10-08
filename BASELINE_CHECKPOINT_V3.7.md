# Input Normalization Layer — Version Checkpoint (V3.7 Frozen)

**Date**: 2026-10-01
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.6
**Freeze basis**: Explicit, narrow account-name canonicalization, verified before implementation to be the single sufficient insertion point (zero detail-leg exceptions across the full 808-voucher population), validated to bring all three targeted trial-balance mismatches to exactly ₹0, with a complete before/after decomposition of every Rule 5/Benford change and zero unexplained residue. See §2–§5.

---

## 1. Relationship to V3.6

V3.6 (`BASELINE_CHECKPOINT_V3.6.md`, commit `c787b01`/`50ab909`) is **not invalidated** — it remains correct on every metric it was frozen against (the header-carryover fix, Laptop/Pollution Fees). V3.7 fixes a **separate, independently-discovered bug**: cross-page account-name fragmentation for Sales, Purchase, and Round Off/Discount, unrelated to header-carryover.

**Trigger**: item #2 on the user's ordered task list (Sales mismatch), which on investigation turned out to be the same root cause as item #3 (Round Off/Discount residual) and a previously-misdiagnosed Purchase "rounding" residual.

---

## 2. Problem Statement & Root Cause (V3.7)

Three accounts print under two different name strings in the source ledger PDF — the name on the account's first page, and a different spelling on its own continuation pages:

| Canonical (first page) | Variant (continuation pages) |
|---|---|
| `Sales` | `Sales Account` |
| `Purchase` | `Purchase Account` |
| `Round Off/Discount` | `Round Off /Discount` (note the embedded space) |

This is not merely cosmetic: `merge_occurrences()` dedups/corroborates legs by `(particulars, dr_cr)`. A voucher genuinely corroborated from pages printing *both* spellings was treated as two separate, non-corroborating legs carrying the same amount — real double-counting, not just a comparison-script aliasing gap.

**Quantified before implementation, confirmed exactly, zero residue:**
- Sales: 391 cross-variant vouchers — the entire ~₹8.27 crore Sales trial-balance mismatch
- Round Off/Discount: 416 cross-variant vouchers — the entire residual
- Purchase: 1 cross-variant voucher — the ₹1,203.60 residual previously (incorrectly) characterized as routine rounding

TDS Receivable HPCL and Rent Expenses HPCL also fragment across pages (`TDS Receivable HPCL`/`TDS Receivable`, `Rent Expenses HPCL`/`Rent Expenses`) but have **zero** vouchers touching both variants — they already reconcile exactly via simple aliasing in `reconcile_trial_balance.py` and are deliberately excluded from this fix.

**Pre-implementation verification (not assumed):** re-parsed every one of the 808 cross-variant legs carrying the longer/variant spelling and confirmed 100% are header legs (`particulars` derived from `current_account`, set inside `_detect_page_header_and_account()`) — 0% are detail legs (verbatim printed sub-line text via `_LEG_RE`, independent of `current_account`). This proves a single insertion point is sufficient; no detail-leg text anywhere in the real population needed separate handling.

---

## 3. The Fix

New dict `_ACCOUNT_NAME_CANONICALIZATION` in `reconstruct_ledger_entries.py`:
```python
_ACCOUNT_NAME_CANONICALIZATION = {
    "Sales Account": "Sales",
    "Purchase Account": "Purchase",
    "Round Off /Discount": "Round Off/Discount",
}
```
Applied to the return value of `_detect_page_header_and_account()` only, immediately before it returns — reached by every header leg, before `merge_occurrences()` ever builds its dedup key. Not a generic fuzzy-match or whitespace-normalization rule: exactly 3 explicit entries.

**Scope discipline**: TDS Receivable HPCL / Rent Expenses HPCL untouched (both variants still present in the GL, still reconcile exactly). No detail-leg parsing path touched. FEES and the frozen V3.5/V3.6 checkpoint docs untouched.

---

## 4. Validated Metrics

| Metric | V3.6 | V3.7 | Changed? |
|---|---|---|---|
| Canonical vouchers | 6,216 | 6,216 | No |
| Total GL rows | 17,128 | 16,320 | **Yes — exactly −808** (391 + 416 + 1, matches quantified population exactly) |
| Removed rows not a variant-name account | — | 0 | confirmed by full key-level diff |
| Added rows not a canonical-name account | — | 0 | confirmed by full key-level diff |
| Rows with changed amount (shared keys) | — | 0 | No amount ever changed, only consolidated |
| New amount conflicts | — | 0 | zero `leg_amount_mismatch_across_occurrences` entries |
| Orphan/unresolved line count | 579 / 22 | 579 / 22 | No |

**Trial balance reconciliation** (`reconcile_trial_balance.py`): 82/82 matched accounts clean (within ₹1.00). Sales, Purchase, and Round Off/Discount each reconcile to **exactly ₹0** diff, each now matched via only its canonical name (`variants_used` = single-element list). TDS Receivable HPCL and Rent Expenses HPCL remain clean with **both** variants still present, confirming they were never touched.

### New finding — entry-balance unmasking (deferred, not fixed)

77 vouchers flipped from `entry_balanced=True` to `False`. Investigated individually, all 77: each has a **separate, unrelated** customer/debtor name-fragmentation issue on its Dr side (e.g. `"ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI"` vs `"ARO OFFICE B-1204"` — the same customer printed under two names across pages, a different mechanism from the three accounts this fix targets). Pre-fix, that Dr-side duplication coincidentally equaled the Cr-side Sales/Round-Off duplication this fix removed, so the voucher appeared balanced by two bugs cancelling out. Confirmed for all 77: the non-fix-related legs are byte-identical before and after.

**Decision (user, 2026-10-01): deferred, not investigated or fixed as part of V3.7.** Recorded as a new open item — see §6. The 77 vouchers are left exactly as the fix naturally produces them; no fuzzy matching or automatic customer-name merging was attempted.

---

## 5. Downstream Impact — Rule 5 and Benford's Law (re-pinned)

**Benford's Law**: chi-square 287.15 → 296.93. Benford counts every GL row once; the 808 consolidated legs were previously counted *twice* each at the identical amount, so this is a correction of inflated duplicate counts, not new noise. Still far above the deviation threshold (15.5) both before and after — the dataset-level conclusion is unchanged.

**Rule 5/2 flags**: 1,419 → 1,294 (−125). Full key-level decomposition (`entry_id`, `account`, `amount`):
- 201 flags removed, 76 added — **0 unexplained** in either direction; every changed flag's account is one of `{Sales, Sales Account, Purchase, Purchase Account, Round Off/Discount, Round Off /Discount}`.
- 1,214 flags common to both runs — **0** changed severity, **0** changed `source_rules`.
- Multi-signal count: 8 → 8, identical entry list (`Receipt_19/20/87/88`, `Sales Cash_MPSS/24-25/814` group).
- Severity: CRITICAL 360→360 (unchanged), HIGH 627→627 (unchanged), MEDIUM 190→161 (−29, consolidated duplicate flags), LOW 242→146 (−96, consolidated duplicate flags).

Re-pinned in `tests/test_combined_report_regression.py` only after this full decomposition was produced and reviewed, per the user's explicit instruction not to re-pin immediately.

---

## 6. Known Residual Issues — Deferred, Not Fixed

- **FEES narration-continuation bug** — still deferred, still cosmetic-only.
- **Customer/debtor name fragmentation causing duplicate Dr-side legs** (new, surfaced by this fix) — the same customer/debtor account prints under two name strings across pages (distinct from the Sales/Purchase/Round-Off-Discount mechanism fixed here), currently manifesting as 77 vouchers with `entry_balanced=False`. Explicitly deferred — not to be investigated until V3.7 is frozen.
- **Page 548's `"Ledger Tr. A/c"` label** — unchanged, still out of scope (V3.6).

---

## 7. New Regression Tests

`tests/test_account_canonicalization_fix.py` (10 tests): the canonicalization dict is exactly the 3 specified mappings and no more; no variant name survives anywhere in the output; TDS Receivable HPCL / Rent Expenses HPCL both variants still present; a Sales cross-variant voucher collapses to one corroborated leg with all source pages preserved; the same voucher's Round Off/Discount leg collapses correctly; narration is preserved after consolidation; a voucher corroborated via detail legs (not just header legs) also collapses correctly; the exact population-size reduction (808) is pinned; Sales/Purchase/Round Off/Discount reconcile exactly against the trial balance.

`tests/test_header_carryover_fix.py`: the previous `test_total_row_count_and_balancing_unchanged_by_this_fix` (which asserted an absolute, dataset-wide row count against live data — a test-design flaw that any later row-count-changing fix would break regardless of correctness) replaced with two tests scoped to what the V3.6 fix is actually responsible for: the header-carryover population is exactly 40 rows (14+11+15 across pages 477/546/547), and no other page is ever touched by that fix's labels.

`tests/test_combined_report_regression.py`: `test_total_leg_count_pinned`, `test_benford_chi_square_pinned`, `test_leg_flag_counts_pinned`, `test_severity_distribution_pinned` re-pinned to this fix's validated values, each with a docstring explaining the shift.

Full suite: 103 tests, 103 passing.

---

## 8. Preserved Architecture & Invariants

*(Unchanged from V3.1–V3.6 — reproduced for completeness)*

1. **Two-Pass Ledger Design:** this fix only changes what `_detect_page_header_and_account()` returns for 3 specific strings; the two-pass dedup/corroboration structure is otherwise untouched.
2. **Accounting Invariants:** Direction Rule unaffected — this fix only changes which canonical name a header leg's `particulars` carries, never the Dr/Cr direction or amount.
3. **Data Preservation & Anti-Data-Loss:** no leg is ever discarded by this fix — a cross-variant voucher's two name-variant legs are *consolidated* (source pages merged, amount verified identical), never dropped. Narration is preserved through consolidation.

---

## 9. Git

**This freeze's commit**: recorded immediately after this document was written (commit hash appended below once committed).

**Commit hash**: `f2b1bcb` (5 files changed, 482 insertions, 16 deletions)
