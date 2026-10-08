# Input Normalization Layer — Version Checkpoint (V3.6 Frozen)

**Date**: 2026-10-01
**Status**: ACCEPTED STABLE BASELINE (FROZEN) — supersedes V3.5
**Freeze basis**: Direct evidence-based reclassification of 3 pages, independently cross-checked against the firm's official trial balance (exact rupee match on opening balances and Dr/Cr totals for the 2 redacted accounts; exact running-balance continuity match for the 1 genuine continuation), plus a full before/after diff confirming zero structural change and zero downstream rule impact. See §2–§5.

---

## 1. Relationship to V3.5

V3.5 (`BASELINE_CHECKPOINT_V3.5.md`, commit `e0535a3`/`66d9edd`) is **not invalidated** — it remains correct on every metric it was frozen against (the blank-particulars-header fix, J & K Bank Ltd reconciliation, Rule 5/Benford impact). V3.6 fixes a **separate, independently-discovered bug**: the header-carryover bug, characterized during the same trial-balance reconciliation work that surfaced the V3.5 bug, but mechanically unrelated to it.

**Trigger**: the trial balance showed mismatches on `Laptop` and `Pollution Fees` that didn't fit the Shape B or blank-particulars patterns. Traced to exactly 3 pages (477, 546, 547) in `ledgers_redacted.pdf` where `_detect_page_header_and_account()` could not recognize the page's own header, and the reconstruction code was silently inheriting `current_account` from whatever page came immediately before — regardless of whether that was actually correct.

---

## 2. Problem Statement & Root Cause (V3.6)

**Problem:** `collect_occurrences()` carried `current_account` forward unconditionally whenever a page's own header wasn't recognized. This is usually safe (continuation pages normally still re-print their account name in this document — confirmed empirically: only 3 of 926 pages ever fail to produce a detected header at all), but on the 3 pages where it does happen, blind backward inheritance was wrong every time:

- **Pages 477 and 546**: each opens with its own `"Opening Balance"` line — i.e. a brand-new account starting fresh, not a continuation of the page before. Their opening balances (₹2,05,51,866.76 Cr and ₹85,21,875.81 Cr) and the exact Dr/Cr totals of the legs that leaked into `Laptop`/`Pollution Fees` (₹63,19,004.55 Dr / ₹19,30,441.00 Cr, and ₹21,86,245.50 Dr / ₹8,25,182.00 Cr respectively) match the trial balance's 2 unnamed (redacted) rows **exactly**, to the rupee, on every figure. These are almost certainly the two partners' capital/current accounts, redacted in both the ledger PDF and the trial balance PDF for privacy. There is no name to recover — this was never a parsing failure.
- **Page 547**: no `Opening Balance` of its own; its `"Carried Over"` balance (₹5,41,300.00 / ₹4,77,300.00) matches page 548's `"Brought Forward"` balance **exactly** — a genuine continuation whose own header (`"Tr. A/c"` / `"unt"`, split across two lines) just doesn't match either of the two recognized header shapes.

**Why earlier verification didn't catch it:** like the blank-particulars bug, this creates plausible, internally-consistent vouchers (correct amounts, correct balancing) — it only misattributes *which account* they belong to, which is invisible to voucher-balance checks and only surfaces against an independent source document.

**Scope:** exactly 3 pages, 40 legs total (14 + 11 + 15) — the full population of pages in `ledgers_redacted.pdf` where `_detect_page_header_and_account()` returns no account at all.

---

## 3. The Fix

New function `_resolve_headerless_pages()` in `reconstruct_ledger_entries.py`, called once per `collect_occurrences()` run (a pre-scan pass, read-only, before the main per-page loop). For every page where no header is recognized:

1. If the page's own first body line is an `Opening Balance` → a clearly-marked placeholder account, uniquely tied to that page's exact opening balance (e.g. `"[Redacted account — TB opening Cr 2,05,51,866.76]"`). Never inherits from the previous page; never invents a real name; never discards the legs.
2. Else, if the page's `Carried Over` balance exactly matches (amount **and** Dr/Cr sign) the `Brought Forward` balance of the next page that *does* have a recognized header → attributed to that next page's account (forward attribution, verified by evidence, not inherited backward).
3. Else → left unresolved (`None`). Not guessed.

**Scope discipline**: page 548's own header-parsing quirk (`"Ledger Tr. A/c"` — a separate, smaller mis-parse of that page's unusual two-line header format) is explicitly left untouched, confirmed by a dedicated regression test. No other rule, the PDF parser, Sales/Round-Off-Discount, or the FEES bug were touched.

---

## 4. Validated Metrics — Unchanged from V3.5

