"""
Regression test for the 2026-10-02 fix: generate_data.py used to default to
writing general_ledger.csv/itr_summary.csv/form26as.csv straight into data/
-- the exact same paths as the real, reconstructed ledger and tax data --
with no guard at all. These tests pin the fix: a safe default output
directory, and an explicit refusal to write into the real data/ directory
without an explicit override.
"""
import os

import pytest

import generate_data as gd


def test_default_output_dir_is_not_the_real_data_dir():
    """The default OUTPUT_DIR must never resolve to the same directory as
    the real committed data/ files (general_ledger.csv, itr_summary.csv,
    form26as.csv) -- that was the entire landmine."""
    assert os.path.abspath(gd.OUTPUT_DIR) != gd.REAL_DATA_DIR
    # ...and it must still live under the project's data/ tree, not
    # somewhere unrelated, so it's easy to find and clean up.
    assert os.path.abspath(gd.OUTPUT_DIR).startswith(gd.REAL_DATA_DIR)


def test_guard_refuses_to_target_real_data_dir_by_default():
    with pytest.raises(SystemExit, match="Refusing to write synthetic data"):
        gd._guard_against_real_data_overwrite(gd.REAL_DATA_DIR, force=False)


def test_guard_allows_real_data_dir_when_forced():
    # Should NOT raise.
    gd._guard_against_real_data_overwrite(gd.REAL_DATA_DIR, force=True)


def test_guard_allows_any_other_directory_unforced(tmp_path):
    # Should NOT raise -- only the exact real data/ path is special-cased.
    gd._guard_against_real_data_overwrite(str(tmp_path), force=False)


def test_generators_write_to_explicit_output_dir_not_real_data(tmp_path):
    """End-to-end: run the real generator functions against a scratch
    directory and confirm the real data/ files are untouched."""
    real_gl = os.path.join(gd.REAL_DATA_DIR, "general_ledger.csv")
    real_itr = os.path.join(gd.REAL_DATA_DIR, "itr_summary.csv")
    real_26as = os.path.join(gd.REAL_DATA_DIR, "form26as.csv")
    before = {}
    for path in (real_gl, real_itr, real_26as):
        if os.path.exists(path):
            before[path] = os.path.getmtime(path)

    out_dir = str(tmp_path / "synthetic_scratch")
    ledger_rows = gd.generate_general_ledger(n_entries=20, output_dir=out_dir)
    gd.generate_itr_and_26as(ledger_rows, output_dir=out_dir)

    assert os.path.exists(os.path.join(out_dir, "general_ledger.csv"))
    assert os.path.exists(os.path.join(out_dir, "itr_summary.csv"))
    assert os.path.exists(os.path.join(out_dir, "form26as.csv"))

    for path, mtime in before.items():
        assert os.path.getmtime(path) == mtime, (
            f"{path} was modified by generate_data.py -- the real-data "
            "overwrite landmine has regressed"
        )
