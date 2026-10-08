"""
Synthetic data generator for the audit/tax irregularity detection project.

Generates three linked datasets for ONE fictional company, ONE financial year:
  1. general_ledger.csv   -> accounting/audit log entries
  2. itr_summary.csv      -> what the company declared in its ITR
  3. form26as.csv         -> what third parties reported (TDS/income) about the company

A small percentage of records are deliberately corrupted/mismatched so we have
known irregularities to detect later. Every planted issue is labeled in a
'is_planted_issue' column ONLY in this generation script -- the detection
scripts will NOT get to see this column, they have to find it themselves.

SAFETY (added 2026-10-02): this generator used to default to writing
general_ledger.csv/itr_summary.csv/form26as.csv straight into data/ -- the
EXACT same paths as the real, reconstructed ledger and tax data, with no
guard at all. Running this script by accident would have silently
overwritten real data with fictional-company data. It now defaults to
data/synthetic/, a directory nothing else in this project reads from, and
refuses outright to write into data/ itself unless explicitly forced (see
_guard_against_real_data_overwrite below and tests/test_generate_data_safety.py).
"""

import argparse
import csv
import math
import os
import random
from datetime import datetime, timedelta

random.seed(42)  # reproducible output

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DATA_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "data"))
OUTPUT_DIR = os.path.join(REAL_DATA_DIR, "synthetic")


def _guard_against_real_data_overwrite(output_dir, force=False):
    """Refuses to write synthetic data directly into data/ -- the real,
    committed dataset's directory (general_ledger.csv, itr_summary.csv,
    form26as.csv live there and would be silently overwritten). Pass
    force=True only if that is genuinely what's intended."""
    resolved = os.path.abspath(output_dir)
    if resolved == REAL_DATA_DIR and not force:
        raise SystemExit(
            f"Refusing to write synthetic data directly into {REAL_DATA_DIR} "
            "-- that is the real, committed dataset's directory "
            "(general_ledger.csv, itr_summary.csv, form26as.csv live there "
            "and would be silently overwritten). Pass --force-overwrite-real-data "
            "if you really mean to do this, or --output-dir to write "
            "somewhere else."
        )

USERS = ["rmehta", "spatel", "akhan", "jverma", "nsingh"]
ACCOUNTS = [
    "Sales Revenue", "Purchase Expense", "Salary Expense", "Rent Expense",
    "Office Supplies", "Consulting Income", "Bank Charges", "Travel Expense",
    "Interest Income", "Depreciation", "Professional Fees", "Utilities Expense",
]
VENDORS = ["Sharma Traders", "Global Supplies Ltd", "TechNova Pvt Ltd",
           "Kumar & Co", "Bharat Logistics", "Om Enterprises"]

START_DATE = datetime(2024, 4, 1)   # Indian FY 2024-25
END_DATE = datetime(2025, 3, 31)


def random_date():
    delta_days = (END_DATE - START_DATE).days
    return START_DATE + timedelta(days=random.randint(0, delta_days))


def random_amount(low=500, high=200000, round_bias=False):
    if round_bias:
        # deliberately suspicious round numbers
        return random.choice([10000, 25000, 50000, 100000, 150000])
    amt = 10 ** random.uniform(math.log10(low), math.log10(high))
    return round(amt, 2)


def generate_general_ledger(n_entries=1200, issue_rate=0.06, output_dir=None):
    output_dir = output_dir or OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    rows = []
    planted_issue_count = 0

    for i in range(1, n_entries + 1):
        entry_id = f"JE{i:05d}"
        date = random_date()
        account = random.choice(ACCOUNTS)
        user = random.choice(USERS)
        vendor = random.choice(VENDORS)
        entry_type = random.choice(["Debit", "Credit"])
        approved_by = random.choice(USERS)

        is_issue = random.random() < issue_rate
        issue_type = ""

        if is_issue:
            planted_issue_count += 1
            issue_variant = random.choice([
                "round_number", "same_approver", "off_hours", "duplicate"
            ])

            if issue_variant == "round_number":
                amount = random_amount(round_bias=True)
                issue_type = "round_number"
                hour = random.randint(9, 18)

            elif issue_variant == "same_approver":
                amount = random_amount()
                approved_by = user  # segregation of duties violation
                issue_type = "same_approver"
                hour = random.randint(9, 18)

            elif issue_variant == "off_hours":
                amount = random_amount()
                hour = random.choice([1, 2, 3, 23])  # entered at odd hours
                issue_type = "off_hours"

            else:  # duplicate - will be added as a near-copy below
                amount = random_amount()
                issue_type = "duplicate_source"
                hour = random.randint(9, 18)
        else:
            amount = random_amount()
            hour = random.randint(9, 18)

        timestamp = date.replace(hour=hour, minute=random.randint(0, 59))

        row = {
            "entry_id": entry_id,
            "date": date.strftime("%Y-%m-%d"),
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "account": account,
            "vendor": vendor,
            "entry_type": entry_type,
            "amount": amount,
            "entered_by": user,
            "approved_by": approved_by,
            "narration": f"{entry_type} entry for {account}",
            "is_planted_issue": issue_type,  # kept for later scoring, not for detection
        }
        rows.append(row)

        # for the "duplicate" case, immediately add a near-identical entry
        if is_issue and issue_type == "duplicate_source":
            dup = dict(row)
            dup["entry_id"] = f"JE{i:05d}D"
            dup["is_planted_issue"] = "duplicate_copy"
            rows.append(dup)

    with open(f"{output_dir}/general_ledger.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"general_ledger.csv -> {len(rows)} rows, {planted_issue_count} planted issues "
          f"(written to {output_dir})")
    return rows


