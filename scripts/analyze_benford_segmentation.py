"""
analyze_benford_segmentation.py
--------------------------------
Read-only diagnostic for the open question raised in the 2026-10-02 rule-
engine stress-test findings report: the real general_ledger.csv already
deviates from Benford's Law (chi-square 229.74, vs. the 15.5 threshold)
even though the ledger itself has been independently, thoroughly audited
and verified clean (BASELINE_CHECKPOINT_V3.3 through V3.10).

This script does NOT modify any data or any rule. It only re-runs the same
chi-square math rule_benfords_law.py already uses, segmented by vch_type
and by account, to see whether the deviation:
  (a) concentrates in a small number of structurally repetitive segments
      (consistent with a petrol pump's naturally fixed fuel-unit pricing,
      round salaries, and fixed GST slabs breaking Benford's organic-
      magnitude assumption -- a structural false positive), or
  (b) spreads broadly across unrelated segments (which would be more
      consistent with an actual irregularity and warrant deeper
      investigation).

UPDATED 2026-10-02: each segment now also reports Nigrini's MAD (and its
conformity band), alongside chi-square, for the same reason MAD was added
to rule_benfords_law.py/combined_report.py as a second, sample-size-
independent metric -- chi-square's significance threshold scales with
sample size, so smaller segments can swing wildly on chi-square alone.
Still read-only; no rule or threshold is changed by this.

UPDATED 2026-10-02 (again, same day -- dashboard integration): added
run_segmentation_report(), which returns this same analysis as structured
data (and optionally exports it to output/benford_segmentation.json),
following the same run_*_report() convention already used by
combined_report.py and rule_gstr_reconciliation.py. This was added because
the dashboard's Benford tab showed the chi-square-vs-MAD contradiction with
no way for a reader to see WHY the project treats the overall chi-square
"deviates" verdict as likely driven by sample size / this petrol pump's
structurally fixed pricing (fuel unit prices, GST slabs) rather than actual
fraud -- that reasoning lived only in this script's docstring and whoever's
terminal happened to run it, not in anything a reader of the dashboard
could see. The __main__ block below still prints the exact same text it
always has; it just gets its numbers from the structured result now
instead of recomputing them, so there's one source of truth for both.

Run with: python scripts/analyze_benford_segmentation.py
"""
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GL_FILE = os.path.join(SCRIPT_DIR, "..", "data", "general_ledger.csv")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "output")

# The two candidate explanations this script was built to distinguish (see
# module docstring): removing the 3 highest-volume accounts, and removing
# every fuel-sale voucher type, are the two most obvious "maybe it's just
# repetitive fuel pricing" hypotheses -- kept as named constants (rather
# than inline literals) so run_segmentation_report() and the old __main__
# printing can't silently drift apart on which accounts/types they mean.
REPETITIVE_ACCOUNT_CANDIDATES = sorted({"Cash", "Sales", "J & K Bank Ltd"})
FUEL_SALE_VCH_TYPES = sorted({"Sales Cash", "Sales GST", "Sales"})

BENFORD_EXPECTED = {
    1: 30.1, 2: 17.6, 3: 12.5, 4: 9.7, 5: 7.9,
    6: 6.7, 7: 5.8, 8: 5.1, 9: 4.6,
}
THRESHOLD = 15.5
MAD_CONFORMITY_BANDS = {
    "close": 0.006,
    "acceptable": 0.012,
    "marginal": 0.015,
}


def get_first_digit(amount):
    for char in str(abs(amount)):
        if char.isdigit() and char != "0":
            return int(char)
    return None


def classify_mad_conformity(mad):
    if mad < MAD_CONFORMITY_BANDS["close"]:
        return "Close"
    elif mad < MAD_CONFORMITY_BANDS["acceptable"]:
        return "Acceptable"
    elif mad < MAD_CONFORMITY_BANDS["marginal"]:
        return "Marginal"
    else:
        return "Nonconformity"


def chi_square_for(rows):
    first_digits = [get_first_digit(float(r["amount"])) for r in rows]
    first_digits = [d for d in first_digits if d]
    total = len(first_digits)
    if total == 0:
        return None, None, 0, None, None
    counts = Counter(first_digits)
    chi_square = 0.0
    mad = 0.0
    for digit in range(1, 10):
        expected_pct = BENFORD_EXPECTED[digit]
        expected_count = (expected_pct / 100) * total
        actual_count = counts.get(digit, 0)
        chi_square += ((actual_count - expected_count) ** 2) / expected_count
        mad += abs((actual_count / total) - (expected_pct / 100))
    mad /= 9
    return chi_square, chi_square > THRESHOLD, total, mad, classify_mad_conformity(mad)


