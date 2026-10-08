# Input Normalization Layer — Version Checkpoint (V3.11 Frozen)

**Date**: 2026-10-03
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.10
**Freeze basis**: A new financial year's ledger (`Ledgers_Anonymised.pdf`, FY2025-26, 888 pages) was supplied for the first time. Running the unmodified V3.10 parser against it as a diagnostic surfaced 3 new, document-specific quirks not present in the frozen FY2024-25 ledger. Each was root-caused with the same evidence-first discipline as every prior checkpoint (quantify before touching code, prove safety before merging, diff before accepting), fixed, and verified to leave the FY2024-25 baseline's financial result completely unchanged.

---

## 1. Relationship to V3.10

V3.10 (`BASELINE_CHECKPOINT_V3.10.md`) is **not invalidated**. Its two deliberately-deferred residuals (`Payment_2` / `Sales_Payment`, Pattern 5) are untouched and remain exactly as documented. Everything in this checkpoint was triggered by a *new document*, not a re-examination of the old one.

---

## 2. Trigger: the FY2025-26 Ledger Diagnostic

Running `reconstruct_ledger_entries.py` (unmodified, V3.10) against the new 888-page ledger as a non-destructive diagnostic produced:

| Metric | Result |
|---|---|
| Unresolved lines | 15,623 |
| Unbalanced vouchers | 1,234 / 3,313 |

Bucketing the `unresolved` CSV's `reason` column and cross-checking against a direct, independent regex scan of the raw PDF text (not the parser's own output) found one dominant root cause: a new `INV /YY-YY/N` voucher-number format with a literal space before the `/`, breaking `_HEADER_RE`'s `vch_no` group (**7,736 occurrences across 612/888 pages, 69% of the document**). This was reported and approved before any code was written.

---

## 3. Fix 1 — `_VCH_NO_PATTERN` Widening

### Root cause

`_HEADER_RE` and `_WRAPPED_PARTICULARS_HEADER_RE`'s `vch_no` group (`[A-Za-z0-9\/\-_]+`) has no provision for whitespace, so `INV /25-26/166` fails to match at all — falling through to `unclassified_line`, which cascades into `detail_leg_without_active_voucher` and `leg_amount_mismatch_across_occurrences` for every subsequent line of that voucher.

### Fix

```python
_VCH_NO_PATTERN = r"[A-Za-z0-9_\-]+(?:\s?/[A-Za-z0-9_\-]+)*"
```

Applied to `_HEADER_RE` and `_WRAPPED_PARTICULARS_HEADER_RE` only. `_FUSED_HEADER_RE` deliberately untouched — its `vch_no` pattern addresses an unrelated scenario (digits glued directly to an amount with no separating whitespace at all).

### Verification

- Unit-tested against all three real shapes: `INV /25-26/166` (new), `MPSS/24-25/32` (old slash-joined), `699` (old bare-numeric) — all match exactly as expected (`tests/test_v3_11_fy2025_26_fixes.py`).
- Full 298-test suite (V3.10 baseline): **298/298 passing**.
- Exact before/after diff of the FY2024-25 ledger's reconstruction, re-run in an isolated copy: **entries, unresolved, and state-events CSVs byte-for-byte identical**.

### Impact on the new ledger

| | Before | After |
|---|---|---|
| Unresolved lines | 15,623 | 299 |
| Unbalanced vouchers | 1,234 / 3,313 | 181 / 5,947 |

---

## 4. Fix 2 — Continuation Tokens + Anonymization Artifact Fragments

A second pass on the new ledger's remaining 299 unresolved lines found two distinct, unrelated patterns:

### 4a. Multi-word / previously-unlisted continuation tokens (24 lines)

`"Maintenance Exp"` (21×), `"CONTROL"`/`"EQUIPMENT"` (1 each, two-line continuation), `"VEH.ACCOUNT"` (1×) are all Shape B voucher-header counterparty names wrapping onto the next line — e.g. `"By Veh. Running & Journal 792 ... Dr"` / `"Maintenance Exp"` is one printed name split by the page's line wrap. Confirmed, every occurrence, to follow a Shape B header line (never a detail-leg line).

**Fix**: added `"maintenance exp"`, `"control"`, `"equipment"`, `"veh.account"` to `_PARTICULARS_CONTINUATION_TOKENS`. Since a Shape B header leg's `particulars` is always `current_account` (never the printed counterparty), this mechanism only ever extends the private `_counterparty` field — narration text — and **cannot affect any voucher's Dr/Cr balance**. Verified directly: all 24 occurrences, in both documents, sit immediately after a Shape B header line.

