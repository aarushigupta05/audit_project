# Input Normalization Layer — Version Checkpoint (V3.5 Frozen)

**Date**: 2026-10-01
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.4
**Freeze basis**: Direct per-voucher verification against raw PDF source documents and an independent official source document (the firm's Tally trial balance), cross-checked two ways (Tally's own printed running totals, and a naive ground-truth regex re-scan), plus a bounded forensic audit of every downstream Rule 5 / Benford's Law change. See §2–§5.

---

## 1. Relationship to V3.4

V3.4 (`BASELINE_CHECKPOINT_V3.4.md`, commit `1ae74a9`/`c63366b`) is **not invalidated by amount or balancing errors** — it remains correct on every metric it was frozen against (the Shape B fix, voucher balancing, Rule 5 impact). V3.5 fixes a **separate, independently-discovered bug**: the blank-counterparty voucher-header bug described below, which only surfaced while reconciling the partnership firm's ledger against its official Tally trial balance.

**Trigger**: integrating the partnership-firm trial balance (`trial_balance_redacted.pdf`) surfaced a Rs.55,00,000 credit-side gap on the J & K Bank Ltd account. Investigated independently two ways (Tally's own printed cumulative running totals, and a naive regex-only re-scan of the raw PDF ignoring the sophisticated parser's shape logic) before touching any code — both confirmed the gap was real and pinned its exact source.

---

## 2. Problem Statement & Root Cause (V3.5)

**Problem:** A voucher header line with a genuinely blank/omitted counter-party name — e.g. `"By Payment 292 5,00,000.00 1,00,00,906.17 Dr"`, with nothing printed between `"By"` and the voucher type — was not being recognized as a header at all. It fell through to the generic Detail-Leg regex, which wrongly consumed the trailing running-balance figure (`1,00,00,906.17`) as the leg's "amount" and the garbled remainder (including the real amount, now embedded as text) as "particulars". This silently injected one fabricated leg into whatever voucher happened to still be active on that page — losing the real transaction (its own voucher never got created) **and** corrupting an unrelated voucher's balance at the same time.

**Mechanism:** `_WRAPPED_PARTICULARS_HEADER_RE` (which has no particulars group — it matches `To/By <vch_type> <vch_no> <remainder>` directly) *did* match these lines structurally. But the surrounding code treated every match as a "name wraps onto the next line" case, requiring the following source line to look like a plausible printed name before accepting the header at all. For a genuinely blank-particulars line, the real next line is just the next unrelated transaction — so that check failed, and the whole match (including the already-captured `vch_type`/`vch_no`) was discarded (`reconstruct_ledger_entries.py`, the `_WRAPPED_PARTICULARS_HEADER_RE` handling block in `collect_occurrences()`).

**Why V3.4's verification didn't catch it:** this bug creates a plausible-looking three-legged "voucher" (the real two legs plus one garbage leg) that still parses as a complete transaction, just an unbalanced one — and unbalanced vouchers already existed in the dataset for other legitimate reasons (partial postings, cross-page splits), so a raw unbalanced-count check doesn't flag it as anomalous on its own. It only surfaces against an independent source document (the trial balance) or by directly reading the raw PDF line.

**Scope:** 6 instances dataset-wide (6 blank-counterparty header lines → 6 recovered real transactions, contaminating 5 other vouchers — one voucher, Receipt_1177, was hit twice).

---

## 3. The Fix

`reconstruct_ledger_entries.py`, the `_WRAPPED_PARTICULARS_HEADER_RE` handling block: when the next-line continuation check fails, the match is no longer discarded. It's accepted as a legitimate blank-particulars header — `particulars` is set to `""` explicitly, and `h_date`/`direction`/`vch_type`/`vch_no`/`remainder` (already captured from the regex match) are kept. `particulars == ""` naturally falls into the existing Shape B branch (post the V3.4 fix, that branch already attributes to `current_account`), and a dedicated `"[no counterparty name printed in source: <vch_type> <vch_no>]"` narration note is added, since there's no printed name to preserve the way the V3.4 fix preserves one.

**Scope discipline:** nothing else in `collect_occurrences()` or `merge_occurrences()` was touched. The still-open 3-page header-carryover bug (Laptop/Pollution Fees, pages 477/546/547) and the Sales/Round-Off-Discount reconciliation residuals are **unrelated** and untouched by this fix — confirmed below, their mismatches are unchanged except where this fix's own 6 recovered legs happen to complete vouchers that were already flagged there (see §4).

---

## 4. Validated Metrics

Verified by exact key-for-key diff (`entry_id`, `dr_cr`, `amount`, `source_pages`) between the pre-fix and post-fix reconstructed CSVs:

| Metric | V3.4 | V3.5 | Changed? |
|---|---|---|---|
| Canonical vouchers | 6,216 | 6,216 | No |
| Total ledger rows | 17,128 | 17,128 | No (6 removed, 6 added — a relocation, not an addition) |
| Rows removed (garbage legs) | — | **6** | fabricated running-balance amounts, summing to Rs.4,45,72,158.17 |
| Rows added (recovered legs) | — | **6** | real J & K Bank Ltd credit legs, summing to exactly **Rs.55,00,000.00** |
| Vouchers flipped `entry_balanced` False→True | — | **11** | 5 decontaminated (Payment_291, Receipt_837, Receipt_1177, Payment_419, Receipt_852) + 6 newly-completed (Payment_292/294/299/420/428/429) |
| Vouchers flipped True→False | — | **0** | — |
| Other rows changed anywhere in the 926-page population | — | **0** | confirmed by full diff |