Pure relabel, verified by exact key-for-key diff (`entry_id`, `dr_cr`, `amount`, `source_pages`):

| Metric | V3.5 | V3.6 | Changed? |
|---|---|---|---|
| Canonical vouchers | 6,216 | 6,216 | No |
| Total ledger rows | 17,128 | 17,128 | No |
| Rows removed/added | — | 0 / 0 | No |
| Rows with changed account label | — | **40** | **Yes — the fix** (14 + 11 + 15, exact match to the investigated population) |
| Vouchers flipped `entry_balanced` | — | 0 either direction | No |
| Other rows changed anywhere in the 926-page population | — | 0 | confirmed by full diff |

**Per-page result**:

| Page | Legs | Before | After |
|---|---|---|---|
| 477 | 14 | `Laptop` | `[Redacted account — TB opening Cr 2,05,51,866.76]` |
| 546 | 11 | `Pollution Fees` | `[Redacted account — TB opening Cr 85,21,875.81]` |
| 547 | 15 | `Pollution Fees` | `Ledger Tr. A/c` (matches page 548 exactly) |

`Laptop` and `Pollution Fees` each now hold exactly 1 real leg (page 476, ₹44,000; page 545, ₹6,000 — both match the trial balance exactly).

**Trial balance reconciliation** (`reconcile_trial_balance.py`): mismatches 5 → 3. `Laptop` and `Pollution Fees` now reconcile exactly. Remaining 3: the pre-existing Purchase rounding residual (₹1,203.60) plus the two still-deferred items (Sales, Round Off/Discount — unchanged, untouched by this fix).

---

## 5. Downstream Impact — Rule 5 and Benford's Law

**Zero change.** `total_legs_flagged`: 1419 → 1419. Severity distribution identical. Benford chi-square: 287.15 → 287.15 (unchanged — this fix never touches any amount). Diffed the full flagged-leg list leg-by-leg: the only differences are cosmetic account-label updates on 10 legs that were already flagged both before and after (same severity, same reason, same signal count) — none involve Rule 5 (duplicate detection), since every relabeled leg moved to a brand-new, non-colliding account name.

No re-pin needed for `tests/test_combined_report_regression.py` — its pinned values from V3.5 remain correct and unchanged.

---

## 6. Known Residual Issues — Deferred, Not Fixed

Unchanged from V3.5, confirmed untouched by this fix:

- **FEES narration-continuation bug** — still deferred, still cosmetic-only.
- **Sales mismatch** (~₹8.27 crore overshoot even after name-aliasing) — still open, next on the list.
- **Round Off/Discount residual** (~2x the trial balance figure) — still open, low priority.
- **Page 548's `"Ledger Tr. A/c"` label** — a separate, smaller header-format mis-parse, explicitly noted but left untouched (regression-tested to confirm it stays that way).

---

## 7. New Regression Tests

`tests/test_header_carryover_fix.py` (12 tests): `Laptop`/`Pollution Fees` lose exactly their wrongly-attributed legs (with pre-existing, unrelated `"UNRESOLVED"` orphan legs on 477/546 correctly excluded and separately confirmed byte-identical pre/post fix), page 547 matches page 548's account exactly (re-parsed at the occurrence level, not the flattened CSV, so correctly-named sibling legs like `"TDS PAYABLE"` aren't mistaken for the page's own account), page 548's label stays untouched, the two redacted placeholders are distinct and correctly tied to their trial-balance opening balances, total row count/balancing invariants hold, and 3 unit tests against `_resolve_headerless_pages()`'s decision logic directly (Opening-Balance classification, exact-continuity-match requirement, and an end-to-end check against the real PDF for pages 470–550).

Full suite: 91 tests, 91 passing.

---

## 8. Preserved Architecture & Invariants

*(Unchanged from V3.1/V3.3/V3.4/V3.5 — reproduced for completeness)*

1. **Two-Pass Ledger Design:** this fix adds a pre-scan (Pass 0) that runs once before Pass 1, independent of the two-pass dedup/corroboration structure, which is otherwise untouched.
2. **Accounting Invariants:** Direction Rule unaffected — this fix only changes which account a page's header legs attach to, never the Dr/Cr direction itself.
3. **Data Preservation & Anti-Data-Loss:** no leg is ever discarded by this fix; a page with genuinely no resolvable account is left as an explicit, traceable, uniquely-identified placeholder rather than silently merged into an unrelated account or dropped.

---

## 9. Git

**This freeze's commit**: recorded immediately after this document was written (commit hash appended below once committed).

**Commit hash**: `c787b01` (3 files changed, 492 insertions, 0 deletions)
