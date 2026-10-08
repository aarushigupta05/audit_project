"""
RULE 8: Unsupervised ML Anomaly Detection (Isolation Forest)
--------------------------------------------------------------
Every other rule in this project is either a direct cross-document
reconciliation (Rules 4/6/7/GSTR-reconciliation -- provable discrepancies)
or a hand-picked statistical/structural pattern (Rules 1/2/3/5/voucher-
sequence-gap -- a specific red flag someone had to think of first). This
rule is the third, originally-planned tier of the architecture ("rule-based
-> statistical -> unsupervised ML") that was never built: instead of
checking for ONE named pattern, an Isolation Forest looks at every leg's
feature vector at once and flags whichever ones sit furthest from the rest
of the data, whatever shape that turns out to have -- including shapes
nobody wrote a specific rule for.

Why Isolation Forest specifically: it isolates points by repeated random
splits rather than modeling a distribution, so it doesn't assume amounts
are normally distributed (they aren't -- see Benford's Law) and it doesn't
need labeled fraud examples (we don't have any on real data, only on
synthetic). It naturally handles the mix of a few numeric features and a
few categorical ones used here.

FEATURES -- chosen to work on BOTH the real ledgers (entered_by/approved_by/
timestamp are blank there -- see rule_segregation_of_duties.py's documented
limitation) and the synthetic fixture (which has no vch_type/dr_cr), by
using only fields present in some form on both, with the real-only ones
simply omitted when absent rather than guessed:
  - log_amount: log1p(amount) -- the amount's scale, log-transformed since
    raw amounts are heavily right-skewed (a few huge vouchers, many small
    ones) and Isolation Forest's random splits work better on a roughly
    symmetric distribution.
  - account_zscore: how many standard deviations this leg's log_amount is
    from the MEAN log_amount for that same account. A leg catches this
    rule's attention not just for being a large number in absolute terms,
    but for being unusual FOR THAT ACCOUNT -- a ₹50,000 "Bank Charges" leg
    is far stranger than a ₹50,000 "Sales" leg, and a flat log_amount
    feature alone can't see that difference.
  - is_debit: 1 for a Dr/Debit leg, 0 for Cr/Credit -- direction can carry
    signal (e.g. an account that is almost always credited getting an
    unusual debit).
  - day_of_month: the calendar day (1-31) the leg's voucher is dated on --
    catches period-end clustering (a disproportionate pile of entries
    backdated to day 31/1 of a period, a classic window-dressing pattern)
    that no other rule here looks for directly.
  - is_round_10k: 1 if the amount is an exact multiple of 10,000 and
    nonzero, else 0 -- reuses the same "suspiciously clean number" signal
    as rule_round_number.py (Rule 2), but as a continuous input into a
    multivariate model rather than a standalone flag; Rule 2 still runs
    and reports independently.
  - vch_type_rarity: 1 / (how many legs share this leg's vch_type) --
    present only when the input has a vch_type column (the real ledgers
    do; the synthetic fixture does not). A voucher type used only a
    handful of times across the whole ledger is inherently more unusual
    than "Journal" or "Payment", which dominate by count.

Deliberately NOT a feature: entered_by/approved_by/timestamp. These are
blank on every real row this project has (see rule_segregation_of_duties.py
for why), so including them would let the model "detect" nothing but which
rows came from synthetic vs. real data -- a leakage artifact, not a real
signal. This rule will consequently NOT catch the synthetic fixture's
same_approver/off_hours planted issues (those need entered_by/timestamp,
which is exactly what rule_segregation_of_duties.py already owns and is
already honestly documented as inert on real data for the same reason).
See test_rule_ml_anomaly_detection.py for the actual measured recall this
produces against the synthetic ground truth, reported as-is rather than
tuned to look good.

SEVERITY TIERING -- by anomaly-score percentile within the dataset, not a
fixed numeric cutoff (an Isolation Forest's raw decision_function score has
no universal meaning across datasets of different sizes/shapes):
  - top 1% most anomalous  -> high
  - next 2% (1%-3%)        -> medium
  - next 2% (3%-5%)        -> low
  - everything else        -> not flagged

This is a CANDIDATE list for human review, same philosophy as every other
rule here (rule_duplicate_transaction.py's docstring states this most
explicitly) -- amplified here because, unlike the other rules, nobody can
read this rule's logic and know in advance what pattern a given flag
represents; the model found *something* unusual about the leg's context,
not a named irregularity. combined_report.py reports it as its own
dataset-level section (same reporting shape as Benford's Law), not merged
into the per-leg list, for the same reason Benford's isn't merged: it's
evidence about where to look, not an accusation against one transaction
in isolation from the pattern that flagged it.
"""

import csv
import math
import os
from collections import defaultdict

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT_FILE = os.path.join(SCRIPT_DIR, "..", "data", "general_ledger.csv")

RANDOM_STATE = 42
N_ESTIMATORS = 200

# Percentile cut points (fraction of legs, most-anomalous first)
SEVERITY_BANDS = [
    (0.01, "high"),
    (0.03, "medium"),
    (0.05, "low"),
]


def _is_debit(row):
    direction = (row.get("dr_cr") or row.get("entry_type") or "").strip().lower()
    return 1.0 if direction.startswith("d") else 0.0


