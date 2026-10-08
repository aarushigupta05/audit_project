"""
RULE 2: Round-Number Bias Check
--------------------------------
Flags transactions where the amount is a suspiciously "clean" round number
(exact multiple of 10,000), EXCLUDING "Rent Expense" since fixed rent is
naturally often a round number and isn't a real red flag.

Reasoning:
- Genuine, organically-calculated amounts (invoices with tax, quantities x
  rates, etc.) almost never land exactly on a round 10,000 multiple by chance.
- Small round numbers (like 100, 500) are excluded from this check because
  they occur naturally too often in real small expenses and would create
  too much noise.
"""

import csv
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "..", "data", "general_ledger.csv")
EXEMPT_ACCOUNTS = {"Rent Expense"}


def is_round_number(amount, multiple=10000):
    # amount is suspicious if dividing by 'multiple' leaves no remainder
    return amount % multiple == 0


def check_round_number_bias(rows):
    flagged = []
    for row in rows:
        amount = float(row["amount"])
        account = row["account"]

        if account in EXEMPT_ACCOUNTS:
            continue  # skip rent, it's normal for rent to be round

        if is_round_number(amount):
            flagged.append(row)
    return flagged


if __name__ == "__main__":
    with open(INPUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total entries in ledger: {len(rows)}")

    flagged = check_round_number_bias(rows)
    print(f"Entries flagged (round-number bias): {len(flagged)}")
    print()

    print("Sample flagged entries:")
    for row in flagged[:5]:
        print(f"  {row['entry_id']} | {row['date']} | {row['account']} | "
              f"amount={row['amount']} | vendor={row['vendor']}")

    # Validation only -- checking against what we know we planted
    if rows and "is_planted_issue" in rows[0]:
        true_positives = sum(1 for row in flagged if row["is_planted_issue"] == "round_number")
        total_planted = sum(1 for row in rows if row["is_planted_issue"] == "round_number")

        print()
        print("--- Validation against planted issues (test only) ---")
        print(f"Planted 'round_number' issues in data: {total_planted}")
        print(f"Correctly caught by our rule: {true_positives}")
