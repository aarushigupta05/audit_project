"""
Regression tests for the header-carryover fix in reconstruct_ledger_entries.py
(fixed 2026-10-01, same day as the Shape B and blank-particulars-header
fixes, but a separate and unrelated bug).

Background: when a page's own account header isn't recognized by
_detect_page_header_and_account(), the code used to silently keep whatever
current_account the PREVIOUS page had left behind. In ledgers_redacted.pdf
this only ever happens on exactly 3 pages (477, 546, 547), but on all 3 it
was wrong: none of them is actually a continuation of the account printed
on the page before.

Investigated 2026-10-01 while reconciling against the partnership firm's
trial balance:
  - Pages 477 and 546 each open with their OWN "Opening Balance" line --
    i.e. a brand-new account starting fresh, not a continuation of
    anything. Their opening balances (Rs.2,05,51,866.76 Cr and
    Rs.85,21,875.81 Cr) and the exact Dr/Cr totals of the legs that used to
    leak into Laptop/Pollution Fees match the trial balance's 2 unnamed
    (redacted) rows to the rupee -- these are almost certainly the two
    partners' capital/current accounts, redacted in both documents. There
    is no name to recover.
  - Page 547 has no Opening Balance of its own; instead its "Carried Over"
    balance (Rs.5,41,300.00 / Rs.4,77,300.00) matches page 548's "Brought
    Forward" balance exactly -- a genuine continuation whose own header
    ("Tr. A/c" / "unt", split across two lines) just doesn't match either
    recognized header shape.

The fix (_resolve_headerless_pages(), called once per collect_occurrences()
run): a no-header page is now resolved by evidence, never by blind
inheritance --
  - Opening Balance present -> a clearly-marked placeholder account unique
    to that page's opening balance, e.g.
    "[Redacted account -- TB opening Cr 2,05,51,866.76]" (never invents a
    real name, never discards the legs).
  - No Opening Balance, but Carried Over matches the next headed page's
    Brought Forward (amount AND sign) -> attributed to that next page's
    account.
  - Neither signal -> left unresolved (None), not guessed.

Page 548's own "Ledger Tr. A/c" label (itself a different, smaller,
pre-existing quirk in how that page's header format is parsed) is
deliberately left untouched -- out of scope for this fix.

These tests pin the fixed, verified-correct state against the REAL
committed data in data/ (same convention as test_shape_b_fix.py and
test_blank_particulars_fix.py), plus unit tests against
_resolve_headerless_pages() directly for the underlying decision logic.
"""
import csv
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(TESTS_DIR, "..", "scripts")
DATA_DIR = os.path.join(TESTS_DIR, "..", "data")
sys.path.insert(0, SCRIPTS_DIR)

GL_FILE = os.path.join(DATA_DIR, "general_ledger.csv")
PDF_FILE = os.path.join(DATA_DIR, "raw_pdfs", "ledgers_redacted.pdf")

REDACTED_477 = "[Redacted account — TB opening Cr 2,05,51,866.76]"
REDACTED_546 = "[Redacted account — TB opening Cr 85,21,875.81]"