### 4b. Anonymization-artifact fragments (263 lines)

Short, digit-free remnants (`"AL"`, `"ani"`, `"RSING"`, `"AR"`, `"OL"`, `"ICS"`, `"ON"`, `"O"`, `"OF"`, `"OUNT"`, `"MENT"`, `"Enterprises("`, `"Committe)"`) are the tail of a counterparty name whose first line was replaced by the anonymization process with a `"P0xx"`-style code, but whose own (wrapped) second line was not also scrubbed — e.g. `"P027 7,800.00 Dr"` / `"AL"` / `"Sales GST 6,610.14 Cr"`.

**Deliberately NOT added to `_PARTICULARS_CONTINUATION_TOKENS`**: that mechanism appends onto whichever leg came immediately before, and here that leg's particulars is already the complete, correct code (`"P027"`) — appending the fragment would silently rewrite it into a nonexistent compound key (`"P027 AL"`), breaking its cross-page corroboration. Since none of these fragments carry an amount, discarding them loses no financial information.

**Fix**: new `_ANONYMIZATION_ARTIFACT_FRAGMENTS` set, checked in the per-line loop and tagged with a distinct reason, `anonymization_artifact_fragment_discarded`, instead of the generic `unclassified_line` — reported, not silently dropped, and never merged into any leg.

### Verification

- `tests/test_v3_11_fy2025_26_fixes.py` asserts the two token sets never overlap, and — the real safety invariant — that no named leg's `particulars` in the real reconstructed output ends in `" <fragment>"` anywhere.
- Full before/after diff of the FY2024-25 ledger: **every diff line changes narration text or row order only — zero changes to amount, dr_cr, resolved, or entry_balanced on any row** (see §8).

---

## 5. Fix 3 — Redacted-Placeholder / Coded-Counterparty Named-Leg Merge

### Root cause

178 of the new ledger's 181 remaining unbalanced vouchers shared one clean signature: the same leg reprinted under two different labels — a `"P0xx"`-style per-document anonymization code on one page, and this account's own `"[Redacted account...]"` placeholder on its dedicated ledger page — keyed into `named_legs` twice, double-counting it against the voucher's Dr/Cr balance.

### Bounded simulation (read-only, run BEFORE any code change)

Exhaustive check across the full population of **both** documents, before writing any code: within each voucher, does a redacted-placeholder leg have exactly one other named leg with the same `dr_cr`, the same exact amount, and completely disjoint source pages?

| Metric | FY2025-26 ledger | FY2024-25 ledger (frozen) |
|---|---|---|
| Redacted-leg candidates checked | 452 | 25 |
| Exactly 1 match | **182** | **0** |
| Ambiguous (2+) | **0** | **0** |
| No match | 270 (legitimate 2-leg balancing pairs, correctly not candidates) | 25 |

**Decision gate: PASSED.** Zero ambiguity in the new ledger; zero matching candidates at all in the old one — confirming up front that this change could not touch the frozen baseline before it was even written.

### Implementation

New consolidation pass in `merge_occurrences()`, inserted alongside the existing `_PER_VOUCHER_NAME_ALIASES` pass: for each named leg whose particulars starts with `_REDACTED_PLACEHOLDER_PREFIX`, if exactly one other named leg in the same voucher shares its `dr_cr`, its exact amount, and has completely disjoint source pages, the two are merged (source pages combined, the redacted key removed). 0 or 2+ candidates leave every leg untouched — no guessing.

### Verification

- 5 new synthetic unit tests (`tests/test_v3_11_fy2025_26_fixes.py`): merges on exact match; does not merge on dr_cr mismatch, amount mismatch, same-page overlap, or ambiguity (2+ candidates).
- Full before/after diff of the FY2024-25 ledger confirms zero effect (0 matching candidates there, as predicted).

### Impact on the new ledger

| | Before Fix 1 | After Fix 1 | After Fixes 2+3 |
|---|---|---|---|
| Unresolved lines | 15,623 | 299 | **275** (263 harmless discarded fragments + 12 genuine open items) |
| Unbalanced vouchers | 1,234 / 3,313 | 181 / 5,947 | **3 / 5,947** |

---

## 6. Known Residual Issues — Deferred, Not Fixed (New Ledger)