def _day_of_month(date_str):
    """Real dates are 'DD-Mon-YY' (e.g. '30-Apr-24'); synthetic dates are
    'YYYY-MM-DD'. Parse the day number out of either without needing a
    full date-format guess -- just read digits up to the first separator
    for the real format, or the last field for the synthetic one."""
    date_str = (date_str or "").strip()
    if not date_str:
        return 15.0  # neutral mid-month default for the rare blank date
    if "-" in date_str:
        parts = date_str.split("-")
        if len(parts[0]) <= 2 and parts[0].isdigit():
            return float(parts[0])  # 'DD-Mon-YY'
        if len(parts) == 3 and parts[2].isdigit():
            return float(parts[2])  # 'YYYY-MM-DD'
    return 15.0


def load_rows(input_file):
    with open(input_file, newline="") as f:
        return list(csv.DictReader(f))


def build_features(rows):
    """Returns (feature_matrix, feature_names, kept_row_indices). Rows with
    a non-positive or unparseable amount are skipped (an Isolation Forest
    has nothing meaningful to do with them) -- kept_row_indices maps each
    feature-matrix row back to its original position in `rows`."""
    amounts = []
    for row in rows:
        try:
            amounts.append(float(row["amount"]))
        except (KeyError, ValueError, TypeError):
            amounts.append(None)

    # Per-account mean/std of log_amount, computed over VALID amounts only
    log_amounts_by_account = defaultdict(list)
    for row, amt in zip(rows, amounts):
        if amt is not None and amt > 0:
            log_amounts_by_account[row.get("account", "")].append(math.log1p(amt))

    account_mean = {}
    account_std = {}
    for acct, vals in log_amounts_by_account.items():
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n if n > 1 else 0.0
        account_mean[acct] = mean
        account_std[acct] = math.sqrt(var) if var > 0 else 1.0  # floor: avoid div-by-zero

    has_vch_type = any("vch_type" in row for row in rows)
    vch_type_counts = defaultdict(int)
    if has_vch_type:
        for row in rows:
            vch_type_counts[row.get("vch_type", "")] += 1

    feature_names = ["log_amount", "account_zscore", "is_debit", "day_of_month", "is_round_10k"]
    if has_vch_type:
        feature_names.append("vch_type_rarity")

    matrix = []
    kept_indices = []
    for i, (row, amt) in enumerate(zip(rows, amounts)):
        if amt is None or amt <= 0:
            continue
        log_amt = math.log1p(amt)
        acct = row.get("account", "")
        zscore = (log_amt - account_mean.get(acct, log_amt)) / account_std.get(acct, 1.0)
        feats = [
            log_amt,
            zscore,
            _is_debit(row),
            _day_of_month(row.get("date", "")),
            1.0 if (amt % 10000 == 0) else 0.0,
        ]
        if has_vch_type:
            feats.append(1.0 / vch_type_counts.get(row.get("vch_type", ""), 1))
        matrix.append(feats)
        kept_indices.append(i)

    return matrix, feature_names, kept_indices


def _severity_for_rank(rank_fraction):
    for threshold, severity in SEVERITY_BANDS:
        if rank_fraction <= threshold:
            return severity
    return None


def detect_anomalies(rows, random_state=RANDOM_STATE):
    """Returns a list of flag dicts, most anomalous first. Each flag:
    {entry_id, account, amount, date, dr_cr, severity, anomaly_rank_pct}.
    Returns [] for fewer than 30 valid rows -- too small a sample for an
    Isolation Forest's random splits to mean anything (and no real or
    synthetic dataset in this project is anywhere near that small)."""
    matrix, feature_names, kept_indices = build_features(rows)
    if len(matrix) < 30:
        return []

    scaler = StandardScaler()
    X = scaler.fit_transform(matrix)

    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=0.05,
        random_state=random_state,
    )
    model.fit(X)
    scores = model.decision_function(X)  # lower = more anomalous

    order = sorted(range(len(scores)), key=lambda i: scores[i])  # most anomalous first
    n = len(order)

    flags = []
    for rank, idx in enumerate(order):
        rank_fraction = (rank + 1) / n
        severity = _severity_for_rank(rank_fraction)
        if severity is None:
            break  # order is sorted, so nothing after this is flaggable either
        row = rows[kept_indices[idx]]
        flags.append({
            "entry_id": row.get("entry_id", ""),
            "account": row.get("account", ""),
            "amount": row.get("amount", ""),
            "date": row.get("date", ""),
            "dr_cr": row.get("dr_cr") or row.get("entry_type", ""),
            "vch_type": row.get("vch_type", ""),
            "severity": severity,
            "anomaly_score": round(float(scores[idx]), 6),
            "anomaly_rank_pct": round(rank_fraction * 100, 2),
        })
    return flags


def run_ml_anomaly_report(input_file=None):
    input_file = input_file or DEFAULT_INPUT_FILE
    rows = load_rows(input_file)
    flags = detect_anomalies(rows)
    severity_counts = defaultdict(int)
    for f in flags:
        severity_counts[f["severity"]] += 1
    return {
        "total_legs_considered": len(rows),
        "flags": flags,
        "severity_counts": dict(severity_counts),
    }


if __name__ == "__main__":
    result = run_ml_anomaly_report()
    print(f"Legs considered: {result['total_legs_considered']}")
    print(f"Flags: {len(result['flags'])}  (by severity: {result['severity_counts']})")
    for f in result["flags"][:15]:
        print(f"  [{f['severity']:>6}] {f['entry_id']:<20} {f['account']:<30} "
              f"{f['dr_cr']:<4} {f['amount']:>14}  (top {f['anomaly_rank_pct']}%)")