def _segment_rows(rows, group_by_key, limit=None):
    """Shared helper: groups rows by a field, runs chi_square_for() on each
    group, sorted largest-first. Used for both the vch_type and account
    breakdowns, which were previously two near-identical copy-pasted loops
    in main()."""
    groups = defaultdict(list)
    for r in rows:
        groups[r[group_by_key]].append(r)
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    if limit is not None:
        ordered = ordered[:limit]
    overall_n = len(rows)
    segments = []
    for key, group in ordered:
        chi, dev, n, mad, band = chi_square_for(group)
        segments.append({
            "segment": key, "n": n,
            "pct_of_total": 100 * n / overall_n if overall_n else 0.0,
            "chi_square": chi, "deviates": dev, "mad": mad, "mad_conformity": band,
        })
    return segments


def run_segmentation_report(write_export=True):
    """Runs the full segmentation analysis (module docstring) and returns
    it as structured data -- generated_at / overall / by_vch_type /
    by_account / exclusion_checks -- the same run_*_report() shape used
    elsewhere in this project (combined_report.py, rule_gstr_reconciliation.py).
    Optionally exports to output/benford_segmentation.json so a dashboard
    or anything else downstream has one stable JSON source for this
    analysis instead of having to re-run or re-parse this script's text
    output."""
    with open(GL_FILE, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    overall_chi, overall_dev, overall_n, overall_mad, overall_band = chi_square_for(rows)

    by_vch_type = _segment_rows(rows, "vch_type")
    by_account = _segment_rows(rows, "account", limit=15)

    remainder = [r for r in rows if r["account"] not in REPETITIVE_ACCOUNT_CANDIDATES]
    chi_r, dev_r, n_r, mad_r, band_r = chi_square_for(remainder)

    non_fuel = [r for r in rows if r["vch_type"] not in FUEL_SALE_VCH_TYPES]
    chi_f, dev_f, n_f, mad_f, band_f = chi_square_for(non_fuel)

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall": {
            "n": overall_n, "chi_square": overall_chi, "deviates": overall_dev,
            "mad": overall_mad, "mad_conformity": overall_band,
        },
        "by_vch_type": by_vch_type,
        "by_account": by_account,
        "exclusion_checks": [
            {
                "label": f"Excluding {', '.join(REPETITIVE_ACCOUNT_CANDIDATES)} "
                         f"(the 3 highest-volume accounts)",
                "n": n_r, "pct_of_total": 100 * n_r / overall_n if overall_n else 0.0,
                "chi_square": chi_r, "deviates": dev_r, "mad": mad_r, "mad_conformity": band_r,
            },
            {
                "label": f"Excluding vch_type in {', '.join(FUEL_SALE_VCH_TYPES)} "
                         f"(fuel-sale voucher types)",
                "n": n_f, "pct_of_total": 100 * n_f / overall_n if overall_n else 0.0,
                "chi_square": chi_f, "deviates": dev_f, "mad": mad_f, "mad_conformity": band_f,
            },
        ],
    }

    if write_export:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        json_path = os.path.join(OUTPUT_DIR, "benford_segmentation.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(result, jf, indent=2, ensure_ascii=False)
        print(f"[EXPORT] Wrote structured report to:\n  {json_path}")

    return result


def main():
    result = run_segmentation_report()
    overall = result["overall"]

    print("=" * 78)
    print(f"OVERALL: n={overall['n']}, chi-square={overall['chi_square']:.2f}, "
          f"deviates={overall['deviates']}, MAD={overall['mad']:.5f} "
          f"({overall['mad_conformity']})")
    print("=" * 78)

    print("\nBy vch_type:")
    for seg in result["by_vch_type"]:
        print(f"  {seg['segment']:16s} n={seg['n']:6d} ({seg['pct_of_total']:5.1f}% of all legs)  "
              f"chi-square={seg['chi_square']:8.2f}  deviates={seg['deviates']!s:5s}  "
              f"MAD={seg['mad']:.5f} ({seg['mad_conformity']})")

    print("\nBy account (top 15 by leg count):")
    for seg in result["by_account"]:
        print(f"  {seg['segment']:42s} n={seg['n']:6d} ({seg['pct_of_total']:5.1f}% of all legs)  "
              f"chi-square={seg['chi_square']:8.2f}  deviates={seg['deviates']!s:5s}  "
              f"MAD={seg['mad']:.5f} ({seg['mad_conformity']})")

    for check in result["exclusion_checks"]:
        print(f"\n{check['label']}: n={check['n']} ({check['pct_of_total']:.1f}% of all legs), "
              f"chi-square={check['chi_square']:.2f}, deviates={check['deviates']}, "
              f"MAD={check['mad']:.5f} ({check['mad_conformity']})")


if __name__ == "__main__":
    main()
