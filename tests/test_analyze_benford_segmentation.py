"""
Tests for analyze_benford_segmentation.py's run_segmentation_report().

This script was originally a read-only, manually-run diagnostic (see its
module docstring) with no test coverage -- fine while nothing downstream
depended on its exact output. That changed 2026-10-02 when the dashboard's
Benford tab started consuming its structured result directly, so a
regression here would silently misinform the dashboard's "why trust MAD
over chi-square" explanation. These tests pin:
  1. the real numbers (so a future edit to this script or to
     general_ledger.csv that changes the segmentation story gets caught),
  2. the shape run_segmentation_report() returns (so the dashboard's own
     consumption of it is exercised against the real function, not just a
     hand-built fixture).
"""
import pytest

from analyze_benford_segmentation import run_segmentation_report, chi_square_for


@pytest.fixture(scope="module")
def real_segmentation():
    return run_segmentation_report(write_export=False)


def test_overall_matches_rule_benfords_law(real_segmentation):
    """The "overall" figure here must match rule_benfords_law.py's own
    output exactly -- it's the same chi-square/MAD math over the same
    general_ledger.csv, just computed independently in this script. A
    mismatch would mean the two implementations have drifted apart."""
    overall = real_segmentation["overall"]
    assert overall["n"] == 14625
    assert overall["chi_square"] == pytest.approx(229.74, abs=0.01)
    assert overall["deviates"] is True
    assert overall["mad"] == pytest.approx(0.01061, abs=0.00001)
    assert overall["mad_conformity"] == "Acceptable"


def test_by_vch_type_covers_every_voucher_type_exactly_once(real_segmentation):
    segments = real_segmentation["by_vch_type"]
    names = [s["segment"] for s in segments]
    assert len(names) == len(set(names))  # no duplicates
    total_n = sum(s["n"] for s in segments)
    assert total_n == real_segmentation["overall"]["n"]  # every leg counted once


def test_by_vch_type_sorted_largest_first(real_segmentation):
    segments = real_segmentation["by_vch_type"]
    ns = [s["n"] for s in segments]
    assert ns == sorted(ns, reverse=True)


def test_by_account_limited_to_15(real_segmentation):
    assert len(real_segmentation["by_account"]) == 15


def test_breadth_of_deviation_pins_the_real_finding(real_segmentation):
    """This is the specific finding the dashboard's Benford tab needs to
    surface: chi-square deviates across essentially every substantial
    segment, INCLUDING both exclusion checks that strip out the most
    obvious repetitive-pricing candidates -- not just the heaviest-volume
    ones. That breadth is what supports "sample-size/structural-pricing
    artifact" over "fraud concentrated somewhere specific"."""
    # Every vch_type segment with a non-trivial sample size deviates.
    substantial = [s for s in real_segmentation["by_vch_type"] if s["n"] >= 10]
    assert all(s["deviates"] for s in substantial)
    assert len(substantial) >= 7  # most of the 10 real voucher types

    # Both "remove the obvious suspects" exclusion checks still deviate --
    # the deviation isn't concentrated in the parts removed.
    for check in real_segmentation["exclusion_checks"]:
        assert check["deviates"] is True


def test_purchase_and_contra_are_the_most_extreme_segments(real_segmentation):
    """Pins the specific real outliers worth flagging for a manual look
    (see the dashboard's Benford tab) -- Purchase and Contra have chi-
    square values an order of magnitude above every other substantial
    segment, and are the two segments to name explicitly rather than
    averaging away."""
    by_chi = sorted(real_segmentation["by_vch_type"], key=lambda s: -s["chi_square"])
    top_two = {s["segment"] for s in by_chi[:2]}
    assert top_two == {"Purchase", "Contra"}


def test_chi_square_for_empty_rows_returns_none_tuple():
    assert chi_square_for([]) == (None, None, 0, None, None)


def test_run_segmentation_report_writes_export(tmp_path, monkeypatch):
    import analyze_benford_segmentation as abs_mod
    monkeypatch.setattr(abs_mod, "OUTPUT_DIR", str(tmp_path))
    result = abs_mod.run_segmentation_report(write_export=True)

    exported_path = tmp_path / "benford_segmentation.json"
    assert exported_path.exists()
    import json
    with open(exported_path) as f:
        on_disk = json.load(f)
    assert on_disk["overall"]["n"] == result["overall"]["n"]
