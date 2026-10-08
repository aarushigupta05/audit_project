"""
COMBINED REPORT
-----------------
Runs all detection rules together and produces one unified, prioritized
report -- instead of four separate outputs.

Design decision:
- Rule 1 (segregation of duties), Rule 2 (round-number bias), and Rule 4
  (ITR vs 26AS reconciliation) all flag SPECIFIC transactions/entities,
  so their results get merged into one combined list.
- Rule 3 (Benford's Law) checks the WHOLE dataset's health, not one
  transaction -- so it's reported separately as a dataset-level warning,
  not mixed into the per-transaction list.
- Rule 6 (trial balance vs ledger reconciliation, added 2026-10-02) flags
  SPECIFIC accounts, reported alongside Rule 4 as its own high-confidence
  section (not merged into the per-leg list, since it's account-level, not
  leg-level -- same reporting shape Rule 4 already uses).

GSTR-1/GSTR-3B monthly GST reconciliation (scripts/rule_gstr_reconciliation.py,
also added 2026-10-02) is deliberately NOT included here: it checks a
different set of periods (16 months, April 2025-July 2026, FY2025-26 and
FY2026-27) against a document pair with zero overlap with
general_ledger.csv (FY2024-25) -- merging it in would mix two unrelated
timeframes under one report. Rule 6 is the opposite case: same ledger,
same period, same severity ladder already established by Rule 4 --
there's no timeframe mismatch to protect against, so it belongs here.

Import, not duplicate, for Rule 6 (and now Rule 7):
Every other rule above is duplicated inline in this file rather than
imported from its standalone module (see each rule's own comment). Rule 6
breaks that pattern deliberately: its real work is parse_trial_balance.py's
~100-line, PDF-geometry-based column extraction (x1-position bucketing
over the trial balance PDF) plus reconcile_trial_balance.py's account-alias
handling -- re-implementing that inline would be fragile duplication of
real complexity, not the few lines of arithmetic the other rules'
duplication costs. check_trial_balance_reconciliation() is imported
directly from rule_trial_balance_reconciliation.py instead, so there is
exactly one implementation of the PDF-parsing logic, never two to drift
apart.

Rule 7 (voucher-number sequence gaps, added 2026-10-03) is imported for the
same reason: its prefix/trailing-number split and the externally-assigned-
series exclusion (rule_voucher_sequence_gap.py's MAX_SEQUENCE_DIGITS
heuristic) are real logic worth keeping in one place, not a few lines of
arithmetic safe to fork.

Rule 7 is reported alongside Rules 4 and 6 as its own section, not merged
into the per-leg list: like Rule 6 it isn't about one leg or one amount,
it's about an entire voucher-number SERIES (a vch_type/prefix combination)
having a hole in it.

Rule 8 (unsupervised ML anomaly detection, added in the V3.14 finalization
pass -- see rule_ml_anomaly_detection.py and BASELINE_CHECKPOINT_V3.14.md)
is ledger-intrinsic like Rules 1/2/3/5/7 (needs only general_ledger.csv,
no FY-matching tax document), so it lives in compute_ledger_intrinsic_flags()
alongside them and runs for BOTH ledgers via run_ledger_only_report() too.
It is reported as its own dataset-level section, same reporting shape as
Benford's Law, not merged into the per-leg list: unlike Rules 1/2/5 it
doesn't name a specific, explainable pattern for a given leg -- it reports
"the model found this leg's context unusual", which is a different kind of
evidence that would misleadingly look like one more named-pattern flag if
merged into the same list.

Convergence logic:
If a single ledger entry is flagged by MORE THAN ONE rule, that's stronger
evidence than being flagged by just one -- so entries with multiple flags
are ranked higher in the final report.

Severity tiers (Rules 1, 2, 5):
These three rules have no built-in signal of how material a flagged leg
is -- a round-number amount or a duplicate-candidate match can be Rs.20,000
or Rs.70,00,000, and those are not equally worth an auditor's time. They
share one materiality ladder (get_severity_by_amount), using the lakh/crore
bands standard in Indian audit practice, so severity is comparable across
rules instead of each rule inventing its own scale. Rule 4 already carried
its own severity (based on % gap between ITR and 26AS, which is a more
direct signal than amount alone) and is left as-is. Rule 3 is dataset-level,
not per-leg, so it is not tiered here.

Structured export:
Besides the console report, run_combined_report() writes the full result
to output/combined_report.json (everything, nested) and three flat CSVs
(output/ledger_leg_flags.csv, output/tax_reconciliation_flags.csv,
output/ml_anomaly_flags.csv) so the report can be consumed by something
other than a human reading a terminal -- a future frontend, a spreadsheet,
a regression test -- without re-parsing console text.
"""

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

