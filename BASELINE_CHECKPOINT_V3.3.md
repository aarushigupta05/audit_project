# Input Normalization Layer — Version Checkpoint (V3.3 Frozen)

**Date**: 2026-10-01
**Status**: ACCEPTED STABLE BASELINE (FROZEN)
**Freeze basis**: Direct per-voucher verification against raw PDF source documents (see §5). NOT based on before/after aggregate count comparison (see §6 for why).

---

## 1. Frozen Modules

1. `scripts/reconstruct_ledger_entries.py` (V3.3 — column proximity fix + Bank/Cash Book account resolution)
2. `scripts/pdf_to_csv_converter.py` (Strategy 0: automatic ledger detection and bypass of `extract_tables()`)

---

## 2. Problem Statement & Root Cause (V3.3)

**Problem:** When a voucher header line in the PDF has a small horizontal gap between the voucher number and the amount column, `pdfplumber`'s default `x_tolerance=3` merged the final digit(s) of the voucher number with the leading digit(s) of the amount into a single token.

Example corruptions:
- `"Receipt 611,13,137.59"` was parsed as voucher `Receipt 6` with amount ₹11,13,137.59 (inflated by ₹10L)
- `"Journal 871,52,000.00"` was parsed as voucher `Journal 8` with amount ₹71,52,000.00 (inflated by ₹70L)

**Mechanism:** The non-greedy `_FUSED_HEADER_RE` matched the minimum digits needed to form a valid Indian currency suffix, stealing the remaining digits from the voucher number and prepending them to the amount. Date-disambiguation then created a second ghost voucher entry (e.g., `Receipt_6_15-Apr-24`) alongside the real one (`Receipt_61`).

**Physical ground truth:** PDF character coordinate inspection confirms a horizontal gap of 2.35–2.69 points between the `Vch No.` and `Debit`/`Credit` columns. Setting `x_tolerance=1.5` causes `pdfplumber` to respect this gap, preserving true column boundaries without merging adjacent tokens.

**Two code changes in V3.3:**
1. **Line 215** of `reconstruct_ledger_entries.py`: `page.extract_text()` → `page.extract_text(x_tolerance=1.5)`
2. **Lines 171–202**: Extended `_detect_page_header_and_account` to correctly resolve `J & K Bank Ltd Book` pages (Shape 1 detection + Shape 2 naming fix). This was a pre-existing defect exposed when the x_tolerance change altered text layout on Bank Book pages; it is documented as a V3.3 fix, not a separate version increment.

---

## 3. Validated Baseline Metrics (926-Page Ledger PDF)

- **Input Document**: `data/raw_pdfs/ledgers_redacted.pdf` (926 pages, 1.68 MB)
- **Raw Voucher Occurrences Parsed**: 14,619
- **Canonical Vouchers Merged**: 6,216 (ghost/split vouchers unified)
- **Canonical Named Legs**: 17,106
- **Canonical Orphan Legs**: 22 (preserved distinctly with source page)
- **Duplicate Source-Page Legs**: **0**
- **Balanced Canonical Vouchers**: 5,078 (81.7%)
- **Unbalanced Canonical Vouchers**: 1,138 (18.3% — single-leg counter-accounts)
- **State Events Captured**: 1,720 (748 Brought Forward, 748 Carried Over, 132 Closing Balances, 92 Opening Balances)
- **Unresolved Line Items**: 579 (<0.63 lines/page, zero financial legs lost)
- **Cross-Occurrence Conflicts**: **0** (100% false conflicts eliminated)
- **Artifact Hashes (SHA-256)**:
  - `ledgers_redacted_reconstructed_entries.csv`: `0ead792d49863657c229acc704109ce7308343d75837e1923ada009b89aed088`
  - `ledgers_redacted_reconstructed_state_events.csv`: `0d659c0af2c011d287fe244f273863ed45cf3a423a4bcd81c91469b9e8afeba1`
  - `ledgers_redacted_reconstructed_unresolved.csv`: `73302715344808d3fc105d7b3e62c0ad0ec0580b2b8c2e2c333088d64dc03bf5`

---

## 4. Regression Verification (5 Target Vouchers)

