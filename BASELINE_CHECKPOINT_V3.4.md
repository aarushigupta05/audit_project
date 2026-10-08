# Input Normalization Layer — Version Checkpoint (V3.4 Frozen)

**Date**: 2026-10-01
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.3
**Freeze basis**: Direct per-voucher verification against raw PDF source documents (4 known cases + full-population invariant check), plus a bounded forensic audit of every downstream Rule 5 flag change (42 of 42 explained). See §2–§5.

---

## 1. Relationship to V3.3

V3.3 (`BASELINE_CHECKPOINT_V3.3.md`, commit `598d80d`) is **not invalidated by amount or balancing errors** — it remains correct on every metric it was frozen against (voucher counts, balancing, the 5 fused-header corruption cases). V3.4 fixes a **separate, independently-discovered bug**: the account/direction *attribution* bug described below, which V3.3's verification process could not have caught because it is invisible to voucher-balance checks (see §2).

**Trigger**: discovered while building the partnership-firm trial-balance reconciliation adapter (document-integration phase of the roadmap). Comparing the firm's official Tally trial balance against the reconstructed ledger surfaced 53 account-level mismatches, many showing exact Dr/Cr swaps — which traced back to this bug, not to the trial-balance adapter.

---

## 2. Problem Statement & Root Cause (V3.4)

**Problem:** A "Shape B" voucher line — a simple `"To/By <Name> ... amount Dr/Cr"` header line, as opposed to a `"(as per details)"` multi-leg ("Shape A") voucher — was attaching its leg to `<Name>`, the **printed counter-party**, instead of to the ledger page's own subject account (`current_account`).

Example: on the "Audit Fee" ledger page, the line `"To AUDIT FEE PAYABLE ... 35,000 Dr"` was reconstructed as a leg on account **`AUDIT FEE PAYABLE`** (Dr), when it should be a leg on account **`Audit Fee`** (Dr) — `AUDIT FEE PAYABLE` is the counter-party, not this page's account.

**Mechanism:** `_classify_header_amounts()` derives `dr_cr` purely from the `To`/`By` token, which by the project's Direction Rule (§9) always describes `current_account`'s own movement. The Shape A branch in `collect_occurrences()` correctly paired this direction with `current_account`. The Shape B (`else`) branch paired it with the printed text instead — a one-line bug (`reconstruct_ledger_entries.py`, previously line 362).

**Why V3.3's verification didn't catch it:** every check used so far (per-voucher Dr=Cr balance, the 25-sample freeze verification, the 52-test pytest suite) is insensitive to *which* account an amount is attributed to, only to whether debits equal credits for the voucher — which remains true even when both legs' labels are swapped. It only surfaces against an independent source (the trial balance) or by directly reading two corroborating ledger pages of the same voucher.

**Scope:** 9,802 of 14,605 header legs (67.1%) were affected — every Shape B leg, unconditionally. Shape A legs (4,803) were confirmed unaffected.

---

## 3. The Fix

`reconstruct_ledger_entries.py`, Shape B branch: now uses `current_account`, mirroring the Shape A branch. The printed counter-party name is **not discarded** — it's preserved as a `"[counterparty per source line: ...]"` narration note on the voucher, so the source document's own wording is never lost even for the minority of counter-parties with no ledger page of their own.

A second-order fix was needed alongside this: the pre-existing wrapped-line-continuation logic (for printed names spanning two lines, e.g. `"ELECTRICITY BILL"` → `"PAYABLE"`) assumed a header leg's `particulars` held the printed text. Once that became `current_account`, continuation lines started corrupting the account name itself (e.g. `"Cash"` → `"Cash PAYABLE"`). Fixed by redirecting continuation onto a private `_counterparty` buffer for Shape B legs, finalized into narration after each page's parsing completes and before the account label is ever written out.

**Scope discipline:** `pdf_to_csv_converter.py` was not touched. No rule script's logic was changed (`rule_duplicate_transaction.py` received a documentation-only update, see §5). The separate, smaller "FEES" narration-continuation bug (an orphaned single-word all-caps continuation line getting glued onto the wrong field) was identified but deliberately **not fixed here** — it's isolated to narration text only (see §6) and left for a separate change.

---

## 4. Validated Metrics — Unchanged from V3.3

The fix is a pure relabeling: it changes *which account* a leg is attributed to, never an amount, never which legs exist, never voucher balancing. Verified by exact key-for-key diff (`entry_id`, `dr_cr`, `amount`, `source_pages`) between the pre-fix and post-fix reconstructed CSVs:

| Metric | V3.3 | V3.4 | Changed? |
|---|---|---|---|
| Canonical vouchers | 6,216 | 6,216 | No |
| Balanced vouchers | 5,078 | 5,078 | No |
| Unbalanced vouchers | 1,138 | 1,138 | No |
| Named legs | 17,106 | 17,106 | No |
| Orphan legs | 22 | 22 | No |
| Rows with changed `entry_balanced` flag | — | **0** | No |
| Rows added/removed | — | **0 / 0** | No |
| Rows with changed `particulars` (account) label | — | **9,802** | **Yes — the fix** |

9,802 is an exact match to the independently-quantified Shape B population from the investigation phase.

---

## 5. Downstream Impact — Rule 5 (Duplicate Transaction Detection)

Rule 5 matches on `(date, account, amount, direction)`, so it was the one rule directly exposed to the relabeling. Full before/after audit (not just the aggregate count):

- **32 flags removed, 10 flags added, net -22** (`total_legs_flagged`: 1437 → 1415)
- **All 42 changes traced to a specific leg** whose account the fix corrected — 20 removed + 9 added directly (the leg's own account changed), 12 removed + 1 added indirectly (a sibling leg in the same match group was corrected, breaking or creating the match)
- **Zero unexplained residue**
- The 10 added flags are not new false positives — they're genuine, previously-hidden coincidental matches (e.g. multiple receipts landing in the same bank account on the same day for the same amount) that the mislabeling was masking by scattering each voucher's correct-side label across different counter-party names
- The repeat-vendor pair documented in `rule_duplicate_transaction.py` as a verified known limitation no longer fires — see the docstring update dated 2026-10-01 for why

`test_leg_flag_counts_pinned` and `test_severity_distribution_pinned` in `tests/test_combined_report_regression.py` were re-pinned to the new values (1415 / severity `{LOW:242, MEDIUM:190, HIGH:624, CRITICAL:359}`) on the strength of this audit.

Benford's Law (chi-square 288.29), tax reconciliation (0 mismatches), and the 5 target-voucher amounts from V3.3 §4 are **all unchanged** — only account-name-dependent logic was affected.

---

## 6. Known Residual Issue — Deferred, Not Fixed

**FEES narration-continuation bug**: an orphaned single-word all-caps continuation line (e.g. a lone `"FEES"`) is incorrectly glued onto the preceding leg's text via `_PARTICULARS_CONTINUATION_TOKENS` instead of being treated as narration. Confirmed still present (e.g. `"Caliberation Charges FEES"`, `"Cash FEES"`), but as an incidental consequence of the Shape B fix's narration-buffer restructuring, it is now **confined entirely to the narration column** — it no longer corrupts the `account`/`particulars` field used by any rule or reconciliation. Pinned by `test_fees_bug_confined_to_narration_does_not_corrupt_account_column` in `tests/test_shape_b_fix.py`. Left for a separate, dedicated fix.

---

## 7. New Regression Tests

`tests/test_shape_b_fix.py` (13 tests): the 4 known voucher pairs (8 parametrized assertions), a source-level invariant re-parsing pages 1–325 directly against `collect_occurrences()` (every header leg's `particulars` must equal its own page's `current_account`), two `merge_occurrences()` unit tests (a Dr/Cr pair of a simple voucher must not falsely merge; a genuinely repeated leg across pages must corroborate), a pin that 9,808 counter-party names are preserved in narration rather than discarded, and the FEES-isolation test from §6.

Full suite: 65 tests, 65 passing.

---

## 8. Preserved Architecture & Invariants

*(Unchanged from V3.1/V3.3 — reproduced for completeness)*

1. **Two-Pass Ledger Design:** Pass 1 parses raw occurrences without intra-occurrence deduplication (except pruning redundant synthetic header legs when an explicit detail row already captures the same leg — this pruning pass now also runs after Shape B's narration finalization, see §3). Pass 2 does conservative cross-account deduplication and corroboration.
2. **Accounting Invariants:** Direction Rule: `To` = Debit (Dr), `By` = Credit (Cr) — describing current_account's own movement (this is the invariant V3.4 enforces correctly for Shape B for the first time). Trailing Dr/Cr is running balance direction, not transaction leg direction.
3. **Data Preservation & Anti-Data-Loss:** Orphan legs never merged into one another. Narration contextually captured and attached to voucher records — now including preserved Shape B counter-party names. Low-scoring table groups in the generic converter quarantined, not deleted.

---

## 9. Git

**This freeze's commit**: recorded immediately after this document was written (commit hash appended below once committed).

**Commit hash**: `1ae74a9` (5 files changed, 442 insertions, 8 deletions)