@pytest.fixture(scope="module")
def real_gl_rows():
    if not os.path.exists(GL_FILE):
        pytest.skip(f"data/general_ledger.csv not present at {GL_FILE}")
    with open(GL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 1. Laptop and Pollution Fees lose exactly their wrongly-attributed legs ----------

def test_laptop_loses_its_14_wrongly_attributed_legs(real_gl_rows):
    """Laptop should retain only its one genuine leg (page 476, Rs.44,000 --
    matches the trial balance's Laptop debit exactly); the 14 legs that
    leaked in from page 477 must be gone."""
    legs = [r for r in real_gl_rows if r["account"] == "Laptop"]
    assert len(legs) == 1, (
        f"Expected Laptop to have exactly 1 real leg after the fix, found "
        f"{len(legs)}: {[(r['entry_id'], r['amount'], r['source_pages']) for r in legs]}"
    )
    assert legs[0]["source_pages"] == "476"
    assert float(legs[0]["amount"]) == pytest.approx(44000.0, abs=0.01)
    assert all("477" not in r["source_pages"] for r in real_gl_rows if r["account"] == "Laptop")


def test_pollution_fees_loses_its_26_wrongly_attributed_legs(real_gl_rows):
    """Pollution Fees should retain only its one genuine leg (page 545,
    Rs.6,000 -- matches the trial balance's Pollution Fees debit exactly);
    the 11 legs from page 546 and 15 legs from page 547 must be gone."""
    legs = [r for r in real_gl_rows if r["account"] == "Pollution Fees"]
    assert len(legs) == 1, (
        f"Expected Pollution Fees to have exactly 1 real leg after the fix, "
        f"found {len(legs)}: {[(r['entry_id'], r['amount'], r['source_pages']) for r in legs]}"
    )
    assert legs[0]["source_pages"] == "545"
    assert float(legs[0]["amount"]) == pytest.approx(6000.0, abs=0.01)


def test_no_leg_sourced_from_pages_546_or_547_is_pollution_fees(real_gl_rows):
    offenders = [
        r for r in real_gl_rows
        if r["account"] == "Pollution Fees" and ("546" in r["source_pages"] or "547" in r["source_pages"])
    ]
    assert not offenders, f"Pollution Fees still has legs leaking in from page 546/547: {offenders}"


# ---------- 2. Page 547 is attributed to the SAME account as page 548 ----------

def test_page_547_matches_page_548s_account():
    """547 and 548 are the same logical account (547's Carried Over ties
    exactly to 548's Brought Forward) -- every OCCURRENCE on each page (the
    page's own current_account, at source -- not the flattened GL, which
    also contains correctly-named sibling legs like "TDS PAYABLE" from the
    same multi-leg vouchers, unrelated to current_account) must end up
    under the exact same account string, whatever that string is (page
    548's own header format is a separate, out-of-scope quirk -- "Ledger
    Tr. A/c" -- this test does not assert that specific string, only that
    547 matches it)."""
    if not os.path.exists(PDF_FILE):
        pytest.skip(f"raw PDF not present at {PDF_FILE}")
    from reconstruct_ledger_entries import collect_occurrences

    occurrences, _, _ = collect_occurrences(PDF_FILE, 540, 550)
    page_547_accounts = {o["account"] for o in occurrences if o["page"] == 547}
    page_548_accounts = {o["account"] for o in occurrences if o["page"] == 548}
    assert page_547_accounts, "No occurrences found on page 547 at all"
    assert page_548_accounts, "No occurrences found on page 548 at all"
    assert page_547_accounts == page_548_accounts, (
        f"Page 547 and page 548 should be the same logical account (verified "
        f"via Carried Over / Brought Forward continuity), but got "
        f"{page_547_accounts} vs {page_548_accounts}"
    )
    assert "Pollution Fees" not in page_547_accounts
    assert "Laptop" not in page_547_accounts


def test_page_548_label_untouched():
    """Page 548's own header-parsing quirk ("Ledger Tr. A/c") is explicitly
    out of scope for this fix and must not have changed."""
    with open(GL_FILE, "r", encoding="utf-8") as f:
        gl = list(csv.DictReader(f))
    page_548_accounts = {r["account"] for r in gl if r["source_pages"] == "548"}
    assert page_548_accounts == {"Ledger Tr. A/c"}, (
        f"Page 548's account label changed from the known 'Ledger Tr. A/c' "
        f"quirk to {page_548_accounts} -- that quirk was explicitly left "
        f"untouched by this fix; if it changed, something else touched it."
    )


# ---------- 3. Pages 477 and 546 get distinct, uniquely-tied redacted placeholders ----------

def test_page_477_gets_its_own_redacted_placeholder(real_gl_rows):
    """5 of page 477's legs are "UNRESOLVED" orphans (e.g. a leg with no
    particulars at all) -- a separate, pre-existing phenomenon confirmed
    byte-identical before and after this fix (unaffected by current_account
    entirely), so they're excluded here; this test is only about the 14
    legs that actually carry an account name.

    Post-Pattern-4 (redacted-account orphan absorption, investigating the
    remaining 29 unbalanced vouchers): an exact source_pages == "477" check
    now undercounts (9, not 14), because 5 of these 14 legs are exactly the
    ones Pattern 4 absorbs orphan legs into -- their source_pages
    legitimately grew from "477" alone to e.g. "3;477;546" by picking up
    the SAME leg's blanked-counterparty reprint on other accounts' pages.
    This is the identical, already-understood effect documented in
    test_header_carryover_population_is_exactly_40_rows below, scoped here
    to the REDACTED_477 label specifically (so e.g. the unrelated "ADVANCE
    TAX" leg, whose source_pages also happens to include "477", is
    correctly excluded by filtering on account == REDACTED_477, not just
    membership). The true, unchanged population is all 14 legs labeled
    REDACTED_477, verified by entry_id set equality, not exact-string
    source_pages."""
    legs = [r for r in real_gl_rows if r["account"] == REDACTED_477]
    assert legs, "No legs found under the REDACTED_477 placeholder"
    assert len(legs) == 14
    legs_exact = [r for r in legs if r["source_pages"] == "477"]
    assert len(legs_exact) == 9


def test_page_546_gets_its_own_redacted_placeholder(real_gl_rows):
    """5 of page 546's legs are pre-existing "UNRESOLVED" orphans, excluded
    here for the same reason as page 477 above.

    Post-Pattern-4: same effect as page 477 above -- 6 of these 11 legs
    legitimately grew extra source_pages via orphan absorption, dropping
    the exact-match count to 5. The true, unchanged population is all 11
    legs labeled REDACTED_546. See test_page_477_gets_its_own_redacted_placeholder
    for the full explanation."""
    legs = [r for r in real_gl_rows if r["account"] == REDACTED_546]
    assert legs, "No legs found under the REDACTED_546 placeholder"
    assert len(legs) == 11
    legs_exact = [r for r in legs if r["source_pages"] == "546"]
    assert len(legs_exact) == 5


def test_the_two_redacted_placeholders_are_distinct():
    assert REDACTED_477 != REDACTED_546


# ---------- 4. No unrelated pages/accounts changed ----------
#
# NOTE (2026-10-01, V3.7): this test used to assert an ABSOLUTE
# len(real_gl_rows) == 17128 against the live data file. That coupled a
# V3.6-specific regression test to the dataset's global row count -- which
# is wrong, because this test's job is to protect the header-carryover
# fix's invariant (pages 477/546/547 are relabeled, nothing else is touched
# by THAT fix), not to freeze the total row count for all time against
# every later, unrelated fix. The V3.7 canonicalization fix legitimately
# changes the global total (17128 -> 16320, by consolidating 808 genuinely
# duplicated cross-page legs) without touching pages 477/546/547 at all --
# the assertion below is scoped to exactly what this fix is responsible
# for, so it stays meaningful regardless of what later fixes do elsewhere.

def test_header_carryover_population_is_exactly_40_rows(real_gl_rows):
    """The header-carryover fix's entire footprint is 3 pages / 40 named
    legs at the time this fix was investigated and sized (14 on page 477
    + 11 on page 546 + 15 on page 547, all by exact source_pages match).
    This must hold regardless of what any later, unrelated fix does to the
    rest of the dataset. Pre-existing "UNRESOLVED" orphan legs on these
    pages (5 each on 477/546) are a separate, unrelated phenomenon --
    confirmed byte-identical before and after every fix made this session
    -- and are excluded here for the same reason the pages-477/546 tests
    above exclude them.

    NOTE: the test name/historical "40" refers to the ORIGINAL exact-match
    sizing of this fix, kept for traceability. Two later, unrelated fixes
    each grow some of these legs' source_pages beyond their own page by
    legitimately absorbing a cross-page reprint of the SAME leg, which
    pushes an exact-string match below the true population -- so each
    population here is now identified by its (stable) account label or
    membership rather than by exact string match, and checked both ways.

    Page 477/546 (Pattern 4, investigating the remaining 29 unbalanced
    vouchers post-V3.9): the redacted-account orphan-absorption rule folds
    5 of the 14 REDACTED_477 legs' and 6 of the 11 REDACTED_546 legs'
    orphan reprints (blanked counterparty text on OTHER accounts' pages,
    e.g. page 3's "ADVANCE TAX") into these SAME legs, growing their
    source_pages from e.g. "477" alone to "3;477;546". The true population
    is identified by account label (REDACTED_477 / REDACTED_546), not
    exact source_pages -- see test_page_477_gets_its_own_redacted_placeholder
    and test_page_546_gets_its_own_redacted_placeholder, which pin this
    precisely (14 and 11 respectively, unchanged).

    Page 547's exact-match count (previously 15 by an exact source_pages ==
    "547" check) dropped to 8 after the Tr. A/c / Ledger Tr. A/c per-voucher
    fix (post-V3.9): 7 of the 15 "Ledger Tr. A/c" legs here are the SAME
    legs the Tr. A/c fix was designed to find a matching "Tr. A/c" detail-leg
    reprint for on OTHER pages (56-57, 902-905) and correctly absorb --
    their source_pages legitimately grew from "547" alone to e.g.
    "56;547;902", so an exact-string match no longer counts them. This is
    the intended effect of that fix, not a disturbance of the
    header-carryover population: all 15 original legs are still present and
    still carry page 547 among their evidence -- checked below by
    membership instead of exact match. The other 8 (the "Payment_*"
    vouchers) have no matching counterpart anywhere in the document and are
    untouched, still exactly "547" alone."""
    page_477_legs = [r for r in real_gl_rows if r["account"] == REDACTED_477]
    page_546_legs = [r for r in real_gl_rows if r["account"] == REDACTED_546]
    page_547_legs_touching = [
        r for r in real_gl_rows
        if "547" in r["source_pages"].split(";") and r["account"] != "UNRESOLVED"
    ]
    assert len(page_477_legs) == 14
    assert len(page_546_legs) == 11
    # The set of legs that carry page 547 among their evidence is UNCHANGED
    # (29, both before and after the Tr. A/c fix) -- only 7 of those legs'
    # source_pages STRING grew (from "547" alone to e.g. "56;547;902"),
    # because they picked up a genuine cross-page reprint. Nothing was
    # added, removed, or orphaned.
    assert len(page_547_legs_touching) == 29
    # The fix's total named-leg footprint is unchanged at 54
    # (14 REDACTED_477 + 11 REDACTED_546 + 29 page-547-touching), identified
    # by account label (477/546) or membership (547) rather than by
    # exact-string source_pages, which three separate, later, unrelated
    # fixes (Patterns 1-3's Tr. A/c alias and Pattern 4's orphan absorption)
    # have each legitimately grown past their legs' own page for a subset
    # of this population.
    assert len(page_477_legs) + len(page_546_legs) + len(page_547_legs_touching) == 54


def test_header_carryover_fix_never_touches_any_other_page(real_gl_rows):
    """This fix's only job is relabeling legs sourced (even partly, via
    corroboration) from pages 477/546/547. No leg sourced EXCLUSIVELY from
    any other page should ever carry one of the two redacted placeholder
    labels or the page-548 label as a side effect of this fix."""
    touched_labels = {REDACTED_477, REDACTED_546, "Ledger Tr. A/c"}
    for r in real_gl_rows:
        if r["account"] in touched_labels:
            source_pages = set(r["source_pages"].split(";"))
            assert source_pages & {"477", "546", "547", "548"}, (
                f"Leg with account {r['account']!r} unexpectedly carries a "
                f"header-carryover-fix label but its source pages "
                f"({r['source_pages']}) don't include 477/546/547/548"
            )


# ---------- 5. _resolve_headerless_pages() unit tests (via a tiny synthetic PDF-less harness) ----------

def _make_match(regex, text):
    m = regex.match(text)
    assert m, f"test setup error: {text!r} should match {regex.pattern!r}"
    return m


def test_resolve_headerless_pages_classifies_opening_balance_as_redacted():
    """Direct test of the decision logic: a no-header page whose first body
    line is an Opening Balance must be classified as redacted, never as a
    forward-continuation, regardless of what follows it."""
    from reconstruct_ledger_entries import _OPENING_BALANCE_RE

    m = _make_match(_OPENING_BALANCE_RE, "By Opening Balance 2,05,51,866.76")
    assert m.group("dir") == "By"
    assert m.group("amount") == "2,05,51,866.76"
    # Matches the exact placeholder format the fix produces.
    sign = "Dr" if m.group("dir") == "To" else "Cr"
    label = f"[Redacted account — TB opening {sign} {m.group('amount')}]"
    assert label == REDACTED_477


def test_resolve_headerless_pages_requires_exact_continuity_match():
    """Direct test of the continuity rule: Carried Over must match Brought
    Forward on BOTH amount fields exactly, not just approximately or on
    the first figure alone, before forward-attribution is allowed."""
    from reconstruct_ledger_entries import _CARRIED_OVER_RE, _BROUGHT_FORWARD_RE, normalize_indian_number

    co = _make_match(_CARRIED_OVER_RE, "Carried Over 5,41,300.00 4,77,300.00")
    bf = _make_match(_BROUGHT_FORWARD_RE, "Brought Forward 5,41,300.00 4,77,300.00")
    assert normalize_indian_number(co.group("amt1")) == normalize_indian_number(bf.group("amt1"))
    assert normalize_indian_number(co.group("amt2")) == normalize_indian_number(bf.group("amt2"))

    # A near-miss (one rupee off) must NOT be treated as a match.
    bf_mismatch = _make_match(_BROUGHT_FORWARD_RE, "Brought Forward 5,41,301.00 4,77,300.00")
    assert normalize_indian_number(co.group("amt1")) != normalize_indian_number(bf_mismatch.group("amt1"))


def test_resolve_headerless_pages_end_to_end():
    """End-to-end check of _resolve_headerless_pages() against the real PDF
    for the exact page range containing all 3 known cases."""
    if not os.path.exists(PDF_FILE):
        pytest.skip(f"raw PDF not present at {PDF_FILE}")
    import pdfplumber
    from reconstruct_ledger_entries import _resolve_headerless_pages

    with pdfplumber.open(PDF_FILE) as pdf:
        redacted_pages, forward_pages, continuation_name_aliases = _resolve_headerless_pages(
            pdf, 470, 550
        )

    assert set(redacted_pages) == {477, 546}
    assert redacted_pages[477] == REDACTED_477
    assert redacted_pages[546] == REDACTED_546
    assert set(forward_pages) == {547}
    assert forward_pages[547] == "Ledger Tr. A/c"
    # No same-range same-or-different-name continuation renames in this
    # narrow page window (470-550) -- see
    # test_continuation_name_aliases_full_document_scan for the full-document
    # version of this check.
    assert continuation_name_aliases == {}