All 5 originally-confirmed fused-header cases now parse the correct `vch_no`, the correct non-inflated amount, eliminate their ghost vouchers, and evaluate `entry_balanced == True`. Each was verified against raw PDF character coordinates and at least two independent counter-ledger pages:

| Entry ID | Date | Vch No | Correct Amount (Dr = Cr) | Ghost Eliminated | Pages Corroborating |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `Receipt_61` | 15-Apr-24 | 61 (was parsed as 6) | ₹1,13,261.53 | `Receipt_6_15-Apr-24` | 22; 40; 393 |
| `Receipt_64` | 16-Apr-24 | 64 (was parsed as 6) | ₹2,16,288.00 | `Receipt_6_16-Apr-24` | 393; 497; 498 |
| `Receipt_73` | 18-Apr-24 | 73 (was parsed as 7) | ₹1,11,109.00 | `Receipt_7_18-Apr-24` | 22; 41; 394 |
| `Receipt_79` | 20-Apr-24 | 79 (was parsed as 7) | ₹1,09,789.24 | `Receipt_7_20-Apr-24` | 22; 41; 394 |
| `Journal_87` | 10-Jun-24 | 87 (was parsed as 8) | ₹1,52,000.00 | `Journal_8_10-Jun-24` | 267; 342; 885 |

---

## 5. Extended Sample Verification (Freeze Justification)

This freeze is justified by direct per-voucher verification against raw PDF source documents across a 25-voucher sample, not by aggregate count comparison (see §6).

### 5a. 15-Sample Newly-Balanced Verification (seed=42, from 215 F→T transitions)

15 vouchers sampled randomly from the 215 newly-balanced entries; each was verified by:
1. Extracting the raw PDF lines for all source pages using `pdfplumber` character coordinates
2. Confirming Dr and Cr amounts independently from opposite counter-ledger pages
3. Confirming no incorrect amount inflation remained

**Result: 15/15 verified correct. Zero false positives.**

All 15 showed the consistent physical signature: character gap of 2.35–2.69 pts at the voucher-number/amount boundary on Cash Book (J & K Bank Ltd) and Payment/Receipt pages — exactly the gap that `x_tolerance=1.5` is calibrated to preserve.

### 5b. 10-Sample Diff Regression Check (seed=42, from 218 changed lines outside original 5)

10 diff lines sampled from non-target vouchers affected by the tolerance change; each was verified by:
1. Raw character coordinate inspection confirming the gap measurement
2. Counter-account cross-check confirming the new amount matches the contra-side entry

**Result: 10/10 verified correct. Zero regressions found.**

No evidence of over-splitting (legitimate single-token lines incorrectly separated).

---

## 6. Known Gap: Pre-V3.3 Codebase Does Not Match the Published V3.1 Baseline

> **This is a documented, unresolved finding. It does not block this freeze, but it must be stated plainly.**

Reverting only the two documented V3.3 changes (`x_tolerance` adjustment + account-detection fix) and running the resulting script against the full 926-page PDF produces:

- **Canonical vouchers**: 6,363
- **Balanced**: 4,865 (76.5%)

This does **not** match `BASELINE_CHECKPOINT_V3.md`'s published numbers:

- **Canonical vouchers**: 6,623
- **Balanced**: 4,319 (65.2%)

The gap is **260 canonical vouchers** and **546 balanced vouchers** in the wrong direction (the pre-V3.3 state appears better-balanced than the published V3.1 frozen baseline, which itself is impossible if the only intervening change were the V3.3 fix).

**Why this exists:** No version control was in use between the V3.1 freeze (2026-09-06) and today. The current codebase contains additional undocumented intermediate changes beyond the two V3.3 changes — their exact nature cannot be identified without a git history.

**Why this does not block the freeze:** V3.3's correctness is established by §5's direct per-voucher PDF verification, not by computing a delta from V3.1. The 5 confirmed corruption cases are validated from first principles (raw character coordinates + counter-ledger corroboration). The extended 25-sample checks confirm no regressions and correct behaviour across the non-target population. The before/after comparison is supporting evidence, not the foundation.

**Action taken:** Git is initialized at this commit (see §8) so that future freezes can always be diffed exactly.

---

## 7. Four-Quadrant Reconciliation (V3.2-script-state → V3.3)