# Build paths relative to this script's location, so it works on any
# machine regardless of where the project folder is placed.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
# Rule 6 and Rule 7 are imported, not duplicated inline -- see the module
# docstring's "Import, not duplicate, for Rule 6 (and now Rule 7)" section.
from rule_trial_balance_reconciliation import check_trial_balance_reconciliation
from rule_voucher_sequence_gap import check_voucher_sequence_gaps
from rule_duplicate_transaction import _is_invoice_rounding   # Rule 5's one rounding exclusion
# Rule 8 (ML anomaly layer, added as part of the V3.14 finalization pass) --
# imported for the same reason as Rules 6/7: its real work (feature
# engineering + an IsolationForest fit) is substantial logic worth keeping
# in one place, not a few lines safe to fork inline.
from rule_ml_anomaly_detection import detect_anomalies

DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "output")

GL_FILE = os.path.join(DATA_DIR, "general_ledger.csv")
ITR_FILE = os.path.join(DATA_DIR, "itr_summary.csv")
FORM26AS_FILE = os.path.join(DATA_DIR, "form26as.csv")

EXEMPT_ROUND_NUMBER_ACCOUNTS = {"Rent Expense"}
ROUND_NUMBER_MULTIPLE = 10000
RECON_TOLERANCE_RUPEES = 10.0

BENFORD_EXPECTED = {
    1: 30.1, 2: 17.6, 3: 12.5, 4: 9.7, 5: 7.9,
    6: 6.7, 7: 5.8, 8: 5.1, 9: 4.6,
}

# ---------- Shared severity ladder (Rules 1, 2, 5) ----------
SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def get_severity_by_amount(amount):
    """Materiality bands matching standard Indian audit convention
    (lakh / crore thresholds). Rs.10L+ = CRITICAL, Rs.1L+ = HIGH,
    Rs.10k+ (the round-number threshold itself) = MEDIUM, else LOW."""
    amount = abs(amount)
    if amount >= 1_000_000:
        return "CRITICAL"
    if amount >= 100_000:
        return "HIGH"
    if amount >= 10_000:
        return "MEDIUM"
    return "LOW"


def escalate_severity(severity, steps=1):
    idx = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[min(idx + steps, len(SEVERITY_ORDER) - 1)]


def max_severity(severities):
    if not severities:
        return "LOW"
    return max(severities, key=SEVERITY_ORDER.index)


# ---------- Rule 1: Segregation of duties ----------
# KNOWN LIMITATION ON REAL DATA (documented 2026-10-02, see the matching
# note in rule_segregation_of_duties.py): adapt_ledger_to_schema.py always
# writes entered_by="" / approved_by="" for real rows, since Tally ledger
# exports carry no ERP user-attribution metadata. This rule is therefore
# structurally inert against the current real pipeline -- not a bug, a
# data-availability gap. Left as-is rather than removed, since the logic
# is correct and would fire immediately if a future data source populated
# these fields.
def rule_segregation_of_duties(row):
    entered = (row.get("entered_by") or "").strip()
    approved = (row.get("approved_by") or "").strip()
    if entered and approved and entered == approved:
        amount = float(row.get("amount", 0) or 0)
        return {
            "rule": "segregation_of_duties",
            "reason": "Same person entered and approved this entry",
            "severity": get_severity_by_amount(amount),
        }
    return None


# ---------- Rule 2: Round-number bias ----------
# SEVERITY CHANGED 2026-10-07 (BASELINE_CHECKPOINT_V3.14.md, Addendum 17).
# Detection is unchanged -- every multiple of Rs.10,000 (Rs.10,000 is a
# meaningful figure in Indian tax practice, e.g. the Section 40A(3) cash
# payment limit) is still screened and still appears in the flags. What
# changed is that being round is no longer, by itself, a reason to rank a
# leg HIGH/CRITICAL: on the real ledgers 83-85% of all flagged legs were
# flagged ONLY for being round, ranked by size alone (e.g. a Rs.3,00,000
# bank credit = HIGH). A round amount on its own is now LOW (informational).
# It still reaches HIGH/CRITICAL when another independent rule fires on the
# same leg (duplicate transaction, segregation of duties): the severity of a
# leg is the highest of its reasons, and those rules carry their own
# amount-based severity, so corroboration restores the materiality ranking.
ROUND_NUMBER_ALONE_SEVERITY = "LOW"