Note: the 6 newly-completed vouchers (Payment_292/294/299/420/428/429) already existed pre-fix as a single unbalanced Dr leg on the Laptop/Pollution Fees pages (477/546) — the subject of the still-open header-carryover bug (§6). This fix gave each of them their missing Cr leg, without touching the carryover bug itself.

**Trial balance reconciliation** (`reconcile_trial_balance.py`): J & K Bank Ltd now reconciles exactly (credit diff = Rs.0.00, debit diff ~1e-7 rounding) — moved from the mismatched bucket to clean. Mismatches: 6 → 5 (Laptop and Pollution Fees remain, from the unrelated, still-open carryover bug; Sales and Round Off/Discount remain, also unrelated — see V3.4-era `reconcile_trial_balance.py` docstring).

---

## 5. Downstream Impact — Rule 5 and Benford's Law

- **`total_legs_flagged`: 1415 → 1419 (+4)**. All 4 are the new J & K Bank Ltd legs for Payment_292 (Rs.5,00,000), Payment_294 (Rs.7,50,000), Payment_299 (Rs.12,50,000), and Payment_429 (Rs.9,00,000) — each flagged by Rule 1 (round-number bias) only. Payment_420 (Rs.13,65,000) and Payment_428 (Rs.7,35,000) are not round numbers under Rule 1's thresholds and don't flag.
- **Severity**: HIGH 624→627 (+3: Payment_292, Payment_294, Payment_429), CRITICAL 359→360 (+1: Payment_299). LOW/MEDIUM unchanged.
- **`multi_signal`**: unchanged at 8 — none of the 4 new flags coincides with another voucher on `(date, account, amount, direction)`, so none trigger Rule 5 as well.
- **Benford's Law**: chi-square 288.29 → 287.15 — expected, since the population of amounts changed (6 fabricated numbers removed, 6 real numbers added).
- **Tax reconciliation**: unchanged (0 mismatches) — unaffected, as expected (this fix is scoped to the partnership-firm ledger, not the individual's filing).

`test_leg_flag_counts_pinned`, `test_severity_distribution_pinned`, and `test_benford_chi_square_pinned` in `tests/test_combined_report_regression.py` were re-pinned to these values on the strength of this audit.

---

## 6. Known Residual Issues — Deferred, Not Fixed

Unchanged from V3.4, confirmed still isolated and untouched by this fix:

- **FEES narration-continuation bug** (confined to narration, doesn't corrupt the account column) — still deferred, still pinned by `test_fees_bug_confined_to_narration_does_not_corrupt_account_column`.
- **3-page header-carryover bug** (Laptop/Pollution Fees, pages 477/546/547) — still open. This fix completed 6 vouchers that were partly victims of it, but did not address the carryover mechanism itself; the Laptop and Pollution Fees trial-balance mismatches remain.
- **Sales mismatch** (~Rs.8.27 crore overshoot even after name-aliasing) — still open, needs its own investigation.
- **Round Off/Discount residual** (debit ~2x the trial balance figure even after aliasing) — still open, low priority.

---

## 7. New Regression Tests

`tests/test_blank_particulars_fix.py` (14 tests): the 6 known recovered legs (parametrized, checking both account/amount and that the amount is not one of the old garbage running-balance figures), a sum check that the 6 legs total exactly Rs.55,00,000, the 5 contaminated vouchers (parametrized) confirming no garbage leg remains and each is now balanced with exactly 2 legs, a population-wide regex guard that no row in `general_ledger.csv` has an account value that looks like an un-parsed voucher header line, and a direct check that `reconcile_trial_balance.py` now reports J & K Bank Ltd as clean.

Full suite: 79 tests, 79 passing.

---

## 8. Preserved Architecture & Invariants

*(Unchanged from V3.1/V3.3/V3.4 — reproduced for completeness)*

1. **Two-Pass Ledger Design:** Pass 1 parses raw occurrences; Pass 2 does conservative cross-account deduplication and corroboration. This fix only changes which lines Pass 1 recognizes as a header at all — it does not change the two-pass structure.
2. **Accounting Invariants:** Direction Rule: `To` = Debit (Dr), `By` = Credit (Cr) — describing current_account's own movement. A blank-particulars header is still subject to this rule exactly like any Shape B header once recognized.
3. **Data Preservation & Anti-Data-Loss:** the printed counter-party name is preserved as narration when present (V3.4); when it's genuinely absent, this fix records that fact explicitly (`"[no counterparty name printed in source: ...]"`) rather than silently losing the leg to a generic fallthrough.

---

## 9. Git

**This freeze's commit**: recorded immediately after this document was written (commit hash appended below once committed).

**Commit hash**: `e0535a3` (3 files changed, 359 insertions, 9 deletions)
