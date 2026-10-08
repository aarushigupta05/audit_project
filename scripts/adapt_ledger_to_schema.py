"""
adapt_ledger_to_schema.py
-------------------------
Adapts the output of reconstruct_ledger_entries.py V3.2
(data/ledgers_redacted_reconstructed_entries.csv) into the schema
expected by combined_report.py and the detection rules.

Mapping:
  - entry_id       <- entry_id
  - date           <- date
  - account        <- particulars
  - amount         <- amount (float)
  - entered_by     <- "" (no ERP user ID in bank/accountant statements)
  - approved_by    <- "" (no ERP approver ID in bank/accountant statements)
  - vendor         <- ""
  - entry_type     <- dr_cr
  - timestamp      <- ""
  - narration      <- narration (if present, else "")
  (is_planted_issue is omitted because real data has no ground truth label)

Extra real fields (vch_type, vch_no, dr_cr, resolved, source_pages,
corroboration_count, entry_balanced) are preserved as extra columns.
"""

import csv
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Removed 2026-10-04 (generalized-product architecture correction): this
# used to fall back to a hardcoded "C:\Users\<user>\Desktop\audit_project"
# when PROJECT_DIR/data didn't exist -- a single developer's own machine
# path, which is wrong on literally every other machine (and would just
# point at a second, differently-wrong, nonexistent path there, making a
# genuine setup problem harder to diagnose, not easier). The
# SCRIPT_DIR-relative computation below is already correct on any machine
# as long as this file stays in its normal scripts/ location relative to
# data/, which it always does.
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_DIR, "data")

INPUT_LEDGER = os.path.join(DATA_DIR, "ledgers_redacted_reconstructed_entries.csv")
OUTPUT_GL = os.path.join(DATA_DIR, "general_ledger.csv")


def adapt_ledger(input_path=INPUT_LEDGER, output_path=OUTPUT_GL):
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input ledger not found at {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        in_rows = list(reader)

    out_fieldnames = [
        "entry_id", "date", "timestamp", "account", "vendor",
        "entry_type", "amount", "entered_by", "approved_by",
        "narration",
        # Extra real ledger provenance columns preserved
        "vch_type", "vch_no", "dr_cr", "resolved",
        "source_pages", "corroboration_count", "entry_balanced",
    ]

    out_rows = []
    for r in in_rows:
        amount_val = r.get("amount", "0.0")
        try:
            amount_float = float(amount_val)
        except ValueError:
            amount_float = 0.0

        out_rows.append({
            "entry_id": r.get("entry_id", ""),
            "date": r.get("date", ""),
            "timestamp": "",
            "account": r.get("particulars", ""),
            "vendor": "",
            "entry_type": r.get("dr_cr", ""),
            "amount": f"{amount_float:.2f}",
            "entered_by": "",
            "approved_by": "",
            "narration": r.get("narration", ""),
            # Preserved real metadata
            "vch_type": r.get("vch_type", ""),
            "vch_no": r.get("vch_no", ""),
            "dr_cr": r.get("dr_cr", ""),
            "resolved": r.get("resolved", ""),
            "source_pages": r.get("source_pages", ""),
            "corroboration_count": r.get("corroboration_count", ""),
            "entry_balanced": r.get("entry_balanced", ""),
        })

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Adapted {len(out_rows)} ledger entries -> {output_path}")
    return len(out_rows)


if __name__ == "__main__":
    adapt_ledger()