def rule_round_number(row):
    if row["account"] in EXEMPT_ROUND_NUMBER_ACCOUNTS:
        return None
    amount = float(row["amount"])
    if amount % ROUND_NUMBER_MULTIPLE == 0:
        return {
            "rule": "round_number_bias",
            "reason": f"Suspiciously round amount ({amount})",
            "severity": ROUND_NUMBER_ALONE_SEVERITY,
        }
    return None


# ---------- Rule 3: Benford's Law (dataset-level, not per-row) ----------
def get_first_digit(amount):
    for char in str(abs(amount)):
        if char.isdigit() and char != "0":
            return int(char)
    return None


def rule_benfords_law(rows):
    from collections import Counter
    first_digits = [get_first_digit(float(r["amount"])) for r in rows]
    first_digits = [d for d in first_digits if d]
    total = len(first_digits)
    counts = Counter(first_digits)

    chi_square = 0.0
    for digit in range(1, 10):
        expected_pct = BENFORD_EXPECTED[digit]
        expected_count = (expected_pct / 100) * total
        actual_count = counts.get(digit, 0)
        chi_square += ((actual_count - expected_count) ** 2) / expected_count

    threshold = 15.5
    deviates = chi_square > threshold
    return chi_square, deviates


# ADDED 2026-10-02, alongside (not replacing) rule_benfords_law()'s existing
# chi-square check. Duplicates scripts/rule_benfords_law.py's compute_mad()/
# classify_mad_conformity() -- see that module's docstring for the full
# reasoning (chi-square's significance threshold scales with sample size,
# so at n=14,625 even immaterial deviations trigger "deviates"; MAD is
# sample-size independent and gives a useful second opinion).
MAD_CONFORMITY_BANDS = {
    "close": 0.006,
    "acceptable": 0.012,
    "marginal": 0.015,
}


def compute_mad(rows):
    from collections import Counter
    first_digits = [get_first_digit(float(r["amount"])) for r in rows]
    first_digits = [d for d in first_digits if d]
    total = len(first_digits)
    counts = Counter(first_digits)

    mad = 0.0
    for digit in range(1, 10):
        expected_proportion = BENFORD_EXPECTED[digit] / 100
        actual_proportion = counts.get(digit, 0) / total
        mad += abs(actual_proportion - expected_proportion)
    mad /= 9
    return mad


def classify_mad_conformity(mad):
    if mad < MAD_CONFORMITY_BANDS["close"]:
        return "Close conformity"
    elif mad < MAD_CONFORMITY_BANDS["acceptable"]:
        return "Acceptable conformity"
    elif mad < MAD_CONFORMITY_BANDS["marginal"]:
        return "Marginally acceptable"
    else:
        return "Nonconformity"


# ---------- Rule 4: ITR vs 26AS reconciliation ----------
# FIX (2026-10-02): claimed == 0 used to fall through to the plain
# "else MEDIUM" default below (pct_diff was set to 0, which never hit the
# >=30/>=10 branches), silently understating the one case where the
# percentage gap is actually undefined -- claimed nothing, but a deductor
# reported TDS regardless. Now matches rule_itr_26as_reconciliation.py's
# classify_severity(), which this duplicates: CRITICAL for any nonzero
# mismatch against a zero claim. See that function's docstring for the
# full reasoning.
def rule_reconciliation(form26as_rows):
    flags = []
    for row in form26as_rows:
        reported = float(row["tds_reported_by_deductor"])
        claimed = float(row["tds_claimed_in_itr_share"])
        mismatch = claimed - reported
        if abs(mismatch) > RECON_TOLERANCE_RUPEES:
            if claimed == 0:
                severity = "CRITICAL"
            else:
                pct_diff = abs(mismatch / claimed) * 100
                severity = "CRITICAL" if pct_diff >= 30 else "HIGH" if pct_diff >= 10 else "MEDIUM"
            flags.append({
                "entity": row["deductor_name"],
                "reason": f"ITR claims Rs.{claimed} but 26AS reports Rs.{reported} "
                          f"(gap: Rs.{mismatch:.2f})",
                "severity": severity,
                "source": "ITR-26AS Reconciliation",
            })
    return flags


# ---------- Rule 7: Voucher-number sequence gaps ----------
# No currency amount to apply get_severity_by_amount to -- a missing
# voucher number is equally "a hole in the series" whether that voucher
# would have been Rs.500 or Rs.5,00,000. Severity here is about how
# unusual the gap count is for the series, not about materiality.
def get_severity_by_gap_count(count):
    if count >= 10:
        return "HIGH"
    if count >= 5:
        return "MEDIUM"
    return "LOW"


