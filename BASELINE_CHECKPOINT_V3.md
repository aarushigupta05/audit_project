# Input Normalization Layer — Version Checkpoint (V3.1 Frozen)

**Date**: 2026-09-06
**Status**: ACCEPTED STABLE BASELINE (FROZEN)

---

## 1. Frozen Modules
1. scripts/reconstruct_ledger_entries.py (V3.1 state-machine two-pass architecture with Pass-1 intra-occurrence pruning)
2. scripts/pdf_to_csv_converter.py (Strategy 0: automatic ledger detection and bypass of \extract_tables()\)

---

## 2. Validated Baseline Metrics (926-Page Ledger PDF)
- **Input Document**: \data/raw_pdfs/ledgers_redacted.pdf\ (926 pages, 1.68 MB)
- **Raw Voucher Occurrences Parsed**: 14,388
- **Canonical Vouchers Merged**: 6,623
- **Canonical Named Legs**: 17,177
- **Canonical Orphan Legs**: 22 (preserved distinctly with source page)
- **Duplicate Source-Page Legs**: **0** (down from 14)
- **Balanced Canonical Vouchers**: 4,319 (65.2%)
- **Unbalanced Canonical Vouchers**: 2,304 (34.8% - single-leg counter-accounts)
- **State Events Captured**: 1,720 (748 Brought Forward, 748 Carried Over, 132 Closing Balances, 92 Opening Balances)
- **Unresolved Line Items**: 603 (<0.65 lines/page, zero financial legs lost)
- **Cross-Occurrence Conflicts**: **0** (100% false conflicts eliminated)
- **Execution Performance**:
  - Standalone \econstruct_ledger_entries.py\: **110.64s**
  - Integrated \pdf_to_csv_converter.py\: **146.99s**
- **Table Extraction Bypass**: \extract_tables()\ is 100% bypassed on ledger PDFs.
- **Byte-for-Byte Equivalence**: Standalone and integrated runs produce identical SHA-256 hashes:
  - \ledgers_redacted_reconstructed_entries.csv\: SHA256 \6c2e9347f4f6a88566fde5a834a72f18098d7acb85a12fbf84fff1b535d0d2ae\
  - \ledgers_redacted_reconstructed_state_events.csv\: SHA256 \ed4f19b286e3fab99bbbf343772f987d45d46557ed1b809dcb4ed12db2a701a\
  - \ledgers_redacted_reconstructed_unresolved.csv\: SHA256 \1988bd2a423ade1e2729fb0f9c365887a60d34b79c728fc02ea3fb96d37448ba\

---

## 3. Preserved Architecture & Invariants
1. **Two-Pass Ledger Design**:
   - Pass 1: Parse pages into raw occurrences without intra-occurrence deduplication, except pruning synthetic Shape-A header legs when an explicit detail row already captures the exact same leg.
   - Pass 2: Conservative cross-account deduplication and corroboration preserving occurrence provenance.
2. **Accounting Invariants**:
   - Direction Rule: \To\ = Debit (Dr), \By\ = Credit (Cr).
   - Trailing Dr/Cr is running balance direction, not transaction leg direction.
   - Shape A: First header amount is the implicit current-account transaction leg (unless explicitly present in detail lines).
3. **Data Preservation & Anti-Data-Loss**:
   - Orphan legs are never merged into one another.
   - Narration is contextually captured and attached to voucher records.
   - Low-scoring table groups in generic converter are quarantined (\*_quarantined.csv\), not deleted.
   - Generic path remains operational for Forms 26AS, AIS, Computation of Income, and ITR-V.