def generate_itr_and_26as(ledger_rows, mismatch_rate=0.15, output_dir=None):
    """
    Roll up ledger data into a simple ITR-style summary, then create a
    26AS-style third-party statement that mostly agrees with it -- except
    for a few deliberately mismatched entries (the real-world signal we
    want the reconciliation engine to catch).
    """
    output_dir = output_dir or OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    total_income = sum(r["amount"] for r in ledger_rows
                        if r["account"] in ("Sales Revenue", "Consulting Income", "Interest Income"))
    total_expense = sum(r["amount"] for r in ledger_rows
                         if r["account"] not in ("Sales Revenue", "Consulting Income", "Interest Income"))

    itr_row = {
        "assessment_year": "2025-26",
        "financial_year": "2024-25",
        "pan": "AAAPD1234C",  # dummy PAN
        "gross_total_income": round(total_income, 2),
        "total_deductions": round(total_expense * 0.1, 2),  # simplified
        "taxable_income": round(total_income - (total_expense * 0.1), 2),
        "tds_claimed": round(total_income * 0.10, 2),  # assume 10% TDS claimed
        "tax_paid": max(0.0, round((total_income - total_expense) * 0.20, 2)),
    }

    with open(f"{output_dir}/itr_summary.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=itr_row.keys())
        writer.writeheader()
        writer.writerow(itr_row)

    # 26AS: third-party reported TDS -- should roughly match tds_claimed above
    deductors = ["Sharma Traders", "Global Supplies Ltd", "TechNova Pvt Ltd", "Kumar & Co"]
    entries = []
    remaining_tds = itr_row["tds_claimed"]
    n = 4
    for idx, deductor in enumerate(deductors):
        share = round(remaining_tds / n, 2) if idx < n - 1 else round(remaining_tds, 2)
        remaining_tds -= share

        is_mismatch = random.random() < mismatch_rate
        reported_tds = share
        if is_mismatch:
            # deductor reported a different (lower) figure than what was claimed
            reported_tds = round(share * random.uniform(0.4, 0.85), 2)

        entries.append({
            "deductor_name": deductor,
            "pan": itr_row["pan"],
            "financial_year": "2024-25",
            "tds_reported_by_deductor": reported_tds,
            "tds_claimed_in_itr_share": share,
            "mismatch_flag_ground_truth": "YES" if is_mismatch else "NO",
        })

    with open(f"{output_dir}/form26as.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=entries[0].keys())
        writer.writeheader()
        writer.writerows(entries)

    print(f"itr_summary.csv -> 1 row (written to {output_dir})")
    print(f"form26as.csv -> {len(entries)} rows, "
          f"{sum(1 for e in entries if e['mismatch_flag_ground_truth']=='YES')} planted mismatches "
          f"(written to {output_dir})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR,
        help=f"Directory to write the synthetic CSVs into (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--force-overwrite-real-data", action="store_true",
        help="Required in addition to --output-dir to write into the real "
             "data/ directory itself. Without this, pointing --output-dir "
             "at data/ is refused.",
    )
    args = parser.parse_args()

    _guard_against_real_data_overwrite(args.output_dir, force=args.force_overwrite_real_data)

    ledger_rows = generate_general_ledger(output_dir=args.output_dir)
    generate_itr_and_26as(ledger_rows, output_dir=args.output_dir)
    print("\nAll synthetic data generated in:", os.path.abspath(args.output_dir))