# ---------- Rule 5: Duplicate transactions ----------
def rule_duplicate_transactions(rows):
    from collections import defaultdict
    groups = defaultdict(list)
    for idx, row in enumerate(rows):
        acct = (row.get("account") or "").strip()
        date = (row.get("date") or "").strip()
        dr_cr = (row.get("entry_type") or row.get("dr_cr") or "").strip()
        # Sub-rupee postings to a rounding account repeat by nature; same
        # exclusion as rule_duplicate_transaction.py (kept in step by the
        # parity test with rounding rows in test_rule_duplicate_transaction.py).
        if _is_invoice_rounding(row):
            continue
        try:
            amt = f"{float(row.get('amount', 0)):.2f}"
        except ValueError:
            amt = (row.get("amount") or "").strip()

        key = (date, acct, amt, dr_cr)
        groups[key].append((idx, row))

    duplicate_info = {}
    for key, items in groups.items():
        if len(items) < 2:
            continue
        vch_ids = set(r.get("entry_id") for idx, r in items)
        # Critical exclusion: Must span more than one distinct voucher
        if len(vch_ids) > 1:
            date, acct, amt, dr_cr = key
            severity = get_severity_by_amount(float(amt)) if amt else "LOW"
            # 3+ distinct vouchers sharing the same shape is a stronger
            # signal than a simple pairwise coincidence -- escalate one tier.
            if len(vch_ids) > 2:
                severity = escalate_severity(severity)
            for idx, r in items:
                other_vchs = sorted(list(vch_ids - {r.get("entry_id")}))
                duplicate_info[idx] = {
                    "rule": "duplicate_transaction",
                    "reason": (
                        f"Duplicate transaction candidate: matches voucher(s) "
                        f"{', '.join(other_vchs[:3])} on {date} ({dr_cr} {acct} Rs.{amt})"
                    ),
                    "severity": severity,
                }
    return duplicate_info


def compute_ledger_intrinsic_flags(gl_rows):
    """Everything Rules 1, 2, 3, 5 and 7 need is already IN general_ledger.csv
    -- no other FY-specific source document required. This is split out of
    run_combined_report() so a DIFFERENT ledger (a different financial
    year's reconstructed ledger, say) can get these same checks via
    run_ledger_only_report() below, without re-deriving this logic or
    duplicating it. Rules 4 (ITR vs 26AS) and 6 (trial balance vs ledger)
    are deliberately NOT here -- both need their own matching FY-specific
    source document (form26as.csv/itr_summary.csv, trial_balance_redacted.pdf)
    that a different ledger's FY won't have until those documents exist for
    it too; see run_ledger_only_report()'s docstring.

    Returns (sorted_flags, multi_signal, single_signal, severity_counts,
    voucher_gap_flags, chi_square, deviates, mad, mad_conformity,
    ml_anomaly_flags) -- the same values run_combined_report() previously
    computed inline, plus Rule 8's flags (added last, so every existing
    caller that unpacks this tuple positionally needed exactly one new
    name added at the end, nothing reordered).
    """
    # Pre-compute Rule 5 duplicate flags across the entire ledger
    duplicate_flags = rule_duplicate_transactions(gl_rows)

    # Collect per-transaction flags from Rule 1, Rule 2, and Rule 5 (keyed per leg)
    transaction_flags = {}  # leg_key -> leg data

    voucher_leg_counts = {}
    for idx, row in enumerate(gl_rows):
        vch_id = row["entry_id"]
        voucher_leg_counts[vch_id] = voucher_leg_counts.get(vch_id, 0) + 1
        leg_num = voucher_leg_counts[vch_id]

        reasons = []
        r1 = rule_segregation_of_duties(row)
        if r1:
            reasons.append(r1)
        r2 = rule_round_number(row)
        if r2:
            reasons.append(r2)
        r5 = duplicate_flags.get(idx)
        if r5:
            reasons.append(r5)

        if reasons:
            leg_key = f"{vch_id} (leg {leg_num})"
            transaction_flags[leg_key] = {
                "entry_id": vch_id,
                "leg_num": leg_num,
                "date": row["date"],
                "account": row["account"],
                "amount": row["amount"],
                "reasons": reasons,
                "signal_count": len(reasons),
                "severity": max_severity([r["severity"] for r in reasons]),
            }

    # Rule 7 flags (voucher-number sequence gaps) -- grouped per series
    # (vch_type, prefix) so one row in the report/export is "this series has
    # N missing numbers", not N separate rows for the same underlying gap
    # pattern. check_voucher_sequence_gaps() itself returns one dict per
    # missing number; group here purely for reporting.
    raw_gaps = check_voucher_sequence_gaps(gl_rows)
    gap_series = defaultdict(list)
    for g in raw_gaps:
        gap_series[(g["vch_type"], g["prefix"])].append(g)
    voucher_gap_flags = []
    for (vch_type, prefix), series_gaps in sorted(gap_series.items()):
        voucher_gap_flags.append({
            "vch_type": vch_type,
            "prefix": prefix,
            "missing_count": len(series_gaps),
            "group_min": series_gaps[0]["group_min"],
            "group_max": series_gaps[0]["group_max"],
            "group_size": series_gaps[0]["group_size"],
            "severity": get_severity_by_gap_count(len(series_gaps)),
            "missing_numbers": [g["missing_number"] for g in series_gaps],
        })

    # Rule 3 (dataset-level, separate from the above)
    chi_square, deviates = rule_benfords_law(gl_rows)
    mad = compute_mad(gl_rows)
    mad_conformity = classify_mad_conformity(mad)

    # Rule 8 (dataset-level, separate from the above -- see module docstring)
    ml_anomaly_flags = detect_anomalies(gl_rows)

    # Sort transaction-level flags: most signals first, then severity
    # (convergence logic -- multiple independent signals on the same leg
    # is stronger evidence than one, and within a signal-count tier the
    # more material amount sorts first)
    sorted_flags = sorted(
        transaction_flags.items(),
        key=lambda x: (x[1]["signal_count"], SEVERITY_ORDER.index(x[1]["severity"])),
        reverse=True,
    )
    multi_signal = [f for f in sorted_flags if f[1]["signal_count"] > 1]
    single_signal = [f for f in sorted_flags if f[1]["signal_count"] == 1]
    severity_counts = {s: 0 for s in SEVERITY_ORDER}
    for _, data in sorted_flags:
        severity_counts[data["severity"]] += 1

    return (
        sorted_flags, multi_signal, single_signal, severity_counts,
        voucher_gap_flags, chi_square, deviates, mad, mad_conformity,
        ml_anomaly_flags,
    )