- **`"Input IGST"` (12 lines) / `Journal_777`, `Journal_778`, `Journal_779` (3 unbalanced vouchers)**: a ~₹0.40 IGST leg whose amount is captured on only one of its several reprint pages; the other reprints lose the amount to the page's column wrap. Left unresolved and the 3 vouchers left unbalanced — there is nothing to merge or invent, and the amount is immaterial. Flagged for the client's own follow-up, not force-fixed.
- Every other quirk found in the new-ledger investigation (gap counts, page traces) is covered by Fixes 1–3 above; nothing else was bundled in.

---

## 7. New Addition — Rule 7: Voucher-Number Sequence Gap Detection

Added alongside the above (not a bug fix — new detection capability, prompted by a market-practice review of standard audit-analytics techniques). `scripts/rule_voucher_sequence_gap.py`: flags missing numbers inside an otherwise-continuous voucher-number series (classic forensic-accounting test — a gap can mean a deleted or suppressed voucher). Grouped per `(vch_type, prefix)` series; a series whose numbers exceed `MAX_SEQUENCE_DIGITS` digits (a vendor-assigned invoice number, not Tally's own sequence) is excluded generically, not by hardcoding a vch_type name. Imported into `combined_report.py` as its own section (same pattern as Rule 6), not duplicated inline, since the exclusion heuristic is real logic worth keeping in one place.

**Run against the frozen FY2024-25 ledger**: 66 missing numbers across 5 series (Journal 15, Payment 11, Receipt 27, Sales Cash 3, Sales GST 10). Contra and Debit Note — also Tally-assigned, qualifying series — are fully contiguous (0 gaps). Purchase's 10-digit vendor invoice numbers are correctly excluded by the digit-length heuristic without naming `"Purchase"` anywhere in the rule.

14 new tests (`tests/test_rule_voucher_sequence_gap.py`): prefix/number splitting (pure numeric, slash-joined, space-before-slash, no-trailing-digit), gap detection, contiguous-series null result, below-minimum-group-size exclusion, externally-assigned long-series exclusion, independent per-prefix and per-vch_type grouping, repeated-number-across-legs counted once, and two pinned checks against the real ledger (Purchase excluded, exactly the same 5 series have gaps).

---

## 8. Validated Metrics — FY2024-25 Ledger (Frozen Baseline)

| Metric | V3.10 | V3.11 | Changed? |
|---|---|---|---|
| Total GL rows | 14,625 | 14,625 | No |
| Canonical vouchers | 6,216 | 6,216 | No |
| Unbalanced vouchers | 2 | 2 | No — exactly `{Payment_2, Sales_Payment}`, unchanged |
| State events | 1,720 | 1,720 | No |
| Unresolved + conflicts (rows) | 579 | 547 | **Yes — exactly −32** (9 Maintenance Exp + 1 CONTROL + 1 EQUIPMENT + 21 VEH.ACCOUNT, all now merged into narration instead of falling through as noise) |
| `anonymization_artifact_fragment_discarded` (new reason, relabeled from `unclassified_line`) | 0 | 30 | Reclassified, not removed — same 30 rows (`Enterprises(` / `Committe)` pairs), clearer reason label |

**Zero changes to any row's `amount`, `dr_cr`, `resolved`, or `entry_balanced`** — confirmed by full diff (every changed line differs only in `narration` text or row position) and re-confirmed by `tests/test_v3_11_fy2025_26_fixes.py`'s explicit per-voucher spot checks.

Full suite: **331 tests, 331 passing** (298 from V3.10 + 19 for Fixes 1–3 + 14 for Rule 7).

---

## 9. Preserved Architecture & Invariants

- Tally Direction Rule, two-pass reconstruction (`collect_occurrences()` → `merge_occurrences()`), Shape A / Shape B parsing — unchanged.
- No fuzzy matching anywhere added by this checkpoint: the vch_no widening is a stricter character-class extension (not a loosened match), the continuation-token additions are exact-string (case-insensitive) matches, the artifact-fragment set is exact-string, and the new named-leg merge is exact `entry_id` + `dr_cr` + `amount` + disjoint-pages matching — the same rigor as every prior checkpoint.
- `Payment_2` / `Sales_Payment` header-regex ambiguity (V3.10 Pattern 5) — still deliberately unfixed.
- `"Input IGST"` / `Journal_777,778,779` (§6) — new residual, same "don't guess" discipline.
