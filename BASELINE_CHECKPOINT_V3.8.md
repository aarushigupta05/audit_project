# Input Normalization Layer — Version Checkpoint (V3.8 Frozen)

**Date**: 2026-10-02
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.7
**Freeze basis**: A narrow, safe, evidence-backed fix for CGST/SGST cross-page name fragmentation — discovered while investigating a separate, broader 77-voucher issue, verified with the identical proof structure as V3.7 (both forms are genuine continuous header-leg ledger runs; the variant form has zero detail-leg occurrences; zero amount conflicts across the full 779+779 population) — plus a complete, honest classification of 7 OTHER name-pairs found in the same investigation that do **not** meet the safety bar and were deliberately left unfixed.

---

## 1. Relationship to V3.7

V3.7 (`BASELINE_CHECKPOINT_V3.7.md`, commit `f2b1bcb`/`6454b8c`) is **not invalidated** — it remains correct on every metric it was frozen against. V3.7 §6 logged a new deferred item: 77 vouchers that flipped from `entry_balanced=True` to `False` when the V3.7 fix landed, due to an unrelated, unmasked customer/debtor name-fragmentation issue. This checkpoint is the result of investigating that item, per the user's explicit instruction.

**Important — read this before anything else**: this fix does **not** resolve the 77-voucher issue it was sent to investigate. It resolves a **different, larger, independently-discovered** problem (CGST/SGST double-counting, affecting 1,558 rows and 766 previously-unbalanced vouchers elsewhere in the dataset) that surfaced as a side effect of the same investigation. The original 77 vouchers remain exactly as V3.7 left them — see §6.

---

## 2. Investigation — Full Classification of the 77-Voucher Issue

Every one of the 77 vouchers was traced to a duplicate-amount, different-account-name pair on its Dr side (the same mechanism class as Sales/Purchase/Round-Off-Discount: two spellings of what might be the same real account, never corroborating because `merge_occurrences()` keys on exact `(particulars, dr_cr)`). Exactly **9 distinct name-pairs** were involved across the 77. Each was individually verified against the same evidentiary bar as V3.7 (does the variant form ever appear as a detail leg — i.e., free-form printed text independent of `current_account` — which the header-only insertion point cannot reach; do both forms correspond to one genuine, continuous ledger run; are there any amount conflicts).

**Result — only 1 of the 9 pairs is safe:**

| Pair | Classification | Vouchers of the 77 | Full dataset-wide population |
|---|---|---|---|
| `CGST` / `CGST Account` | **SAFE** — both header-leg only, continuous ledger run (pages 172, 173–251) | 1 | 779 cross-variant vouchers |
| `SGST` / `SGST Account` | **SAFE** — same structure (pages 787, 788–866) | 1 (same voucher) | 779 cross-variant vouchers |
| `ARO OFFICE B-1204` / `...ROYAL PALMS C/O NAGBANI` | NOT SAFE — short form has zero header pages anywhere; exists only as detail-leg counter-party text | 10 | not quantified (out of scope) |
| `CAMBRIDGE` / `CAMBRIDGE INTERNATIONAL (GENSET A/C)` | NOT SAFE — same reason | 7 | — |
| `PRINCIPAL MHAC VEH. ACCOUNT` / `...VEH.ACCOUNT` | NOT SAFE — same reason | 26 | — |
| `Principal MHAC School` / `...School Nagbani` | NOT SAFE — same reason | 10 | — |
| `SGF INFRA PVT LTD` / `...GURHA BAKSHI NAGAR` | NOT SAFE — same reason | 10 | — |
| `Veh. Running &` / `...& Maintenance Exp` | NOT SAFE — same reason | 1 | — |
| `KC EDU.SOCIETY( GENSET AC` / `...(GENSET AC` | NOT SAFE — both forms DO have their own header pages (470–471, 469), but the legs driving these 10 vouchers come from unrelated detail-leg mentions elsewhere, not from those header pages; continuity unverified | 10 | — |
| `JANDIYAL ELECTRONICS` / `...PARTNER` | NOT SAFE — **affirmative evidence of being two different real accounts** (a firm account vs. a distinct "PARTNER" account), each with its own header page (383, 382); must not be merged | 3 | — |

(Counts sum to 77 + 1 overlap, since the CGST/SGST voucher also involves a second, unsafe driver and so is not fully resolved — see §6.)