def run_combined_report(write_exports=True):
    with open(GL_FILE, "r") as f:
        gl_rows = list(csv.DictReader(f))

    with open(FORM26AS_FILE, "r") as f:
        form26as_rows = list(csv.DictReader(f))

    (sorted_flags, multi_signal, single_signal, severity_counts,
     voucher_gap_flags, chi_square, deviates, mad, mad_conformity,
     ml_anomaly_flags) = (
        compute_ledger_intrinsic_flags(gl_rows)
    )

    # Rule 4 flags (reconciliation)
    reconciliation_flags = rule_reconciliation(form26as_rows)

    # Rule 6 flags (trial balance vs ledger reconciliation) -- reads its
    # own files (trial_balance_redacted.pdf, general_ledger.csv) via
    # check_trial_balance_reconciliation()'s own defaults, same as how
    # rule_reconciliation() above reads form26as_rows passed in rather
    # than re-deriving them.
    trial_balance_flags = check_trial_balance_reconciliation()

    # ---------- Print combined report ----------
    print("=" * 70)
    print("COMBINED IRREGULARITY REPORT")
    print("=" * 70)

    print("\n[DATASET-LEVEL CHECK] Benford's Law")
    print(f"  Chi-square: {chi_square:.2f} (threshold: 15.5)")
    if deviates:
        print("  WARNING: Overall number pattern deviates from natural distribution.")
    else:
        print("  Overall number pattern looks statistically natural.")
    print(f"  MAD (Nigrini, first-digit test): {mad:.5f} -- {mad_conformity}")

    print(f"\n[HIGH-CONFIDENCE] Tax Reconciliation Mismatches: {len(reconciliation_flags)}")
    for f in reconciliation_flags:
        print(f"  [{f['severity']}] {f['entity']}: {f['reason']}")

    print(f"\n[HIGH-CONFIDENCE] Trial Balance Reconciliation Mismatches: {len(trial_balance_flags)}")
    for f in trial_balance_flags:
        print(f"  [{f['severity']}] {f['account']} ({f['side']}): "
              f"TB=Rs.{f['tb_amount']:,.2f} GL=Rs.{f['gl_amount']:,.2f} "
              f"(gap: Rs.{f['mismatch_amount']:,.2f})")

    print(f"\n[HIGH-CONFIDENCE] Voucher-Number Sequence Gaps: "
          f"{sum(f['missing_count'] for f in voucher_gap_flags)} missing number(s) "
          f"across {len(voucher_gap_flags)} series")
    for f in voucher_gap_flags:
        label = f"{f['vch_type']} {f['prefix']}".strip()
        print(f"  [{f['severity']}] {label}: {f['missing_count']} missing in range "
              f"{f['group_min']}-{f['group_max']} (series size {f['group_size']})")

    print(f"\n[DATASET-LEVEL CHECK] ML Anomaly Detection (Isolation Forest, Rule 8)")
    ml_severity_counts = defaultdict(int)
    for f in ml_anomaly_flags:
        ml_severity_counts[f["severity"]] += 1
    print(f"  Flags: {len(ml_anomaly_flags)} (high={ml_severity_counts['high']}, "
          f"medium={ml_severity_counts['medium']}, low={ml_severity_counts['low']})")
    print("  Candidate list for review -- see rule_ml_anomaly_detection.py's "
          "docstring for what this does and does not catch.")

    print(f"\n[TRANSACTION-LEVEL] Ledger Legs Flagged: {len(sorted_flags)}")
    print(f"  Multi-signal (higher confidence): {len(multi_signal)}")
    print(f"  Single-signal (lower confidence): {len(single_signal)}")

    print(f"  By severity: " + ", ".join(
        f"{s}={severity_counts[s]}" for s in reversed(SEVERITY_ORDER)))

    if multi_signal:
        print("\n  Top multi-signal entries:")
        for leg_id, data in multi_signal[:5]:
            print(f"    [{data['severity']}] {leg_id} | {data['date']} | "
                  f"{data['account']} | Rs.{data['amount']}")
            for r in data["reasons"]:
                print(f"        - {r['reason']}")
    else:
        print("\n  Top multi-signal entries: None (0 entries with >1 signal)")
        print("\n  Sample single-signal entries:")
        for leg_id, data in single_signal[:5]:
            print(f"    [{data['severity']}] {leg_id} | {data['date']} | "
                  f"{data['account']} | Rs.{data['amount']}")
            for r in data["reasons"]:
                print(f"        - {r['reason']}")

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": {
            "general_ledger_file": os.path.abspath(GL_FILE),
            "total_legs": len(gl_rows),
        },
        "benford": {
            "chi_square": round(chi_square, 2),
            "threshold": 15.5,
            "deviates": deviates,
            "mad": round(mad, 5),
            "mad_conformity": mad_conformity,
        },
        "tax_reconciliation_flags": reconciliation_flags,
        "trial_balance_flags": trial_balance_flags,
        "voucher_sequence_gap_flags": voucher_gap_flags,
        "ml_anomaly_flags": ml_anomaly_flags,
        "ledger_leg_flags": [
            {"leg_key": leg_key, **data} for leg_key, data in sorted_flags
        ],
        "summary": {
            "total_legs_flagged": len(sorted_flags),
            "multi_signal": len(multi_signal),
            "single_signal": len(single_signal),
            "by_severity": severity_counts,
            "ml_anomaly_flags": len(ml_anomaly_flags),
        },
    }

    if write_exports:
        export_report(result)

    return result


