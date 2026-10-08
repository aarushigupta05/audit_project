"""
RULE 1: Segregation of Duties Check
------------------------------------
Checks whether the same person both ENTERED and APPROVED a journal entry.
This is one of the most basic audit red flags -- normally, one person
records a transaction and a DIFFERENT person approves it, as a check.

We are NOT looking at the 'is_planted_issue' column here on purpose --
that column only exists because WE generated the synthetic data and
planted issues into it for testing. A real detection script would never
have that column available, since real-world data doesn't come pre-labeled.
We'll use it at the very end, ONLY to check our own work.

KNOWN LIMITATION ON REAL DATA (investigated 2026-10-02, documented rather
than worked around): this rule is structurally inert on the real pipeline.
adapt_ledger_to_schema.py always writes entered_by="" and approved_by=""
for every row -- Tally ledger exports (bank/accountant statements) carry no
ERP user-attribution metadata, so there is no real source for either field.
The empty-string guard below (entered and approved and ...) correctly
prevents this from vacuously flagging the entire dataset, but it also means
this rule can never produce a true positive on real data as the pipeline is
currently sourced -- it only has logic coverage against the synthetic
fixtures in tests/test_rule_segregation_of_duties.py and
tests/conftest.py::gl_rows. If a future data source provides real
entered_by/approved_by values (e.g. a direct ERP export instead of a PDF
bank/ledger statement), this rule would start producing real signal
immediately with no code change needed -- the logic itself is not the
limitation, the input data is.
"""

import csv
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "..", "data", "general_ledger.csv")


def check_segregation_of_duties(rows):
    flagged = []
    for row in rows:
        entered = (row.get("entered_by") or "").strip()
        approved = (row.get("approved_by") or "").strip()
        if entered and approved and entered == approved:
            flagged.append(row)
    return flagged


if __name__ == "__main__":
    # Step 1: read the CSV file into a list of dictionaries (one dict per row)
    with open(INPUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total entries in ledger: {len(rows)}")

    # Step 2: run our rule
    flagged = check_segregation_of_duties(rows)

    print(f"Entries flagged (same person entered & approved): {len(flagged)}")
    print()

    # Step 3: show the first few flagged entries so we can eyeball them
    print("Sample flagged entries:")
    for row in flagged[:5]:
        print(f"  {row['entry_id']} | {row['date']} | {row['account']} | "
              f"amount={row['amount']} | entered_by={row['entered_by']} | "
              f"approved_by={row['approved_by']}")

    # Step 4 (VALIDATION ONLY): check how many of our flags match what we
    # deliberately planted -- this tells us if our rule actually works.
    if rows and "is_planted_issue" in rows[0]:
        true_positives = sum(1 for row in flagged if row["is_planted_issue"] == "same_approver")
        total_planted = sum(1 for row in rows if row["is_planted_issue"] == "same_approver")

        print()
        print("--- Validation against planted issues (test only) ---")
        print(f"Planted 'same_approver' issues in data: {total_planted}")
        print(f"Correctly caught by our rule: {true_positives}")