**Why the "NOT SAFE" pairs fail the bar, precisely**: for Sales/Purchase/Round-Off-Discount/CGST/SGST, canonicalizing inside `_detect_page_header_and_account()` works because every single occurrence of the variant spelling, across the full population, is a *header leg* (derived from `current_account`). For the 6 "detail-leg-only" pairs above, the *short* form never has its own header page anywhere in the 926-page document — it exists solely as free-form text captured by `_LEG_RE`, parsed independently of `current_account`. The proven insertion point cannot reach that text at all, and there is no document convention (unlike the proven first-page/continuation-page split) establishing that the short form deterministically corresponds to one specific long form rather than being a coincidental resemblance. Canonicalizing it would require either touching detail-leg parsing (a materially different, never-validated insertion point) or an identity assumption not backed by the same page-continuity evidence — exactly what the user's instruction ruled out ("do not use fuzzy matching or guess account identities").

For KC EDU.SOCIETY and JANDIYAL, both forms have genuine header pages, but the specific vouchers driving the 77-flip are corroborated through *unrelated* detail-leg mentions elsewhere, not through those header pages — so even the superficial resemblance to the CGST/SGST pattern doesn't hold up under inspection. JANDIYAL additionally carries affirmative evidence (the word "PARTNER") that it is a distinct account, not a name variant.

---

## 3. The Fix (safe subset only)

Extended `_ACCOUNT_NAME_CANONICALIZATION` in `reconstruct_ledger_entries.py` with 2 new entries:
```python
"CGST Account": "CGST",
"SGST Account": "SGST",
```
Same insertion point as V3.7 (the return value of `_detect_page_header_and_account()`), same rigor: verified before implementation that all 779+779 cross-variant legs carrying the variant form are header legs (0 detail legs), and zero amount conflicts when consolidated.

**Scope discipline**: none of the 7 excluded pairs were touched. FEES, page 548, and the frozen V3.5–V3.7 checkpoints are untouched.

---

## 4. Validated Metrics

| Metric | V3.7 | V3.8 | Changed? |
|---|---|---|---|
| Canonical vouchers | 6,216 | 6,216 | No |
| Total GL rows | 16,320 | 14,762 | **Yes — exactly −1,558** (779 CGST + 779 SGST, matches quantified population exactly) |
| Removed rows not CGST Account/SGST Account | — | 0 | confirmed by full key-level diff |
| Added rows with an unexpected account | — | 0 | (0 new rows at all — the canonical "CGST"/"SGST" rows already existed from detail-leg reprints; header-leg occurrences corroborate into them) |
| New amount conflicts | — | 0 | zero `leg_amount_mismatch_across_occurrences` entries |
| Orphan/unresolved line count | 579 / 22 | 579 / 22 | No |
| Entries flipped unbalanced → balanced | — | **766** | Large, independent win — see §6 |
| Entries flipped balanced → unbalanced | — | **0** | No regressions |

**Trial balance reconciliation**: 82/82 matched accounts clean. CGST and SGST each reconcile to exactly ₹0 diff, each now matched via only its canonical name. This closes a reconciliation gap that was previously **invisible** — `reconcile_trial_balance.py` had no CGST/SGST alias entry at all before this fix, so it only ever summed the "CGST"/"SGST" (detail-leg-reprint) rows and silently never checked the "...Account" (header-leg) rows against the trial balance.

---

## 5. Downstream Impact — Rule 5 and Benford's Law (re-pinned)

**Benford's Law**: chi-square 296.93 → 227.06. Same mechanical cause as V3.7: the 1,558 consolidated rows were each counted twice before this fix (once per spelling, identical amount); this is a correction of inflated duplicate counts. Still far above the deviation threshold both before and after.

**Rule 5/2 flags**: 1,294 → 1,282 (−12). Full key-level decomposition (`entry_id`, `account`, `amount`, `severity`, `source_rules`): 12 flags removed, 0 added, zero unexplained — every removed flag's account is `CGST Account` or `SGST Account`, every one LOW severity, every one `duplicate_transaction`-only. Multi-signal count and exact entry list unchanged (8, identical to V3.7).

Re-pinned in `tests/test_combined_report_regression.py` only after this decomposition was produced and reviewed.