def export_report(result):
    """Writes the full result to output/combined_report.json and three flat
    CSVs (ledger_leg_flags.csv, tax_reconciliation_flags.csv,
    ml_anomaly_flags.csv) for anything downstream that isn't a human
    reading the console."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    json_path = os.path.join(OUTPUT_DIR, "combined_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    leg_csv_path = os.path.join(OUTPUT_DIR, "ledger_leg_flags.csv")
    with open(leg_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "entry_id", "leg_num", "date", "account", "amount",
            "severity", "signal_count", "source_rules", "reasons",
        ])
        for leg in result["ledger_leg_flags"]:
            rules = "; ".join(r["rule"] for r in leg["reasons"])
            reasons = "; ".join(r["reason"] for r in leg["reasons"])
            writer.writerow([
                leg["entry_id"], leg["leg_num"], leg["date"], leg["account"],
                leg["amount"], leg["severity"], leg["signal_count"], rules, reasons,
            ])

    recon_csv_path = os.path.join(OUTPUT_DIR, "tax_reconciliation_flags.csv")
    with open(recon_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["entity", "severity", "reason", "source"])
        for flag in result["tax_reconciliation_flags"]:
            writer.writerow([flag["entity"], flag["severity"], flag["reason"], flag["source"]])

    ml_csv_path = os.path.join(OUTPUT_DIR, "ml_anomaly_flags.csv")
    with open(ml_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["entry_id", "account", "dr_cr", "amount", "date",
                          "vch_type", "severity", "anomaly_score", "anomaly_rank_pct"])
        for flag in result["ml_anomaly_flags"]:
            writer.writerow([
                flag["entry_id"], flag["account"], flag["dr_cr"], flag["amount"],
                flag["date"], flag["vch_type"], flag["severity"],
                flag["anomaly_score"], flag["anomaly_rank_pct"],
            ])

    tb_csv_path = os.path.join(OUTPUT_DIR, "trial_balance_flags.csv")
    with open(tb_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["account", "side", "tb_amount", "gl_amount",
                          "mismatch_amount", "severity", "variants_used"])
        for flag in result["trial_balance_flags"]:
            writer.writerow([
                flag["account"], flag["side"], flag["tb_amount"], flag["gl_amount"],
                flag["mismatch_amount"], flag["severity"],
                "; ".join(flag["variants_used"]),
            ])

    gap_csv_path = os.path.join(OUTPUT_DIR, "voucher_sequence_gap_flags.csv")
    with open(gap_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["vch_type", "prefix", "missing_count", "group_min",
                          "group_max", "group_size", "severity", "missing_numbers"])
        for flag in result["voucher_sequence_gap_flags"]:
            writer.writerow([
                flag["vch_type"], flag["prefix"], flag["missing_count"],
                flag["group_min"], flag["group_max"], flag["group_size"],
                flag["severity"],
                "; ".join(str(n) for n in flag["missing_numbers"]),
            ])


def run_ledger_only_report(gl_file, report_label, write_exports=True):
    """Runs the ledger-intrinsic rules (1, 2, 3, 5, 7, 8 -- see
    compute_ledger_intrinsic_flags()) against ANY general_ledger.csv-shaped
    file, not just data/general_ledger.csv. Built for a second financial
    year's reconstructed ledger (e.g. FY2025-26) to get the same checks
    the FY2024-25 ledger gets, without needing a second copy of this
    module or a second hardcoded GL_FILE.

    Deliberately excludes Rules 4 and 6 (and does not write to or read
    output/combined_report.json / data/form26as.csv / data/itr_summary.csv
    / trial_balance_redacted.pdf -- the FY2024-25 ones): those two rules
    each need their OWN matching-FY source document (a Form 26AS/ITR pair,
    a trial balance PDF) to compare the ledger against, and reusing the
    FY2024-25 copies of those documents against a different FY's ledger
    would silently compare the wrong year -- worse than not checking at
    all. Run them as their own, separate step once/if the matching
    documents for this FY exist, the same way GSTR reconciliation is kept
    separate from this report for the same reason (see module docstring).

    report_label is used only for output file naming (e.g. "FY2025-26" ->
    output/combined_report_FY2025-26.json), so running this for several
    ledgers never overwrites another one's output -- including never
    overwriting output/combined_report.json, which stays the FY2024-25
    report run_combined_report() produces.
    """
    with open(gl_file, "r") as f:
        gl_rows = list(csv.DictReader(f))

    (sorted_flags, multi_signal, single_signal, severity_counts,
     voucher_gap_flags, chi_square, deviates, mad, mad_conformity,
     ml_anomaly_flags) = (
        compute_ledger_intrinsic_flags(gl_rows)
    )

    print("=" * 70)
    print(f"LEDGER-ONLY IRREGULARITY REPORT -- {report_label}")
    print("(Rules 1/2/3/5/7/8 only -- no Form26AS/ITR or trial balance for "
          "this period on file; see run_ledger_only_report's docstring)")
    print("=" * 70)

    print("\n[DATASET-LEVEL CHECK] Benford's Law")
    print(f"  Chi-square: {chi_square:.2f} (threshold: 15.5)")
    if deviates:
        print("  WARNING: Overall number pattern deviates from natural distribution.")
    else:
        print("  Overall number pattern looks statistically natural.")
    print(f"  MAD (Nigrini, first-digit test): {mad:.5f} -- {mad_conformity}")

    print(f"\n[HIGH-CONFIDENCE] Voucher-Number Sequence Gaps: "
          f"{sum(f['missing_count'] for f in voucher_gap_flags)} missing number(s) "
          f"across {len(voucher_gap_flags)} series")
    for f in voucher_gap_flags:
        label = f"{f['vch_type']} {f['prefix']}".strip()
        print(f"  [{f['severity']}] {label}: {f['missing_count']} missing in range "
              f"{f['group_min']}-{f['group_max']} (series size {f['group_size']})")

    print(f"\n[DATASET-LEVEL CHECK] ML Anomaly Detection (Isolation Forest, Rule 8)")
    ml_severity_counts = defaultdict(int)
    for f in ml_anomaly_flags:
        ml_severity_counts[f["severity"]] += 1
    print(f"  Flags: {len(ml_anomaly_flags)} (high={ml_severity_counts['high']}, "
          f"medium={ml_severity_counts['medium']}, low={ml_severity_counts['low']})")

    print(f"\n[TRANSACTION-LEVEL] Ledger Legs Flagged: {len(sorted_flags)}")
    print(f"  Multi-signal (higher confidence): {len(multi_signal)}")
    print(f"  Single-signal (lower confidence): {len(single_signal)}")
    print(f"  By severity: " + ", ".join(
        f"{s}={severity_counts[s]}" for s in reversed(SEVERITY_ORDER)))

    if multi_signal:
        print("\n  Top multi-signal entries:")
        for leg_id, data in multi_signal[:5]:
            print(f"    [{data['severity']}] {leg_id} | {data['date']} | "
                  f"{data['account']} | Rs.{data['amount']}")
            for r in data["reasons"]:
                print(f"        - {r['reason']}")
    else:
        print("\n  Top multi-signal entries: None (0 entries with >1 signal)")
        print("\n  Sample single-signal entries:")
        for leg_id, data in single_signal[:5]:
            print(f"    [{data['severity']}] {leg_id} | {data['date']} | "
                  f"{data['account']} | Rs.{data['amount']}")
            for r in data["reasons"]:
                print(f"        - {r['reason']}")

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": {
            "general_ledger_file": os.path.abspath(gl_file),
            "report_label": report_label,
            "total_legs": len(gl_rows),
        },
        "benford": {
            "chi_square": round(chi_square, 2),
            "threshold": 15.5,
            "deviates": deviates,
            "mad": round(mad, 5),
            "mad_conformity": mad_conformity,
        },
        "voucher_sequence_gap_flags": voucher_gap_flags,
        "ml_anomaly_flags": ml_anomaly_flags,
        "ledger_leg_flags": [
            {"leg_key": leg_key, **data} for leg_key, data in sorted_flags
        ],
        "summary": {
            "total_legs_flagged": len(sorted_flags),
            "multi_signal": len(multi_signal),
            "single_signal": len(single_signal),
            "by_severity": severity_counts,
            "ml_anomaly_flags": len(ml_anomaly_flags),
        },
    }

    if write_exports:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        suffix = report_label.replace(" ", "_")
        json_path = os.path.join(OUTPUT_DIR, f"combined_report_{suffix}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        leg_csv_path = os.path.join(OUTPUT_DIR, f"ledger_leg_flags_{suffix}.csv")
        with open(leg_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "entry_id", "leg_num", "date", "account", "amount",
                "severity", "signal_count", "source_rules", "reasons",
            ])
            for leg in result["ledger_leg_flags"]:
                rules = "; ".join(r["rule"] for r in leg["reasons"])
                reasons = "; ".join(r["reason"] for r in leg["reasons"])
                writer.writerow([
                    leg["entry_id"], leg["leg_num"], leg["date"], leg["account"],
                    leg["amount"], leg["severity"], leg["signal_count"], rules, reasons,
                ])

        gap_csv_path = os.path.join(OUTPUT_DIR, f"voucher_sequence_gap_flags_{suffix}.csv")
        with open(gap_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["vch_type", "prefix", "missing_count", "group_min",
                              "group_max", "group_size", "severity", "missing_numbers"])
            for flag in result["voucher_sequence_gap_flags"]:
                writer.writerow([
                    flag["vch_type"], flag["prefix"], flag["missing_count"],
                    flag["group_min"], flag["group_max"], flag["group_size"],
                    flag["severity"],
                    "; ".join(str(n) for n in flag["missing_numbers"]),
                ])

        ml_csv_path = os.path.join(OUTPUT_DIR, f"ml_anomaly_flags_{suffix}.csv")
        with open(ml_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["entry_id", "account", "dr_cr", "amount", "date",
                              "vch_type", "severity", "anomaly_score", "anomaly_rank_pct"])
            for flag in result["ml_anomaly_flags"]:
                writer.writerow([
                    flag["entry_id"], flag["account"], flag["dr_cr"], flag["amount"],
                    flag["date"], flag["vch_type"], flag["severity"],
                    flag["anomaly_score"], flag["anomaly_rank_pct"],
                ])
        print(f"\n[EXPORT] Wrote structured report to:\n  {json_path}")

    return result


if __name__ == "__main__":
    run_combined_report()