Comparison is between the V3.3 output and the real V3.2-script-state output (i.e., current code with the two V3.3 changes reverted). As noted in §6, this V3.2-script-state baseline is **not** the same as the V3.1 published checkpoint.

**V3.2-script-state**: 6,363 canonical vouchers, 4,865 balanced, 1,498 unbalanced
**V3.3**: 6,216 canonical vouchers, 5,078 balanced, 1,138 unbalanced

Universe split:
- 6,216 vouchers present in **both** versions
- 147 vouchers present **only in V3.2-script-state** (eliminated ghost/date-split entries)
- 0 vouchers present **only in V3.3**

Four-quadrant table (vouchers present in both):

|  | **V3.3 Balanced** | **V3.3 Unbalanced** | **Row Total** |
| :--- | :--- | :--- | :--- |
| **V3.2 Balanced** | 4,863 (stayed balanced) | **0 (regressions)** | 4,863 |
| **V3.2 Unbalanced** | 215 (newly balanced) | 1,138 (stayed unbalanced) | 1,353 |
| **Column Total** | 5,078 | 1,138 | **6,216** |

Eliminated-only vouchers: 147 total — **2 were balanced in V3.2-script-state, 145 were unbalanced.**
The 2 eliminated balanced vouchers (`Journal_7_5-Apr-24`, `Payment_5_3-Apr-24`) were **ghost entries** that appeared accidentally balanced due to coincidental amount matching across inflated legs; their elimination is correct.

**Full arithmetic identity (closes exactly):**

```
Net balanced change = +215 (F→T) − 0 (T→F) − 2 (eliminated balanced ghosts) + 0 (added)
                    = +213
Observed: 5,078 − 4,865 = 213  ✓
```

**Zero regressions.** No voucher that was genuinely balanced in V3.2-script-state became unbalanced in V3.3.

**Resolving the earlier 215 vs. 207 discrepancy:**
The in-memory `collect_v3_2` reimplementation overstated V3.2-balanced by 6 (reporting 4,871 instead of 4,865), producing a net change of 207 instead of 213. The discrepancy was entirely in the baseline estimate, not in the newly-balanced count. The correct figure is 213 net / 215 F→T transitions.

---

## 8. Git Initialization

From this checkpoint forward, every freeze corresponds to a git commit in the project repository.

- **Repository**: initialized at `C:\Users\<user>\Desktop\audit_project` (see Part B of freeze process)
- **This freeze's commit**: recorded immediately after this document was written (commit hash appended below)
- **Tracking policy**:
  - ✅ Tracked: `scripts/*.py`, `BASELINE_CHECKPOINT_*.md`, `README.md`, small tax-doc CSVs (`data/26AS__*.csv`, `data/form26as.csv`, `data/itr_summary.csv`), `.gitignore`
  - ❌ Ignored: `data/ledgers_redacted_reconstructed_*.csv` (large, reproducible), `data/general_ledger.csv`, `data/raw_pdfs/*.pdf`, `output/`, `__pycache__/`

**First commit hash**: `598d80d9ca0aef3b2da538f57cfb175ae4c911ab`

---

## 9. Preserved Architecture & Invariants

_(Unchanged from V3.1 — reproduced for completeness)_

1. **Two-Pass Ledger Design:**
   - Pass 1: Parse pages into raw occurrences without intra-occurrence deduplication, except pruning synthetic Shape-A header legs when an explicit detail row already captures the exact same leg.
   - Pass 2: Conservative cross-account deduplication and corroboration preserving occurrence provenance.
2. **Accounting Invariants:**
   - Direction Rule: `To` = Debit (Dr), `By` = Credit (Cr).
   - Trailing Dr/Cr is running balance direction, not transaction leg direction.
   - Shape A: First header amount is the implicit current-account transaction leg (unless explicitly present in detail lines).
3. **Data Preservation & Anti-Data-Loss:**
   - Orphan legs are never merged into one another.
   - Narration is contextually captured and attached to voucher records.
   - Low-scoring table groups in generic converter are quarantined (`*_quarantined.csv`), not deleted.
   - Generic path remains operational for Forms 26AS, AIS, Computation of Income, and ITR-V.