---

## 6. The Original 77-Voucher Issue — Status After This Fix

**Resolved: 0 of 77.** Verified directly: all 77 vouchers from `tests/fixtures_flip77_entry_ids.txt` remain `entry_balanced=False` after this fix. The one voucher that does involve CGST/SGST as a partial driver also has a second, unsafe driver, so it is not fully resolved either.

**What this fix resolved instead**: 766 *other* previously-unbalanced vouchers elsewhere in the dataset (886 → 120 total unbalanced vouchers), all independently confirmed to involve CGST/SGST double-counting directly, with zero exceptions and zero regressions (0 vouchers flipped the other way).

**What remains open for the 77-voucher issue** — recorded as a refined, split deferred item (see §7): the 7 unsafe pairs above. A future fix, if pursued, would need:
- For the 6 pure detail-leg-truncation pairs: either (a) independent corroborating evidence establishing the truncation is a deterministic, document-wide print convention (e.g. a fixed column-width rule, verified across many more examples than these 6) rather than per-case resemblance, or (b) a different, more conservative approach (e.g. flagging for manual CA review rather than auto-merging).
- For KC EDU.SOCIETY: Carried-Over/Brought-Forward continuity evidence between pages 469 and 470–471 (the same technique used for page 547/548 in V3.6), to determine whether it is a genuine continuation or two separate accounts, before any action.
- For JANDIYAL ELECTRONICS/PARTNER: should likely be left permanently separate — the evidence points to two different real accounts, not a bug.

---

## 7. Known Residual Issues — Deferred, Not Fixed

- **FEES narration-continuation bug** — still deferred, still cosmetic-only.
- **77-voucher customer/debtor name-fragmentation issue** — refined per §6 above: 0 of 77 resolved by this fix; 7 distinct name-pairs remain, classified by exact reason for exclusion. Any future work here needs the specific additional evidence listed in §6, not a blanket "do the same thing again."
- **Page 548's `"Ledger Tr. A/c"` label** — unchanged, still out of scope (V3.6).

---

## 8. New Regression Tests

`tests/test_cgst_sgst_canonicalization_fix.py` (9 tests): the 2 new mappings are present; the 7 excluded pairs are explicitly NOT in the dict (guards against silent scope creep); no CGST Account/SGST Account survives in the output; the 7 excluded pairs' both variants still present and untouched; a concrete cross-variant voucher (`Sales GST_MPSS/24-25/19`) collapses to one CGST leg and one SGST leg with all 5 source pages preserved; narration preserved; CGST/SGST reconcile exactly against the trial balance; TDS Receivable HPCL/Rent Expenses HPCL still unaffected; **all 77 originally-flagged vouchers remain unbalanced** (explicit proof this fix didn't touch them).

`tests/fixtures_flip77_entry_ids.txt` (new): the exact 77 entry_ids, committed as a data fixture so the above test is reproducible without re-deriving the V3.6→V3.7 diff.

`tests/test_account_canonicalization_fix.py`: the test asserting the canonicalization dict equals exactly the 3 V3.7 entries was changed to a subset check (a later, separately-verified fix may add more); the row-count-reduction test changed from an exact equality to an upper bound, for the same reason (mirrors the fix already applied to `test_header_carryover_fix.py` in V3.7).

`tests/test_combined_report_regression.py`: `test_total_leg_count_pinned`, `test_benford_chi_square_pinned`, `test_leg_flag_counts_pinned`, `test_severity_distribution_pinned` re-pinned to this fix's validated values.

Full suite: 112 tests, 112 passing.

---

## 9. Preserved Architecture & Invariants

*(Unchanged from V3.1–V3.7 — reproduced for completeness)*

1. **Two-Pass Ledger Design:** this fix only extends what `_detect_page_header_and_account()` returns for 2 more specific strings; the two-pass dedup/corroboration structure is otherwise untouched.
2. **Accounting Invariants:** Direction Rule unaffected.
3. **Data Preservation & Anti-Data-Loss:** no leg is ever discarded — duplicate rows are consolidated (source pages merged, amount verified identical), never dropped.

---

## 10. Git

**This freeze's commit**: recorded immediately after this document was written (commit hash appended below once committed).

**Commit hash**: `b0c8d64` (6 files changed, 504 insertions, 23 deletions)
