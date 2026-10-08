"""
reconstruct_ledger_entries.py (v3 -- state-machine two-pass design)

STANDALONE, not yet wired into pdf_to_csv_converter.py.

CHANGES FROM v2, each grounded in empirical evidence from ledgers_redacted.pdf:

1. STALE OCCURRENCE CASCADE GUARD:
   Reset active voucher occurrence on page boundaries, state events, structural
   noise, and whenever a line begins with To/By or contains amounts but fails
   to parse as a voucher header. Detail legs and orphan amounts appearing
   without an active occurrence are safely captured in unresolved, preventing
   corruption of prior vouchers.

2. EXPANDED VOUCHER HEADER RECOGNITION:
   - Supports compound voucher types: Sales GST, Sales Cash, Purchase GST,
     Debit Note, Credit Note, Contra, Journal, Payment, Receipt, Sales, Purchase.
   - Supports alphanumeric / slash / hyphen voucher numbers (e.g. MPSS/24-25/54).
   - Supports omitted-date continuation voucher rows, carrying forward the
     active date on that page.
   - Supports single-amount headers (when no running balance is present, e.g. Page 54,
     Journal 667 and Page 8 Receipts).

3. EXPANDED STATE EVENTS:
   - Opening Balance: supports both "To Opening Balance" and "By Opening Balance".
   - Closing Balance: supports "To Closing Balance" and "By Closing Balance".
   - Brought Forward / Carried Over: supports both 1-amount and 2-amount forms
     (debit + credit totals).

4. MULTI-LINE PARTICULARS & NARRATION ATTACHMENT:
   - Wrapped party names (e.g. Hindustan -> Petroleum -> Corporation -> Limited,
     DHAVI MACHINE -> PVT.LTD.) are contextually stitched into the preceding
     header leg particulars.
   - Audit narration (Being..., CH PAID..., BILL OF..., etc.) is attached to
     the active voucher occurrence and preserved in canonical entries.

5. ACCOUNT HEADER & STRUCTURAL METADATA RECOGNITION:
   - Identifies table column header "Date Particulars..." as the body boundary;
     preceding lines are classified as account/page header metadata rather than
     unclassified errors.
   - Correctly detects "Cash Book" as "Cash", enabling seamless cross-page
     reconciliation (e.g. Journal 667 between Card Swipe and Cash accounts).
   - Section period markers (e.g. "May 2024", "December") are recognized as
     structural metadata.

6. DATE-AWARE CANONICAL VOUCHER GROUPING:
   - Distinguishes vouchers with identical numbers from different months
     (e.g. Purchase 524 across June, Sep, Oct, Nov, Jan) without conflating them,
     eliminating false cross-occurrence amount conflicts.
"""

import sys
import os
import re
import csv
from collections import defaultdict
import pdfplumber

sys.path.append(os.path.dirname(__file__))
from pdf_to_csv_converter import normalize_indian_number

# --- Patterns grounded in real extracted ledger text ---

_STANDARD_VCH_TYPES = (
    r"Journal|Payment|Receipt|Contra|"
    r"Sales(?:\s+(?:GST|Cash))?|Purchase(?:\s+GST)?|"
    r"Debit\s+Note|Credit\s+Note"
)

# Voucher-number token. Normally a single run of alnum/-/_ joined by "/"
# with no internal whitespace (e.g. "MPSS/24-25/32", "699"). The FY2025-26
# ledger introduces a prefix format with a literal space before each "/"
# ("INV /25-26/166") -- allow that one optional space before a "/"-prefixed
# segment without otherwise loosening the token (no space is still fine,
# so old-format vch_nos match exactly as before).
_VCH_NO_PATTERN = r"[A-Za-z0-9_\-]+(?:\s?/[A-Za-z0-9_\-]+)*"

_HEADER_RE = re.compile(
    r"^(?:(?P<date>\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+)?"
    r"(?P<dir>To|By)\s+"
    r"(?P<particulars>.+?)\s+"
    r"(?P<vch_type>" + _STANDARD_VCH_TYPES + r")\s+"
    r"(?P<vch_no>" + _VCH_NO_PATTERN + r")\s+"
    r"(?P<remainder>[\d,.\sDrC]+)$"
)

# Case A: Fused header regex where vch_no and amount are concatenated without whitespace
_FUSED_HEADER_RE = re.compile(
    r"^(?:(?P<date>\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+)?"
    r"(?P<dir>To|By)\s+"
    r"(?P<particulars>.+?)\s+"
    r"(?P<vch_type>" + _STANDARD_VCH_TYPES + r")\s+"
    r"(?P<vch_no>\d+?)(?=(?:\d{1,2},\d{2},\d{3}|\d{1,2},\d{3})\.\d{2})"
    r"(?P<remainder>[\d,.\sDrC]+)$"
)

# Case B: Header with wrapped particulars where particulars is on the next line
_WRAPPED_PARTICULARS_HEADER_RE = re.compile(
    r"^(?:(?P<date>\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+)?"
    r"(?P<dir>To|By)\s+"
    r"(?P<vch_type>" + _STANDARD_VCH_TYPES + r")\s+"
    r"(?P<vch_no>" + _VCH_NO_PATTERN + r")\s+"
    r"(?P<remainder>[\d,.\sDrC]+)$"
)

_LEG_RE = re.compile(r"^(?P<particulars>.+?)\s+(?P<amount>[\d,]+\.\d{2})\s+(?P<drcr>Dr|Cr)$")
_ORPHAN_AMOUNT_RE = re.compile(r"^(?P<amount>[\d,]+\.\d{2})\s+(?P<drcr>Dr|Cr)$")

_OPENING_BALANCE_RE = re.compile(
    r"^(?:(?P<date>\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+)?(?P<dir>To|By)\s+Opening Balance\s+(?P<amount>[\d,]+\.\d{2})$"
)
_CLOSING_BALANCE_RE = re.compile(
    r"^(?:(?P<date>\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+)?(?:(?P<dir>To|By)\s+)?Closing Balance\s+(?P<amount>[\d,]+\.\d{2})$"
)
_BROUGHT_FORWARD_RE = re.compile(
    r"^Brought Forward\s+(?P<amt1>[\d,]+\.\d{2})(?:\s+(?P<amt2>[\d,]+\.\d{2}))?$"
)
_CARRIED_OVER_RE = re.compile(
    r"^Carried Over\s+(?P<amt1>[\d,]+\.\d{2})(?:\s+(?P<amt2>[\d,]+\.\d{2}))?$"
)

_STRUCTURAL_NOISE_PATTERNS = [
    re.compile(r"^continued\s*\.\.\.", re.IGNORECASE),
    re.compile(r"^Date Particulars Vch Type Vch No\.? Debit Credit Balance$"),
    re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}\s+to\s+\d{1,2}-[A-Za-z]{3}-\d{2,4}$"),
    re.compile(r"^Page \d+$"),
    re.compile(r"^E-Mail\s*:?$"),
    re.compile(r"^[\d,]+\.\d{2}(\s+[\d,]+\.\d{2})?$"),
]

_PERIOD_MARKER_RE = re.compile(
    r"^(January|February|March|Apri[l;]?|May|June|July|August|September"
    r"|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec|OCT\.\d{2})(\s+\d{4})?$",
    re.IGNORECASE,
)

_NARRATION_PREFIXES = (
    "being ", "ch paid", "ch.paid", "bill of", "bill no", "purchase ",
    "advance for", "repair of", "vide bill", "balance write off",
    "transfer to", "fees for", "muncipality slip", "to drivers",
    "jagannath for", "for blankets", "bounced due", "insufficient balance",
    "from 26as", "portal is not", "labour expenses", "renovation of",
    "express card", "calibration fees", "weight and measurement",
    "sale reversed", "extra sale", "itc of", "hence written off",
    "aug commission", "commission of", "comm. on sale",
)

_PARTICULARS_CONTINUATION_TOKENS = {
    "petroleum", "corporation", "limited", "pvt.ltd.", "ltd.", "co.",
    "receivable", "expenses", "consolidated", "payable", "school",
    "international", "corporation limited", "account", "fees", "company",
    "college of nursing", "maan singh", "partner", "nagbani jammu",
    # Added investigating the FY2025-26 ledger's unresolved lines: all four
    # confirmed, every occurrence, to be a Shape B voucher-header
    # counterparty name wrapping onto the next line (never a detail-leg
    # particulars line) -- e.g. "By Veh. Running & Journal 792 ... Dr" /
    # "Maintenance Exp" is one printed name, "Veh. Running & Maintenance
    # Exp", split by the page's line wrap. Merging only ever extends the
    # private "_counterparty" field (narration text), never a named leg's
    # particulars key, so this cannot affect any voucher's Dr/Cr balance --
    # see the is_wrapped_part handling below. "veh.account" additionally
    # matches the long form already whitelisted in _PER_VOUCHER_NAME_ALIASES
    # ("PRINCIPAL MHAC VEH. ACCOUNT" / "...VEH.ACCOUNT").
    "maintenance exp", "control", "equipment", "veh.account",
}

# Anonymization-artifact fragments (FY2025-26 ledger only): short, digit-free
# remnants of a counterparty name whose first line was replaced by the
# anonymization process with a "P0xx"-style code (e.g. "P027"), but whose
# OWN physical PDF line -- the tail of a name that originally wrapped across
# two lines -- was not also scrubbed. Confirmed by direct inspection: every
# occurrence sits on its own line with no amount, immediately between two
# otherwise-complete, already-resolved detail legs (e.g. "P027 7,800.00 Dr"
# / "AL" / "Sales GST 6,610.14 Cr"). Deliberately NOT added to
# _PARTICULARS_CONTINUATION_TOKENS: that mechanism appends onto whatever leg
# came immediately before, and here that leg's particulars is the complete,
# correct account code (e.g. "P027") -- appending the fragment would silently
# rewrite it into a nonexistent compound key ("P027 AL"), breaking its
# cross-page corroboration. Since none of these carry an amount, discarding
# them loses no financial information; they are reported under a distinct
# reason rather than silently merged or left indistinguishable from a
# genuine unclassified line.
_ANONYMIZATION_ARTIFACT_FRAGMENTS = {
    "AL", "ani", "RSING", "AR", "OL", "ICS", "ON", "O", "OF", "OUNT", "MENT",
    "Enterprises(", "Committe)",
}


def _is_structural_noise(line):
    return any(p.match(line) for p in _STRUCTURAL_NOISE_PATTERNS)


def _classify_header_amounts(remainder, direction):
    numbers = re.findall(r"[\d,]+\.\d{2}", remainder)
    leg_drcr = "Dr" if direction == "To" else "Cr"
    if len(numbers) >= 1:
        return normalize_indian_number(numbers[0]), leg_drcr
    return None, leg_drcr


# Explicit, narrow canonicalization for accounts whose name fragments across
# pages: the SAME logical account prints a different name string on its first
# page vs. its continuation pages in this document (confirmed via exact
# trial-balance reconciliation -- see BASELINE_CHECKPOINT_V3.7.md). This is
# NOT a generic fuzzy-matching or whitespace-normalization rule: it is a
# bounded, explicit map of exactly the confirmed variant pairs where
# cross-page corroboration was failing and causing real double-counting.
# TDS Receivable HPCL and Rent Expenses HPCL are deliberately excluded --
# they have zero vouchers touching both their name variants, already
# reconcile exactly via simple aliasing in reconcile_trial_balance.py, and
# must not be touched.
#
# CGST / SGST added 2026-10-02 (V3.8), investigated while root-causing a
# separate 77-voucher entry-balance issue. Verified via the identical proof
# structure as the V3.7 pairs: both "CGST"/"SGST" (first page: 172, 787)
# and "CGST Account"/"SGST Account" (continuation pages: 173-251, 788-866)
# are genuine, continuous header-leg ledger runs for ONE account each; the
# variant ("...Account") form has ZERO detail-leg occurrences anywhere in
# the 779+779 cross-variant vouchers; zero amount conflicts when merged.
# This bug was previously invisible to reconcile_trial_balance.py, which
# had no CGST/SGST alias entry at all, so it only ever summed the
# "CGST"/"SGST" (detail-leg-reprint) rows and silently never checked the
# "...Account" (header-leg) rows against the trial balance.
#
# Several OTHER name-pairs were found during the same 77-voucher
# investigation (customer/vendor names that also appear under two
# spellings). Two of them were re-investigated in V3.9 using a sharper
# test than the original V3.8 pass -- Carried-Over/Brought-Forward
# continuity between their two header pages (same technique as the page
# 547/548 check in V3.6) -- and turned out to meet this same bar after all:
#
# - JANDIYAL ELECTRONICS (page 383) / JANDIYAL ELECTRONICS PARTNER (page
#   382): V3.8 read this as two different real accounts (both forms have
#   their own header page). V3.9 found page 382 ends "Carried Over
#   7,34,963.00 6,46,342.00 continued ..." and page 383 begins "Brought
#   Forward 7,34,963.00 6,46,342.00" -- an exact match proving ONE
#   continuous ledger, not two accounts. Page 383's header text
#   ("JANDIYAL" / "ELECTRONICS Account" / "PARTNER") is wrapped across 3
#   lines, and _detect_page_header_and_account()'s Shape-2 parser only
#   reattaches a 3rd header line when it is "CO."/"A/C"/"LTD." -- "PARTNER"
#   isn't in that list, so it was silently dropped. "JANDIYAL ELECTRONICS"
#   has ZERO detail-leg occurrences anywhere in the document (confirmed by
#   full-population scan) -- same bar as every other entry here.
# - KC EDU.SOCIETY( GENSET AC (pages 470-471) / KC EDU.SOCIETY(GENSET AC
#   (page 469): page 469 ends "Carried Over 2,92,702.00 continued ..." and
#   page 470 begins "Brought Forward 2,92,702.00" -- same proof. Page 469's
#   header prints as one unwrapped line (no space before "GENSET"); pages
#   470-471 wrap it across two lines, which the parser rejoins WITH a
#   space. "KC EDU.SOCIETY( GENSET AC" (with the space) has ZERO detail-leg
#   occurrences anywhere.
#
# The remaining 6 pairs (ARO OFFICE B-1204, CAMBRIDGE, PRINCIPAL MHAC
# VEH.ACCOUNT, Principal MHAC School, SGF INFRA PVT LTD, Veh. Running &)
# are structurally different -- their short form has NO header page
# anywhere (zero occurrences), so there is no page pair to canonicalize at
# all: these are handled separately, per-voucher, by
# _PER_VOUCHER_NAME_ALIASES below. See BASELINE_CHECKPOINT_V3.9.md for the
# full classification and evidence.
#
# 3 more entries added investigating the remaining 29 unbalanced vouchers
# (post-V3.9), same proof structure, same Carried-Over/Brought-Forward test:
# (Employee names below are replaced by placeholders in this comment; the
# two dict entries keep the real ledger spelling, which must match exactly.)
# - Employee A's salary A/c, "<A> -Salary A/c" (page 15) / "<A>-Salary A/c"
#   (page 14): page 14 ends "Carried Over 2,70,000.00 continued ..."; page 15 begins
#   "Brought Forward 2,70,000.00" -- exact match.
# - Employee B's salary A/c, "<B> -Salary A/c" (page 305) / "<B>-Salary A/c"
#   (page 304): page 304 ends "Carried Over 2,25,000.00 continued ..."; page 305
#   begins "Brought Forward 2,25,000.00" -- exact match.
# - EDLI Account (page 319) / EDLI (page 318): page 318 ends "Carried Over
#   2,820.00 continued ..."; page 319 begins "Brought Forward 2,820.00" --
#   exact match.
# All 3 variant forms have ZERO detail-leg occurrences anywhere; zero
# conflicts across the full cross-variant population.
#
# DISCOUNT Account (page 314, the only occurrence anywhere) / DISCOUNT: not
# a continuation-page pair like the others -- page 314 is DISCOUNT's own
# single ledger page, which prints ONE voucher twice (the header-leg
# summary line, and a Shape-A detail-leg breakdown line also named
# "DISCOUNT"). Canonicalizing lets the existing Pass-1 redundant-header-leg
# dedup (collect_occurrences(), already in this file) recognize and drop
# the duplicate, same as it already does elsewhere -- no new logic.
_ACCOUNT_NAME_CANONICALIZATION = {
    "Sales Account": "Sales",
    "Purchase Account": "Purchase",
    "Round Off /Discount": "Round Off/Discount",
    "CGST Account": "CGST",
    "SGST Account": "SGST",
    "JANDIYAL ELECTRONICS": "JANDIYAL ELECTRONICS PARTNER",
    "KC EDU.SOCIETY( GENSET AC": "KC EDU.SOCIETY(GENSET AC",
    "Ashok Sharma -Salary A/c": "Ashok Sharma-Salary A/c",
    "Deepak Sharma -Salary A/c": "Deepak Sharma-Salary A/c",
    "EDLI Account": "EDLI",
    "DISCOUNT Account": "DISCOUNT",
}

# Per-voucher cross-spelling consolidation (V3.9). These 6 pairs do NOT
# qualify for _ACCOUNT_NAME_CANONICALIZATION above: the SHORT form never
# has its own header page anywhere in the 926-page document -- it exists
# purely as free-form detail-leg counter-party text (parsed by _LEG_RE,
# independent of current_account) reprinted on OTHER ledgers' pages. The
# LONG form is the account's own genuine header-leg name on its own
# dedicated page(s), with ZERO detail-leg occurrences of its own. Folding
# the short spelling into the long one at the header-parsing insertion
# point would do nothing (the short form is never a page header to
# canonicalize) and can't reach _LEG_RE's detail-leg text at all.
#
# Instead, merge_occurrences() merges these two spellings ONLY within a
# single voucher (entry_id), and ONLY when ALL of the following hold for
# that specific voucher:
#   - both spellings appear under the same entry_id and dr_cr
#   - their source pages are completely disjoint (never printed on the
#     same page -- this is what rules out two different real line items
#     that coincidentally share an amount, versus a genuine cross-ledger
#     reprint of the same leg)
#   - their amounts match exactly
# This is entry_id + amount + dr_cr matching as deterministic per-voucher
# proof, not string resemblance or fuzzy matching. Verified across all 71
# candidate vouchers in the full population: zero ambiguous/colliding
# cases (no other leg in any of these vouchers shares the matched
# amount+dr_cr). See BASELINE_CHECKPOINT_V3.9.md.
#
# A LIST of (short, long) pairs, not a dict -- a short form is free-form
# detail-leg text written by whoever typed the original voucher, so the
# SAME short string can legitimately be shorthand for different real
# accounts in different documents (found in the FY2025-26 ledger: bare
# "CAMBRIDGE" is shorthand for "CAMBRIDGE INTERNATIONAL (GENSET A/C)" in
# detail-leg text, per the FY2024-25 entry below, in exactly the same way
# it is ALSO the FY2025-26 ledger's own canonical continuation-page name
# for a completely different account (P027's, see
# _resolve_headerless_pages()'s continuation_name_aliases) -- a dict keyed
# by short-form string cannot hold two different targets for one key, so
# this has to be a list; merge_occurrences() iterates every pair exactly as
# before (same per-voucher, disjoint-pages, exact-amount proof), so a short
# form with two candidate targets only ever merges with whichever one
# actually appears in that specific voucher.
_PER_VOUCHER_NAME_ALIASES = [
    ("ARO OFFICE B-1204", "ARO OFFICE B-1204 ROYAL PALMS C/O NAGBANI"),
    ("CAMBRIDGE", "CAMBRIDGE INTERNATIONAL (GENSET A/C)"),
    ("PRINCIPAL MHAC VEH. ACCOUNT", "PRINCIPAL MHAC VEH.ACCOUNT"),
    ("Principal MHAC School", "Principal MHAC School Nagbani"),
    ("SGF INFRA PVT LTD", "SGF INFRA PVT LTD GURHA BAKSHI NAGAR"),
    ("Veh. Running &", "Veh. Running & Maintenance Exp"),
    # Added investigating the remaining 29 unbalanced vouchers (post-V3.9):
    # "Tr. A/c" has ZERO header pages anywhere (pure detail-leg text, 24
    # occurrences across pages 56-58, 902-906); "Ledger Tr. A/c" is the
    # account's own genuine header-leg name (pages 547-548). This is the
    # "Page 548 Ledger Tr. A/c" item deferred since V3.6. All 12 candidate
    # vouchers checked: zero page overlap, exact amount match, zero
    # ambiguity. See BASELINE_CHECKPOINT_V3.9.md (or its successor).
    ("Tr. A/c", "Ledger Tr. A/c"),

    # Added investigating the FY2025-26 ledger's remaining unbalanced
    # vouchers after the Shape-2 header-detection fix and
    # continuation_name_aliases (both found investigating the same
    # original "non-GST Sales account" question). Same proof structure as
    # every entry above: re-derived the full candidate set directly from
    # the FY2025-26 ledger's post-continuation-fix reconstruction output
    # (same-entry_id + same-dr_cr + exact-amount + fully-disjoint-pages),
    # which reduced to exactly these 3 distinct short/long pairs with ZERO
    # ambiguous cases (no short form ever had 2+ candidate long-form
    # matches within the same voucher) -- see the investigation that added
    # this comment for the full before/after counts.
    #
    # "CAMBRIDGE" / "P026": bare "CAMBRIDGE" is ALSO free-form detail-leg
    # shorthand in the FY2025-26 ledger, for a DIFFERENT real account than
    # the one above -- P026's own canonical continuation-page name is
    # "CAMBRIDGE INTERNATIONAL" (not plain "CAMBRIDGE"; proven via
    # continuation_name_aliases' Carried-Over/Brought-Forward match, pages
    # 29->30), so by the time merge_occurrences() runs, P026's own
    # occurrences already read "P026" (continuation_name_aliases'
    # canonical/earlier form), never "CAMBRIDGE INTERNATIONAL" literally --
    # the alias target here is "P026", not the FY2024-25 entry's long form.
    ("CAMBRIDGE", "P026"),
    # "PRINCIPAL MHAC VEH. ACCOUNT" / "P118": the FY2025-26 ledger's own
    # version of the FY2024-25 "vehicle sub-account" pattern (see
    # "Veh. Running &" above) -- this exact phrase has no dedicated header
    # page in the FY2025-26 ledger either, and is shorthand for P118's
    # account (continuation-renamed from "PRINCIPAL MHAC", pages 528->529;
    # note this is a DIFFERENT, all-caps account from P117's "Principal
    # MHAC", pages 526->527 -- confirmed two separate accounts, not a
    # case-typo).
    ("PRINCIPAL MHAC VEH. ACCOUNT", "P118"),
    # "TDS" / "TDS RECEIVABLE SUPPLIER": page 873's own header is "TDS
    # Ledger : ... Page 873" / "Account" / "SUPPLIER" -- a 3rd trailing
    # header line ("SUPPLIER") that neither Shape 2 branch consumes (the
    # same column-reordering extraction artifact as "Rent"/"OIL SUPPLIER
    # RENT", pages 573-574, which continuation_name_aliases already
    # resolves via the Carried-Over/Brought-Forward match alone, since that
    # pair's names differ only by the dropped trailing word). Bare "TDS" is
    # ALSO common free-form detail-leg shorthand elsewhere in the document,
    # which continuation_name_aliases' page-adjacency proof cannot reach --
    # this per-voucher entry catches those remaining cases.
    ("TDS", "TDS RECEIVABLE SUPPLIER"),
]

# Redacted-account orphan absorption (Pattern 4, post-V3.9). The 2 redacted
# accounts (see _resolve_headerless_pages()) get a synthetic header leg on
# their OWN page(s) (477, 546) using the placeholder label as particulars --
# but on every OTHER account's page where one of these vouchers reprints the
# SAME leg with the redacted account as counterparty, the counterparty name
# was blanked out in the source document itself, so it parses as an orphan
# leg (particulars=None) instead of the placeholder text (the placeholder is
# a label WE invented for the account's own header; it is never literally
# printed as body-line particulars anywhere in the source).
#
# Before any code change, this was proven safe by an exhaustive, read-only,
# whole-document bounded simulation (not just the 6 vouchers found by an
# earlier informal check): for every one of the 22 orphan legs in the full
# 926-page population, checking (entry_id, exact amount, exact dr_cr) against
# every named leg in that voucher whose particulars starts with
# "[Redacted account" -- ALL 22 resolved to EXACTLY ONE eligible candidate,
# ZERO were ambiguous (2+ candidates), ZERO had no match, and the affected
# vouchers were EXACTLY the 6 already identified
# (Journal_604/671/672/684/687/690) -- no unexpected candidates. See the
# checkpoint doc for the full simulation report.
_REDACTED_PLACEHOLDER_PREFIX = "[Redacted account"


def _is_garbled_ledger_account_marker(s):
    """True if `s` could be a text-extraction-mangled fragment of the
    literal "Ledger Account" marker line (e.g. "Ledg nt", "Le t", "Ledg")
    -- found on exactly 3 pages of the FY2025-26 ledger (464, 572, 860),
    each a brand-new account's first page whose real name IS cleanly
    printed on the line above this one, but whose own "Ledger Account"
    line is corrupted by the same class of pdfplumber rendering quirk
    already seen elsewhere in this project (a few characters silently
    dropped), so it isn't recognized by Shape 1's exact `== "Ledger
    Account"` check and survives into Shape 3's name_lines instead of
    being filtered out as a marker. Checked as a same-order subsequence
    match against "LedgerAccount" (spaces stripped) rather than an exact
    or fuzzy-distance match, since each real instance drops a different,
    arbitrary run of characters. Scoped deliberately narrow -- requires at
    least 2 characters, so it only ever fires on the 3 known candidates in
    this document (verified: no other page's Shape-3 second line matches),
    not on an arbitrary short real second word of an account name.
    """
    candidate = s.replace(" ", "")
    if len(candidate) < 2:
        return False
    target = "LedgerAccount"
    pos = 0
    for ch in candidate:
        pos = target.find(ch, pos)
        if pos == -1:
            return False
        pos += 1
    return True


def _detect_page_header_and_account(lines):
    col_idx = -1
    for i, l in enumerate(lines):
        if "Date Particulars" in l:
            col_idx = i
            break
    if col_idx == -1:
        return None, 0

    header_lines = lines[:col_idx]
    account_name = None

    # Shape 1: line before "Ledger Account" or before date range if ending in " Book"
    for j, l in enumerate(header_lines):
        if l == "Ledger Account" and j > 0:
            account_name = header_lines[j - 1]
            break
        if l.endswith(" Book") and j + 1 < len(header_lines) and re.match(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}", header_lines[j + 1]):
            base = l[:-5].strip()
            account_name = "Cash" if base == "Cash" else base
            break

    # Shape 2: Split header across lines. The account+date-range line
    # ("<Name> : <date> to <date> Page N") was assumed to always be
    # header_lines[0] -- true for the FY2024-25 ledger, where it genuinely
    # is the first extracted line. The FY2025-26 ledger prepends a constant
    # "ANONYMISED FIRM" preamble line (an anonymization artifact; the real
    # firm-identifying lines it replaces aren't present in the old ledger
    # either) that pushes this line to header_lines[1] instead, which the
    # old index-0-only check never matches -- silently leaving account_name
    # unset for the page. Confirmed empirically before fixing: this made
    # 790 of 888 pages in the new ledger's PDF resolve as "headerless", of
    # which 753 fell through to collect_occurrences()'s safety-net fallback
    # (using the printed counter-party name as the leg's account instead of
    # the page's real one) or silently dropped the header leg entirely --
    # 12,852 of 13,981 voucher occurrences affected in total. Fixed by
    # scanning every header line for the pattern instead of assuming a
    # fixed position, which generalizes to both documents: verified this
    # still matches at index 0, with identical results, on every one of the
    # FY2024-25 ledger's pages that previously relied on this shape.
    if not account_name:
        for j, l in enumerate(header_lines):
            m1 = re.match(
                r"^(?P<part1>.+?)\s+(?:Ledger\s*:|:)\s+\d{1,2}-[A-Za-z]{3}-\d{2,4}\s+to\s+"
                r"\d{1,2}-[A-Za-z]{3}-\d{2,4}\s+Page\s+\d+$",
                l,
            )
            if not m1 or j + 1 >= len(header_lines):
                continue
            part1 = m1.group("part1").strip()
            part2_line = header_lines[j + 1]

            if part2_line.strip() in ("Book", "Account"):
                # The whole second line is a BARE marker word ("Book" or
                # "Account") with no qualifier word before it, so the real
                # account name is part1 alone and the marker carries no
                # content -- same shape, same fix, for both words. Exhaustive
                # scan of every Shape-2 page in BOTH documents (see the
                # investigation that found this) shows bare "Account" alone
                # affects 608 pages (268 in the FY2024-25 ledger, 340 in
                # FY2025-26) -- far more than bare "Book" (275) -- and was
                # previously MASKED for the FY2024-25 ledger only because 4
                # of its bare-"Account" accounts ("Sales", "Purchase",
                # "CGST", "SGST") happened to already be covered by
                # _ACCOUNT_NAME_CANONICALIZATION's "<Name> Account" ->
                # "<Name>" entries -- every OTHER bare-"Account" account in
                # that same ledger silently carried the same bogus " Account"
                # suffix, undetected until now because none of them caused a
                # voucher-balance mismatch in that document (no competing
                # short-form spelling of the same account elsewhere). The
                # FY2025-26 ledger's "B.R TRADING" (page 26, bare "Account")
                # is the case that surfaced this: its own header leg became
                # "B.R TRADING Account", a different string from "P023" --
                # the SAME real account's anonymized code, printed
                # consistently in every OTHER page's "(as per details)"
                # breakdown lines (pages 609, 683) -- so the two spellings
                # were never merged/corroborated, double-counting that leg's
                # amount and unbalancing the voucher (see
                # BASELINE_CHECKPOINT_V3.12.md's successor for the full
                # before/after trace). Originally only special-cased for the
                # literal "Cash"/bare-"Book" shape (this comment's prior
                # text); generalized here to bare "Account" too once the
                # same bug class was found there. Verified: the FY2024-25
                # ledger's bare-"Book" case ("Cash") and qualified case
                # ("Ltd Book", via the `else` branch below) still resolve
                # unchanged, and its full reconstruction output is still
                # byte-identical to the pre-this-investigation baseline
                # despite stripping " Account" from 268 more pages --
                # meaning every one of those accounts' bogus suffixed name
                # was either already masked by an explicit canonicalization
                # entry (now redundant but harmless) or never collided with
                # another spelling of the same account in that document.
                account_name = part1
            else:
                m2 = re.match(r"^(?P<part2>.+?)(?:\s+Account|\s+Book)?$", part2_line)
                part2 = m2.group("part2").strip() if m2 else part2_line
                account_name = f"{part1} {part2}".strip()

            if j + 2 < len(header_lines) and header_lines[j + 2] in ("CO.", "A/C", "LTD."):
                account_name = f"{account_name} {header_lines[j + 2]}"
            break

    # Shape 3: "<Name>" / "<date> to <date>" / "Page N", each its own line,
    # with no "Ledger Account"/"Book" marker anywhere -- a third real header
    # layout, seen only in the FY2025-26 ledger (confirmed: distinct from
    # Shapes 1/2, not a malformed instance of either), used for a page that
    # is the first and only page of its account (first-time coded
    # counterparty accounts like "P006", and a few named ones). The name is
    # whatever remains between the constant "ANONYMISED FIRM" preamble line
    # and the date-range line, after also discarding a lone single-capital-
    # letter line where present (e.g. "P086" / "L" / "1-Apr-25 to
    # 31-Mar-26") -- the same stray-letter pdfplumber rendering artifact
    # already tolerated elsewhere in this project (see
    # adapt_gstr_to_schema.py's _COL_SEP).
    #
    # Deliberately conservative: only accept when EXACTLY one line remains
    # after that filtering, mirroring this project's "0 or 2+ candidates,
    # don't guess" rule used for every other ambiguous merge. This matters
    # because the FY2024-25 ledger's two genuinely-redacted pages (477,
    # 546 -- confirmed by trial-balance cross-check to have no real name
    # printed in the source at all, see _resolve_headerless_pages()) have
    # this SAME 3-line shape with a *different*, 2-line firm-letterhead
    # preamble ("Jammu" / "E-Mail :", not "ANONYMISED FIRM") that an
    # unconditional single-line exclusion would not catch, leaving 2 lines
    # behind -- correctly rejected by the exactly-one-line check below
    # rather than fabricating "Jammu E-Mail :" as an account name. Verified
    # directly against both pages before accepting this guard.
    if not account_name:
        for j, l in enumerate(header_lines):
            if not re.match(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}\s+to\s+\d{1,2}-[A-Za-z]{3}-\d{2,4}$", l):
                continue
            if j + 1 >= len(header_lines) or not re.match(r"^Page\s+\d+$", header_lines[j + 1]):
                continue
            name_lines = [
                hl for hl in header_lines[:j]
                if hl != "ANONYMISED FIRM" and not re.match(r"^[A-Z]$", hl)
            ]
            if len(name_lines) == 1:
                account_name = name_lines[0].strip()
            elif len(name_lines) == 2 and _is_garbled_ledger_account_marker(name_lines[1]):
                # A second genuine Shape-3 residual (3 pages total, verified
                # exhaustively -- see _is_garbled_ledger_account_marker's
                # docstring): the "Ledger Account" marker line itself gets
                # text-extraction-mangled ("Ledg nt", "Le t", "Ledg"), so it
                # isn't excluded as preamble/artifact above and survives as
                # a second name_line -- dropped here once confirmed to be a
                # fragment of that exact marker, not a second word of a
                # real account name.
                account_name = name_lines[0].strip()
            break

    if account_name in _ACCOUNT_NAME_CANONICALIZATION:
        account_name = _ACCOUNT_NAME_CANONICALIZATION[account_name]

    return account_name, col_idx + 1


def _resolve_headerless_pages(pdf, start_page, end_page):
    """Pre-scan pass (read-only, no occurrence building): for every page in
    [start_page, end_page] whose own account header is NOT recognized by
    _detect_page_header_and_account(), determine how current_account should
    be resolved -- WITHOUT blindly inheriting from the previous page, which
    is what caused the header-carryover bug (pages 477/546/547 were wrongly
    absorbed into Laptop/Pollution Fees via whatever account preceded them
    on the page before, even though none of the three is a continuation of
    that account at all). Fixed 2026-10-01.

    Returns (redacted_pages, forward_pages, continuation_name_aliases):
      redacted_pages: {page_num: placeholder_label} -- the page's own first
        body line is an Opening Balance, meaning this is a BRAND-NEW account
        starting fresh, not a continuation of anything before it. Its name
        was never printed in the source at all (confirmed, for the 2 known
        cases -- pages 477 and 546 -- to exactly match the trial balance's
        2 redacted/unnamed rows down to the rupee, on both the opening
        balance and the resulting Dr/Cr totals: almost certainly the two
        partners' capital/current accounts, redacted in both documents).
        Rather than invent a real name or silently drop these legs, they get
        a clearly-marked placeholder uniquely tied to the exact opening
        balance printed on the page, e.g.
        "[Redacted account — TB opening Cr 2,05,51,866.76]".
      forward_pages: {page_num: account_name} -- the page's own last body
        line is a Carried Over balance that exactly matches (amount AND
        Dr/Cr) the Brought Forward balance on the next page that DOES have
        a recognized header -- i.e. a genuine continuation whose own header
        merely failed to parse (page 547's header is split "Tr. A/c" /
        "unt" across two lines, matching neither recognized header shape).
        Only set when that continuity is independently verified; never
        guessed from proximity alone.
      continuation_name_aliases: {later_name: earlier_name} -- generalizes
        the SAME Carried-Over/Brought-Forward proof to the case where BOTH
        pages' headers parse FINE but resolve to two DIFFERENT account
        names -- i.e. the account's own ledger genuinely changes how it
        prints its name partway through, most often because its first page
        uses the FY2025-26 ledger's anonymized "P0xx" code (its Shape 3
        first-page layout) while a later continuation page reverts to
        printing the real, un-anonymized name (Shape 1/2) -- an
        anonymization inconsistency in the source document, not a bug in
        this parser. Found investigating the remaining unbalanced vouchers
        after the Shape-2 header-detection fix: e.g. page 25 ("P023") ends
        "Carried Over 17,96,133.00"; page 26 ("B.R TRADING") begins
        "Brought Forward 17,96,133.00" -- the same account, so every OTHER
        page's detail-leg reference to "P023" and page 26's own header leg
        for "B.R TRADING" must resolve to the SAME name or they silently
        double-count that leg (two different spellings of one real account
        both surviving as separate named legs) and unbalance the voucher.
        Exhaustively scanned across EVERY adjacent same-or-different-name
        page pair in the full page range (not just the ones found via
        unbalanced vouchers): 34 genuine pairs in the FY2025-26 ledger, 2 in
        the FY2024-25 ledger, zero amount mismatches among any pair that
        had both a Carried-Over and a Brought-Forward line to compare (i.e.
        zero false positives from this proof). The LATER name is always
        mapped to the EARLIER one (never the reverse) because the earlier
        name is what the rest of the document's OTHER pages' detail-leg
        breakdowns actually use to refer to this account (confirmed for
        every P0xx-coded case); this also matches the direction already
        used by this project's existing hand-verified entries in
        _ACCOUNT_NAME_CANONICALIZATION (e.g. an employee salary account
        written "<Name> -Salary A/c" on the LATER page maps to
        "<Name>-Salary A/c", the EARLIER page's spelling). Checked for ambiguity the same way as every other
        merge rule in this file: if the same later-name were ever proven to
        continue from two DIFFERENT earlier names, neither mapping would be
        applied for it -- not observed in either document (every later-name
        here resolves to exactly one earlier-name), but guarded against
        regardless since a future document could differ.

    A page that is neither (no Opening Balance, no verified continuity) is
    deliberately left out of both dicts; collect_occurrences() then leaves
    current_account unresolved (None) for it rather than inheriting one --
    see the 2026-10-01 header-carryover investigation. Empirically, every
    one of the 3 no-header pages in ledgers_redacted.pdf resolves to one of
    the two cases above; this is a general mechanism, not a per-page patch,
    so a future document could still have an unresolved page, which is the
    conservative/safe outcome rather than a guess.
    """
    page_lines = {}
    page_detected = {}
    for page_num in range(start_page, min(end_page, len(pdf.pages)) + 1):
        page = pdf.pages[page_num - 1]
        text = page.extract_text(x_tolerance=1.5)
        raw_lines = [l.strip() for l in text.split("\n") if l.strip()] if text else []
        page_lines[page_num] = raw_lines
        page_detected[page_num] = _detect_page_header_and_account(raw_lines)

    redacted_pages = {}
    forward_pages = {}

    for page_num, (detected_acc, body_start_idx) in page_detected.items():
        if detected_acc:
            continue  # header recognized normally -- nothing to resolve

        body = page_lines[page_num][body_start_idx:]
        if not body:
            continue

        m_ob = _OPENING_BALANCE_RE.match(body[0])
        if m_ob:
            sign = "Dr" if m_ob.group("dir") == "To" else "Cr"
            redacted_pages[page_num] = (
                f"[Redacted account — TB opening {sign} {m_ob.group('amount')}]"
            )
            continue

        # This page's own Carried Over line, if any (at most one expected,
        # near the end of the page, immediately before "continued ...").
        carried = None
        for l in body:
            m_co = _CARRIED_OVER_RE.match(l)
            if m_co:
                carried = m_co
        if not carried:
            continue  # neither signal present -- do not guess

        c_amt1 = normalize_indian_number(carried.group("amt1"))
        c_amt2 = normalize_indian_number(carried.group("amt2")) if carried.group("amt2") else None

        # Look ahead to the next page that has its OWN recognized header
        # (skipping over any further no-header pages in between).
        next_page = page_num + 1
        while next_page in page_detected and not page_detected[next_page][0]:
            next_page += 1
        if next_page not in page_detected or not page_detected[next_page][0]:
            continue  # ran off the end of the range, or none found

        next_acc, next_body_start = page_detected[next_page]
        next_body = page_lines[next_page][next_body_start:]
        if not next_body:
            continue
        m_bf = _BROUGHT_FORWARD_RE.match(next_body[0])
        if not m_bf:
            continue  # next page doesn't open with Brought Forward at all

        b_amt1 = normalize_indian_number(m_bf.group("amt1"))
        b_amt2 = normalize_indian_number(m_bf.group("amt2")) if m_bf.group("amt2") else None

        if c_amt1 == b_amt1 and c_amt2 == b_amt2:
            forward_pages[page_num] = next_acc
        # else: amounts don't match -- do not guess

    # continuation_name_aliases: same Carried-Over/Brought-Forward proof,
    # generalized to adjacent page pairs that BOTH have a recognized header
    # (so neither is in redacted_pages/forward_pages above) but resolve to
    # two DIFFERENT account names. See this function's docstring for the
    # full rationale and the "later name maps to earlier name" direction.
    continuation_name_aliases = {}
    _alias_candidates = defaultdict(set)
    for page_num, (detected_acc, body_start_idx) in page_detected.items():
        if not detected_acc:
            continue
        next_page = page_num + 1
        if next_page not in page_detected:
            continue
        next_acc, next_body_start = page_detected[next_page]
        if not next_acc or next_acc == detected_acc:
            continue  # same name -- ordinary continuation, nothing to alias

        body = page_lines[page_num][body_start_idx:]
        carried = None
        for l in body:
            m_co = _CARRIED_OVER_RE.match(l)
            if m_co:
                carried = m_co
        if not carried:
            continue

        next_body = page_lines[next_page][next_body_start:]
        if not next_body:
            continue
        m_bf = _BROUGHT_FORWARD_RE.match(next_body[0])
        if not m_bf:
            continue

        c_amt1 = normalize_indian_number(carried.group("amt1"))
        c_amt2 = normalize_indian_number(carried.group("amt2")) if carried.group("amt2") else None
        b_amt1 = normalize_indian_number(m_bf.group("amt1"))
        b_amt2 = normalize_indian_number(m_bf.group("amt2")) if m_bf.group("amt2") else None

        if c_amt1 == b_amt1 and c_amt2 == b_amt2:
            _alias_candidates[next_acc].add(detected_acc)
        # else: amounts don't match -- do not guess

    for later_name, earlier_names in _alias_candidates.items():
        if len(earlier_names) == 1:
            continuation_name_aliases[later_name] = next(iter(earlier_names))
        # else: the same later-name was proven to continue from 2+ DIFFERENT
        # earlier names -- ambiguous, do not guess (not observed in either
        # document as of this writing; see docstring).

    # Resolve any transitive chains (A -> B -> C) to their final root, so a
    # 3+ page rename chain collapses to one canonical name instead of
    # stopping halfway. Not observed in either document (every chain found
    # so far is a single hop), but resolved defensively rather than assumed.
    for name in list(continuation_name_aliases):
        seen = {name}
        target = continuation_name_aliases[name]
        while target in continuation_name_aliases and target not in seen:
            seen.add(target)
            target = continuation_name_aliases[target]
        continuation_name_aliases[name] = target

    return redacted_pages, forward_pages, continuation_name_aliases


def collect_occurrences(pdf_path, start_page, end_page, apply_continuation_name_aliases=True):
    """apply_continuation_name_aliases=False keeps _resolve_headerless_pages()'s
    new continuation_name_aliases dict computed (for visibility/testing) but
    never applies it to current_account -- used to keep the frozen FY2024-25
    ledger's committed output byte-for-byte untouched while this mechanism
    is still new and its 2 FY2024-25 candidates ("Rent Expenses HPCL" ->
    "Rent Expenses", "TDS Receivable HPCL" -> "TDS Receivable") haven't been
    explicitly reviewed/approved the way every other change to that frozen
    baseline has been in this project. main() passes False for that
    document specifically; every other caller (including the FY2025-26
    ledger, where this mechanism is required to fix the real bug under
    investigation) gets the default True.
    """
    occurrences = []
    state_events = []
    unresolved = []
    current_account = None

    with pdfplumber.open(pdf_path) as pdf:
        redacted_pages, forward_pages, continuation_name_aliases = _resolve_headerless_pages(
            pdf, start_page, end_page
        )
        if not apply_continuation_name_aliases:
            continuation_name_aliases = {}

        for page_num in range(start_page, min(end_page, len(pdf.pages)) + 1):
            page = pdf.pages[page_num - 1]
            text = page.extract_text(x_tolerance=1.5)
            if not text:
                continue
            raw_lines = [l.strip() for l in text.split("\n") if l.strip()]

            # Reset page-scoped state machine: occurrence never leaks across pages
            current_occurrence = None
            active_date = None

            detected_acc, body_start_idx = _detect_page_header_and_account(raw_lines)
            if detected_acc:
                # Fold a later continuation page's own spelling into whatever
                # name the SAME account used earlier, when proven by a
                # Carried-Over/Brought-Forward amount match -- see
                # _resolve_headerless_pages()'s continuation_name_aliases.
                current_account = continuation_name_aliases.get(detected_acc, detected_acc)
            elif page_num in redacted_pages:
                # Fixed 2026-10-01 (header-carryover bug): a fresh,
                # never-before-seen account whose name was never printed in
                # the source at all -- do NOT inherit the previous page's
                # account. See _resolve_headerless_pages().
                current_account = redacted_pages[page_num]
            elif page_num in forward_pages:
                # Fixed 2026-10-01 (header-carryover bug): a genuine
                # continuation whose own header failed to parse -- verified
                # via Carried Over / Brought Forward continuity with the
                # next properly-headed page, not inherited from the
                # previous one. See _resolve_headerless_pages().
                current_account = forward_pages[page_num]
            else:
                # No fresh header, no Opening Balance, no verified
                # continuity: do not guess. Previously this branch silently
                # kept whatever current_account the PREVIOUS page left
                # behind (the header-carryover bug) -- empirically, every
                # no-header page in ledgers_redacted.pdf is resolved by one
                # of the two branches above, so this is a safety net for
                # future documents, not an observed case here.
                current_account = None

            i = body_start_idx
            while i < len(raw_lines):
                line = raw_lines[i]

                if _is_structural_noise(line) or _PERIOD_MARKER_RE.match(line):
                    current_occurrence = None
                    i += 1
                    continue

                # State Events
                m_open = _OPENING_BALANCE_RE.match(line)
                if m_open:
                    ev_date = m_open.group("date") or active_date
                    if m_open.group("date"):
                        active_date = m_open.group("date")
                    state_events.append({
                        "event_type": "opening_balance", "account": current_account,
                        "date": ev_date, "amount": normalize_indian_number(m_open.group("amount")),
                        "source_page": page_num,
                    })
                    current_occurrence = None
                    i += 1
                    continue

                m_close = _CLOSING_BALANCE_RE.match(line)
                if m_close:
                    ev_date = m_close.group("date") or active_date
                    state_events.append({
                        "event_type": "closing_balance", "account": current_account,
                        "date": ev_date, "amount": normalize_indian_number(m_close.group("amount")),
                        "source_page": page_num,
                    })
                    current_occurrence = None
                    i += 1
                    continue

                m_bf = _BROUGHT_FORWARD_RE.match(line)
                if m_bf:
                    amt1 = normalize_indian_number(m_bf.group("amt1"))
                    amt2 = normalize_indian_number(m_bf.group("amt2")) if m_bf.group("amt2") else None
                    state_events.append({
                        "event_type": "brought_forward", "account": current_account,
                        "date": active_date, "amount": amt1 if amt2 is None else f"{amt1} {amt2}",
                        "source_page": page_num,
                    })
                    current_occurrence = None
                    i += 1
                    continue

                m_co = _CARRIED_OVER_RE.match(line)
                if m_co:
                    amt1 = normalize_indian_number(m_co.group("amt1"))
                    amt2 = normalize_indian_number(m_co.group("amt2")) if m_co.group("amt2") else None
                    state_events.append({
                        "event_type": "carried_over", "account": current_account,
                        "date": active_date, "amount": amt1 if amt2 is None else f"{amt1} {amt2}",
                        "source_page": page_num,
                    })
                    current_occurrence = None
                    i += 1
                    continue

                # Voucher Header (Standard, Case A Fused, or Case B Wrapped Particulars)
                header_match = _HEADER_RE.match(line)
                h_date = None
                direction = None
                particulars = None
                vch_type = None
                vch_no = None
                remainder = None
                consumed_next_line = False

                if header_match:
                    h_date = header_match.group("date")
                    direction = header_match.group("dir")
                    particulars = header_match.group("particulars").strip()
                    vch_type = header_match.group("vch_type").strip()
                    vch_no = header_match.group("vch_no").strip()
                    remainder = header_match.group("remainder")
                else:
                    m_fused = _FUSED_HEADER_RE.match(line)
                    if m_fused:
                        h_date = m_fused.group("date")
                        direction = m_fused.group("dir")
                        particulars = m_fused.group("particulars").strip()
                        vch_type = m_fused.group("vch_type").strip()
                        vch_no = m_fused.group("vch_no").strip()
                        remainder = m_fused.group("remainder")
                    else:
                        m_wrap = _WRAPPED_PARTICULARS_HEADER_RE.match(line)
                        if m_wrap:
                            next_l = raw_lines[i + 1] if i + 1 < len(raw_lines) else ""
                            is_continuation = bool(
                                next_l
                                and not next_l.startswith(("To ", "By ", "To\t", "By\t"))
                                and not re.search(r"[\d,]+\.\d{2}", next_l)
                                and not _is_structural_noise(next_l)
                                and not _PERIOD_MARKER_RE.match(next_l)
                            )
                            h_date = m_wrap.group("date")
                            direction = m_wrap.group("dir")
                            vch_type = m_wrap.group("vch_type").strip()
                            vch_no = m_wrap.group("vch_no").strip()
                            remainder = m_wrap.group("remainder")
                            if is_continuation:
                                particulars = next_l.strip()
                                consumed_next_line = True
                            else:
                                # No genuine next-line continuation found. This
                                # is not a failed match -- the voucher header
                                # line itself has no counter-party name printed
                                # at all (a blank-particulars header), as
                                # opposed to a name that wraps onto the next
                                # line. Fixed 2026-10-01: this branch previously
                                # discarded the whole match here, leaving
                                # vch_type/vch_no as None and falling through to
                                # the Detail-Leg regex, which wrongly consumed
                                # the trailing running balance as the leg amount
                                # and silently injected a garbage leg into
                                # whatever voucher was still active -- see the
                                # J & K Bank Ltd Rs.55,00,000 investigation.
                                particulars = ""

                if vch_type and vch_no:
                    if h_date:
                        active_date = h_date
                    else:
                        h_date = active_date

                    leg_amount, drcr = _classify_header_amounts(remainder, direction)

                    if leg_amount is not None and drcr:
                        current_occurrence = {
                            "entry_id": f"{vch_type}_{vch_no}",
                            "date": h_date,
                            "vch_type": vch_type,
                            "vch_no": vch_no,
                            "page": page_num,
                            "account": current_account,
                            "legs": [],
                            "narration": [],
                        }
                        occurrences.append(current_occurrence)

                        is_shape_a = particulars.lower() == "(as per details)"
                        is_blank_particulars = particulars == ""
                        if is_shape_a:
                            if current_account:
                                current_occurrence["legs"].append({
                                    "particulars": current_account, "amount": leg_amount,
                                    "dr_cr": drcr, "resolved": True, "is_header_leg": True,
                                })
                        else:
                            # Shape B ("To/By <Name> ... amount Dr/Cr"): <Name> is the
                            # COUNTER-PARTY printed on this line, not this page's own
                            # account. drcr always describes current_account's own
                            # movement (see _classify_header_amounts), so the leg must
                            # be attributed to current_account -- mirroring the
                            # is_shape_a branch above -- not to the printed name.
                            # Fixed 2026-10-01: this branch previously used
                            # `particulars` (the printed counter-party) directly,
                            # mislabeling ~67% of all header legs and defeating
                            # cross-page corroboration for them (see investigation
                            # findings, Shape B bug). The printed counter-party name
                            # is preserved as narration rather than discarded, so it
                            # isn't lost for the minority of vouchers whose
                            # counter-party has no ledger page of its own elsewhere
                            # in the document.
                            if current_account:
                                # The printed counter-party name is kept on the leg
                                # under a private "_counterparty" key rather than
                                # appended straight to narration here, because a
                                # wrapped-continuation line immediately following
                                # (e.g. "PAYABLE" on its own line, completing
                                # "ELECTRICITY BILL" -> "ELECTRICITY BILL PAYABLE")
                                # may still need to extend it -- see the wrapped-
                                # particulars-continuation handling below, and the
                                # finalization pass after the page loop that moves
                                # this into narration and removes the private key.
                                current_occurrence["legs"].append({
                                    "particulars": current_account, "amount": leg_amount,
                                    "dr_cr": drcr, "resolved": True, "is_header_leg": True,
                                    "_counterparty": particulars,
                                })
                                if is_blank_particulars:
                                    # Distinct from the normal counterparty-
                                    # preservation narration below (which only
                                    # fires for a non-empty printed name): flag
                                    # explicitly that no name was printed at
                                    # all, for traceability back to the source.
                                    current_occurrence["narration"].append(
                                        f"[no counterparty name printed in source: {vch_type} {vch_no}]"
                                    )
                            else:
                                # No page-detected account to attribute this leg to.
                                # Preserve pre-fix behavior rather than silently
                                # dropping the leg. Not observed in the real dataset
                                # as of 2026-10-01 (current_account is always set by
                                # the time Shape B legs are created), kept as a safety
                                # net for future documents.
                                current_occurrence["legs"].append({
                                    "particulars": particulars, "amount": leg_amount,
                                    "dr_cr": drcr, "resolved": True, "is_header_leg": True,
                                })
                    else:
                        current_occurrence = None
                        unresolved.append({
                            "reason": "header_no_parseable_amount",
                            "raw_line": line, "source_page": page_num,
                        })
                    i += 2 if consumed_next_line else 1
                    continue

                # Detail Leg
                leg_match = _LEG_RE.match(line)
                if leg_match:
                    if current_occurrence:
                        current_occurrence["legs"].append({
                            "particulars": leg_match.group("particulars").strip(),
                            "amount": normalize_indian_number(leg_match.group("amount")),
                            "dr_cr": leg_match.group("drcr"), "resolved": True,
                            "is_header_leg": False,
                        })
                    else:
                        unresolved.append({
                            "reason": "detail_leg_without_active_voucher",
                            "raw_line": line, "source_page": page_num,
                        })
                    i += 1
                    continue

                # Orphan Amount
                orphan_match = _ORPHAN_AMOUNT_RE.match(line)
                if orphan_match:
                    if current_occurrence:
                        current_occurrence["legs"].append({
                            "particulars": None,
                            "amount": normalize_indian_number(orphan_match.group("amount")),
                            "dr_cr": orphan_match.group("drcr"), "resolved": False,
                            "is_header_leg": False,
                        })
                    else:
                        unresolved.append({
                            "reason": "orphan_amount_without_active_voucher",
                            "raw_line": line, "source_page": page_num,
                        })
                    i += 1
                    continue

                # Wrapped particulars continuation for the preceding leg
                lower_line = line.lower()
                is_continuation_token = (
                    lower_line in _PARTICULARS_CONTINUATION_TOKENS or
                    any(lower_line == t for t in _PARTICULARS_CONTINUATION_TOKENS)
                )

                # Fixed 2026-10-02 (FEES/COMPANY narration-split bug, V3.9):
                # a bare continuation-token line (e.g. "FEES", "COMPANY") that
                # immediately follows a line just classified as narration
                # (e.g. "WEIGHT AND MEASUREMENT", "AGGARWAL AND") is the
                # second half of that narration phrase -- not a continuation
                # of the preceding LEG's particulars/counterparty. Priority
                # previously ran wrapped-particulars-continuation before
                # narration, so this line got glued onto the active leg
                # instead of its own narration entry. Guarded tightly: only
                # fires when the immediately preceding raw source line is
                # BOTH already narration-classified AND is literally the
                # last entry appended to this occurrence's narration (so a
                # continuation token can never "reach back" across an
                # intervening leg/header line). See BASELINE_CHECKPOINT_V3.9.md.
                prev_raw_line = raw_lines[i - 1] if i > body_start_idx else None
                prev_was_narration = (
                    is_continuation_token
                    and prev_raw_line is not None
                    and current_occurrence is not None
                    and current_occurrence["narration"]
                    and current_occurrence["narration"][-1] == prev_raw_line
                    and (
                        any(prev_raw_line.lower().startswith(p) for p in _NARRATION_PREFIXES)
                        or (prev_raw_line.isupper() and len(prev_raw_line.split()) >= 2
                            and not prev_raw_line.startswith(("TO ", "BY ")))
                    )
                )

                if prev_was_narration:
                    current_occurrence["narration"][-1] = (
                        f"{current_occurrence['narration'][-1]} {line}"
                    )
                    i += 1
                    continue

                is_wrapped_part = (
                    current_occurrence is not None
                    and len(current_occurrence["legs"]) > 0
                    and is_continuation_token
                )

                if is_wrapped_part:
                    last_leg = current_occurrence["legs"][-1]
                    # A Shape B synthetic header leg's "particulars" is now
                    # current_account (this page's own account), not the printed
                    # text -- a continuation word belongs to the printed
                    # counter-party name stashed in "_counterparty", not to
                    # current_account.
                    if "_counterparty" in last_leg:
                        if last_leg.get("_counterparty"):
                            last_leg["_counterparty"] = f"{last_leg['_counterparty']} {line}"
                    elif last_leg.get("particulars"):
                        last_leg["particulars"] = f"{last_leg['particulars']} {line}"
                    i += 1
                    continue

                # Narration for active voucher
                is_narration = (
                    current_occurrence is not None
                    and (any(lower_line.startswith(p) for p in _NARRATION_PREFIXES) or
                         (line.isupper() and len(line.split()) >= 2 and not line.startswith(("TO ", "BY "))))
                )

                if is_narration:
                    current_occurrence["narration"].append(line)
                    i += 1
                    continue

                if line in _ANONYMIZATION_ARTIFACT_FRAGMENTS:
                    unresolved.append({
                        "reason": "anonymization_artifact_fragment_discarded",
                        "raw_line": line, "source_page": page_num,
                    })
                    i += 1
                    continue

                # Guard against stale occurrence leaks: line with To/By or numbers closes active occurrence
                if line.startswith(("To ", "By ", "To\t", "By\t")) or re.search(r"[\d,]+\.\d{2}", line):
                    current_occurrence = None

                unresolved.append({
                    "reason": "unclassified_line", "raw_line": line,
                    "source_page": page_num,
                })
                i += 1

    # Finalize Shape B counter-party names into narration now that no further
    # wrapped-continuation line can extend them (continuation only ever looks
    # at the page immediately following, and the page loop above has finished).
    # Must run BEFORE the Pass-1 dedup pass below, so a counter-party name is
    # preserved even if its leg later turns out to be a redundant duplicate of
    # an explicit detail leg and gets dropped.
    for occ in occurrences:
        for leg in occ["legs"]:
            cp = leg.pop("_counterparty", None)
            if cp:
                occ["narration"].append(f"[counterparty per source line: {cp}]")

    # Pass-1 minimal fix: If a synthetic header leg was created for a Shape-A
    # voucher, and an explicit detail leg in the same occurrence already captures
    # the exact same particulars, amount, and dr_cr, retain the explicit detail
    # leg and remove the redundant synthetic header leg.
    for occ in occurrences:
        header_legs = [l for l in occ["legs"] if l.get("is_header_leg")]
        detail_legs = [l for l in occ["legs"] if not l.get("is_header_leg")]
        if header_legs and detail_legs:
            hl = header_legs[0]
            if any(
                dl["particulars"] == hl["particulars"]
                and dl["dr_cr"] == hl["dr_cr"]
                and dl["amount"] == hl["amount"]
                for dl in detail_legs
            ):
                occ["legs"] = detail_legs

    return occurrences, state_events, unresolved


def merge_occurrences(occurrences):
    entries = {}
    entry_order = []
    conflicts = []

    for occ in occurrences:
        base_eid = occ["entry_id"]
        # Date-aware voucher keying prevents false conflicts when voucher numbers repeat across periods
        eid = base_eid
        if eid in entries and entries[eid]["date"] and occ["date"] and entries[eid]["date"] != occ["date"]:
            eid = f"{base_eid}_{occ['date']}"

        if eid not in entries:
            entries[eid] = {
                "date": occ["date"], "vch_type": occ["vch_type"], "vch_no": occ["vch_no"],
                "named_legs": {},
                "orphan_legs": [],
                "narration": set(),
                "source_accounts": set(),
            }
            entry_order.append(eid)

        if occ.get("account"):
            entries[eid]["source_accounts"].add(occ["account"])
        for n in occ.get("narration", []):
            entries[eid]["narration"].add(n)

        for leg in occ["legs"]:
            if leg["particulars"] is None:
                entries[eid]["orphan_legs"].append({
                    "amount": leg["amount"], "dr_cr": leg["dr_cr"],
                    "source_page": occ["page"], "resolved": False,
                })
                continue

            key = (leg["particulars"], leg["dr_cr"])
            existing = entries[eid]["named_legs"].get(key)
            if existing is None:
                entries[eid]["named_legs"][key] = {
                    "amount": leg["amount"], "source_pages": [occ["page"]],
                    "resolved": leg["resolved"],
                }
            elif existing["amount"] == leg["amount"]:
                existing["source_pages"].append(occ["page"])
            else:
                conflicts.append({
                    "reason": "leg_amount_mismatch_across_occurrences",
                    "raw_line": f"{leg['particulars']} {leg['amount']} {leg['dr_cr']} "
                                f"(entry {eid}, page {occ['page']}) conflicts with "
                                f"earlier {existing['amount']} on page(s) {existing['source_pages']}",
                    "source_page": occ["page"],
                })

    # Per-voucher cross-spelling consolidation (V3.9) -- see
    # _PER_VOUCHER_NAME_ALIASES above for the full safety rationale. Narrow
    # and explicit: only the whitelisted (short, long) pairs, only within
    # one voucher at a time, and only when the evidence this specific
    # voucher provides actually supports it.
    for eid, e in entries.items():
        for short_name, long_name in _PER_VOUCHER_NAME_ALIASES:
            for dr_cr in ("Dr", "Cr"):
                short_key = (short_name, dr_cr)
                long_key = (long_name, dr_cr)
                short_leg = e["named_legs"].get(short_key)
                long_leg = e["named_legs"].get(long_key)
                if short_leg is None or long_leg is None:
                    continue
                if set(short_leg["source_pages"]) & set(long_leg["source_pages"]):
                    # Both spellings were printed on the same page -- this
                    # is not a cross-ledger reprint of one leg, so do not
                    # merge; leave both legs exactly as parsed.
                    continue
                if short_leg["amount"] != long_leg["amount"]:
                    # Different amounts -- not the same leg, do not merge.
                    continue
                long_leg["source_pages"] = sorted(
                    set(long_leg["source_pages"]) | set(short_leg["source_pages"])
                )
                long_leg["resolved"] = long_leg["resolved"] and short_leg["resolved"]
                del e["named_legs"][short_key]

    # Redacted-account / coded-counterparty named-leg consolidation
    # (FY2025-26 ledger investigation). Generalizes the orphan absorption
    # below from orphan legs (particulars=None) to NAMED legs: the same
    # physical leg is reprinted on two different pages under two different
    # printed labels -- a "P0xx"-style per-document anonymization code on
    # one page, and this account's own "[Redacted account...]" placeholder
    # label on its own dedicated ledger page -- so it was keyed into
    # named_legs twice, double-counting it against the voucher's Dr/Cr
    # balance (this was the dominant cause of the FY2025-26 ledger's
    # unbalanced-voucher count).
    #
    # Gated exactly as narrow as every alias mechanism above: within a
    # single voucher, a redacted-placeholder leg is merged with another
    # named leg ONLY when ALL hold -- same dr_cr, same amount (to the
    # paisa), and completely disjoint source pages (ruling out two
    # genuinely different legs that coincidentally share an amount on the
    # same page). Verified by exhaustive simulation across the full
    # population of BOTH documents before this was written: every
    # redacted-placeholder leg was checked against every other named leg in
    # its own voucher under this exact criterion -- 182 candidates in the
    # FY2025-26 ledger, ALL with exactly one match, ZERO ambiguous (2+
    # candidates); ZERO matching candidates at all in the frozen FY2024-25
    # ledger (confirming this cannot alter that baseline -- independently
    # reconfirmed by the full before/after diff run alongside this change).
    for eid, e in entries.items():
        redacted_keys = [
            key for key in e["named_legs"]
            if key[0] and key[0].startswith(_REDACTED_PLACEHOLDER_PREFIX)
        ]
        for rkey in redacted_keys:
            if rkey not in e["named_legs"]:
                continue  # already merged away earlier in this same pass
            rleg = e["named_legs"][rkey]
            r_pages = set(rleg["source_pages"])
            candidates = [
                key for key, leg in e["named_legs"].items()
                if key != rkey
                and not (key[0] and key[0].startswith(_REDACTED_PLACEHOLDER_PREFIX))
                and key[1] == rkey[1]
                and leg["amount"] == rleg["amount"]
                and not (set(leg["source_pages"]) & r_pages)
            ]
            if len(candidates) == 1:
                other_key = candidates[0]
                other_leg = e["named_legs"][other_key]
                other_leg["source_pages"] = sorted(
                    set(other_leg["source_pages"]) | r_pages
                )
                other_leg["resolved"] = other_leg["resolved"] and rleg["resolved"]
                del e["named_legs"][rkey]
            # 0 or 2+ candidates -- do not guess; leave both legs exactly as
            # parsed (still visible, still contributing to entry_balanced).

    # Redacted-account orphan absorption (Pattern 4, post-V3.9) -- see
    # _REDACTED_PLACEHOLDER_PREFIX above for the full safety rationale and
    # the bounded simulation this is gated on. Only absorb an orphan leg
    # when, within its OWN voucher, EXACTLY ONE named leg's particulars is a
    # redacted-account placeholder with the SAME dr_cr and the SAME amount
    # -- otherwise leave the orphan exactly as parsed (still reported as
    # UNRESOLVED). Absorbing means folding the orphan's source page into the
    # matched placeholder leg's corroboration (it is the same physical leg,
    # just reprinted with the counterparty name blanked out on this other
    # page) and removing it from orphan_legs so it is not double-counted
    # against the voucher's Dr/Cr balance.
    for eid, e in entries.items():
        if not e["orphan_legs"]:
            continue
        redacted_named = [
            (key, leg) for key, leg in e["named_legs"].items()
            if key[0] and key[0].startswith(_REDACTED_PLACEHOLDER_PREFIX)
        ]
        remaining_orphans = []
        for orphan in e["orphan_legs"]:
            candidates = [
                (key, leg) for key, leg in redacted_named
                if key[1] == orphan["dr_cr"] and leg["amount"] == orphan["amount"]
            ]
            if len(candidates) == 1:
                _, matched_leg = candidates[0]
                matched_leg["source_pages"] = sorted(
                    set(matched_leg["source_pages"]) | {orphan["source_page"]}
                )
            else:
                # 0 or 2+ candidates -- do not guess; leave the orphan as an
                # unresolved leg exactly as before.
                remaining_orphans.append(orphan)
        e["orphan_legs"] = remaining_orphans

    return entries, entry_order, conflicts


def write_output(entries, entry_order, unresolved, state_events, out_prefix):
    entries_path = f"{out_prefix}_entries.csv"
    unresolved_path = f"{out_prefix}_unresolved.csv"
    state_path = f"{out_prefix}_state_events.csv"

    with open(entries_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "entry_id", "date", "vch_type", "vch_no", "particulars",
            "amount", "dr_cr", "resolved", "source_pages",
            "corroboration_count", "entry_balanced", "narration",
        ])
        writer.writeheader()
        for entry_id in entry_order:
            e = entries[entry_id]
            named_amounts_dr = [l["amount"] or 0 for k, l in e["named_legs"].items() if k[1] == "Dr"]
            named_amounts_cr = [l["amount"] or 0 for k, l in e["named_legs"].items() if k[1] == "Cr"]
            orphan_dr = [l["amount"] or 0 for l in e["orphan_legs"] if l["dr_cr"] == "Dr"]
            orphan_cr = [l["amount"] or 0 for l in e["orphan_legs"] if l["dr_cr"] == "Cr"]
            dr_total = sum(named_amounts_dr) + sum(orphan_dr)
            cr_total = sum(named_amounts_cr) + sum(orphan_cr)
            balanced = abs(dr_total - cr_total) < 0.01
            narr_str = " | ".join(sorted(e.get("narration", [])))

            for (particulars, dr_cr), leg in e["named_legs"].items():
                writer.writerow({
                    "entry_id": entry_id, "date": e["date"], "vch_type": e["vch_type"],
                    "vch_no": e["vch_no"], "particulars": particulars, "dr_cr": dr_cr,
                    "amount": leg["amount"], "resolved": leg["resolved"],
                    "source_pages": ";".join(str(p) for p in leg["source_pages"]),
                    "corroboration_count": len(leg["source_pages"]),
                    "entry_balanced": balanced,
                    "narration": narr_str,
                })
            for leg in e["orphan_legs"]:
                writer.writerow({
                    "entry_id": entry_id, "date": e["date"], "vch_type": e["vch_type"],
                    "vch_no": e["vch_no"], "particulars": "UNRESOLVED",
                    "dr_cr": leg["dr_cr"], "amount": leg["amount"], "resolved": False,
                    "source_pages": str(leg["source_page"]), "corroboration_count": 1,
                    "entry_balanced": balanced,
                    "narration": narr_str,
                })

    with open(unresolved_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reason", "raw_line", "source_page"])
        writer.writeheader()
        writer.writerows(unresolved)

    with open(state_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["event_type", "account", "date", "amount", "source_page"])
        writer.writeheader()
        writer.writerows(state_events)

    return entries_path, unresolved_path, state_path


def reconstruct_full_document(pdf_path):
    """Reconstructs an ENTIRE ledger PDF end to end -- page 1 through its
    last page, auto-detected via pdfplumber -- and writes the same three
    output CSVs main() does. Added 2026-10-04 (dashboard finalization) so
    the dashboard's "Re-run full ledger reconstruction" button has a
    callable function instead of needing to shell out to main()'s CLI
    interface.

    Why auto-detecting the page range is safe, not a guess: main()'s
    start_page/end_page CLI arguments exist for PARTIAL-range diagnostics
    during development (see tests/test_header_carryover_fix.py's
    _resolve_headerless_pages(pdf, 470, 550) calls, for example) -- every
    real, committed run against either of this project's two real ledgers
    has always used the WHOLE document (1 to len(pdf.pages): 926 pages for
    ledgers_redacted.pdf, 888 for Ledgers_Anonymised.pdf -- see
    BASELINE_CHECKPOINT_V3.9.md and V3.11.md). There has never been a case
    in this project where the correct page range for a full-document
    reconstruction was anything other than "the whole file", so detecting
    it from the PDF itself isn't a judgment call -- it's just not
    hardcoding a number pdfplumber can already tell us.

    What this does NOT remove: the need to look at the result. A genuinely
    new ledger PDF (a third financial year, a different firm) could expose
    a header-shape this parser has never seen -- exactly what happened
    with the FY2025-26 ledger across V3.11-V3.13 (new voucher-number
    formats, the bare-Account qualifier bug, continuation-page name
    changes). The unbalanced-voucher count and unresolved-line count this
    function returns are the same safety signals main() has always
    printed; `assess_reconstruction_confidence()` below turns them into an
    honest HIGH/LOW verdict (calibrated against this project's own two
    real, fully-investigated documents, not a guessed threshold) so the
    dashboard can surface a low-confidence document prominently instead of
    silently trusting it.

    Returns a dict: {entries_path, unresolved_path, state_path,
    total_pages, total_vouchers, total_legs, unresolved_count,
    unbalanced_count, unbalanced_entry_ids, date_range_start,
    date_range_end}. The last two are ISO "YYYY-MM-DD" strings (the
    earliest/latest voucher date found), or None if no voucher had a
    parseable date -- used by the dashboard to auto-label a document by
    the financial period its own data actually covers, instead of a
    label baked into the filename.
    """
    import pdfplumber
    from datetime import datetime

    with pdfplumber.open(pdf_path) as pdf:
        end_page = len(pdf.pages)

    # Same frozen-baseline guard as main() -- see collect_occurrences()'s
    # apply_continuation_name_aliases docstring.
    is_frozen_baseline = os.path.splitext(os.path.basename(pdf_path))[0] == "ledgers_redacted"
    occurrences, state_events, unresolved = collect_occurrences(
        pdf_path, 1, end_page,
        apply_continuation_name_aliases=not is_frozen_baseline,
    )
    entries, entry_order, conflicts = merge_occurrences(occurrences)
    unresolved = unresolved + conflicts

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    entries_path, unresolved_path, state_path = write_output(
        entries, entry_order, unresolved, state_events,
        os.path.join(out_dir, f"{base}_reconstructed")
    )

    unbalanced = []
    total_legs = 0
    parsed_dates = []
    for eid in entry_order:
        e = entries[eid]
        dr_total = sum(l["amount"] or 0 for k, l in e["named_legs"].items() if k[1] == "Dr") \
                   + sum(l["amount"] or 0 for l in e["orphan_legs"] if l["dr_cr"] == "Dr")
        cr_total = sum(l["amount"] or 0 for k, l in e["named_legs"].items() if k[1] == "Cr") \
                   + sum(l["amount"] or 0 for l in e["orphan_legs"] if l["dr_cr"] == "Cr")
        if abs(dr_total - cr_total) >= 0.01:
            unbalanced.append(eid)
        total_legs += len(e["named_legs"]) + len(e["orphan_legs"])
        try:
            parsed_dates.append(datetime.strptime(e["date"].strip(), "%d-%b-%y"))
        except (ValueError, AttributeError, KeyError):
            pass  # a handful of unparseable dates shouldn't block the whole range

    date_range_start = min(parsed_dates).strftime("%Y-%m-%d") if parsed_dates else None
    date_range_end = max(parsed_dates).strftime("%Y-%m-%d") if parsed_dates else None

    return {
        "entries_path": entries_path,
        "unresolved_path": unresolved_path,
        "state_path": state_path,
        "total_pages": end_page,
        "total_vouchers": len(entry_order),
        "total_legs": total_legs,
        "unresolved_count": len(unresolved),
        "unbalanced_count": len(unbalanced),
        "unbalanced_entry_ids": unbalanced,
        "date_range_start": date_range_start,
        "date_range_end": date_range_end,
    }


# Confidence thresholds below are calibrated against this project's own two
# real, fully-investigated documents' ACTUAL measured numbers -- not
# guessed at what "seems reasonable":
#   unresolved-line rate:     3.74% (ledgers_redacted.pdf/FY2024-25)
#                              1.97% (Ledgers_Anonymised.pdf/FY2025-26)
#   unbalanced-voucher rate:  0.032% (FY2024-25, 2/6,216)
#                              0.050% (FY2025-26, 3/5,947)
# Both thresholds sit well above either real baseline, so a document that
# is genuinely the same Tally ledger-export shape this parser was built
# and verified against clears them comfortably. This is meant to catch a
# document that does NOT match that pattern (a different accounting
# package's export, a scanned/OCR'd ledger, a layout this parser has
# never seen) -- not to flag normal, expected document-to-document
# variation within the pattern it already handles well.
UNRESOLVED_PCT_WARN_THRESHOLD = 10.0
UNBALANCED_PCT_WARN_THRESHOLD = 1.0
MIN_VOUCHERS_FOR_CONFIDENCE = 20


def assess_reconstruction_confidence(recon_result):
    """Turns reconstruct_full_document()'s raw counts into an honest
    {"confidence": "high"|"low", "reasons": [str, ...]} verdict -- added
    2026-10-04 specifically so a newly-uploaded, never-before-seen ledger
    PDF doesn't get shown with the same unqualified authority as this
    project's two fully-verified real documents just because it happened
    to parse without raising an exception. "low" means at least one
    reason fired; `reasons` is empty exactly when confidence is "high".

    Deliberately does NOT try to guess WHY a document scores low (a
    different accounting software, a scanned image needing OCR, a
    one-off layout quirk) -- it states the measured numbers and lets a
    human look, the same "surface it, don't explain it away" convention
    this project already uses for Benford's Law and ML anomaly
    detection."""
    reasons = []
    total_vouchers = recon_result["total_vouchers"]
    total_legs = recon_result["total_legs"]

    if total_vouchers < MIN_VOUCHERS_FOR_CONFIDENCE:
        reasons.append(
            f"Only {total_vouchers} voucher(s) found -- too few for the percentage "
            f"checks below to mean anything; this usually means the parser didn't "
            f"recognize this document's layout at all."
        )

    if total_legs > 0:
        unresolved_pct = recon_result["unresolved_count"] / total_legs * 100
        if unresolved_pct > UNRESOLVED_PCT_WARN_THRESHOLD:
            reasons.append(
                f"{unresolved_pct:.1f}% of lines could not be attributed to any "
                f"voucher (both real documents this parser was verified against "
                f"sit under 4%)."
            )

    if total_vouchers > 0:
        unbalanced_pct = recon_result["unbalanced_count"] / total_vouchers * 100
        if unbalanced_pct > UNBALANCED_PCT_WARN_THRESHOLD:
            reasons.append(
                f"{unbalanced_pct:.2f}% of vouchers don't balance (both real "
                f"documents this parser was verified against sit under 0.1%)."
            )

    return {"confidence": "low" if reasons else "high", "reasons": reasons}


def main():
    if len(sys.argv) < 4:
        print("Usage: python reconstruct_ledger_entries.py <pdf_path> <start_page> <end_page>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    start_page = int(sys.argv[2])
    end_page = int(sys.argv[3])

    # The frozen FY2024-25 baseline ("ledgers_redacted.pdf") must stay
    # byte-for-byte untouched unless a change to it is explicitly reviewed
    # and approved -- see collect_occurrences()'s apply_continuation_name_aliases
    # docstring. Every other document (the FY2025-26 ledger included) gets
    # the new continuation-name-alias mechanism applied by default.
    is_frozen_baseline = os.path.splitext(os.path.basename(pdf_path))[0] == "ledgers_redacted"
    occurrences, state_events, unresolved = collect_occurrences(
        pdf_path, start_page, end_page,
        apply_continuation_name_aliases=not is_frozen_baseline,
    )
    entries, entry_order, conflicts = merge_occurrences(occurrences)
    unresolved = unresolved + conflicts

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    entries_path, unresolved_path, state_path = write_output(
        entries, entry_order, unresolved, state_events,
        os.path.join(out_dir, f"{base}_reconstructed")
    )

    total_named_legs = sum(len(entries[e]["named_legs"]) for e in entry_order)
    total_orphan_legs = sum(len(entries[e]["orphan_legs"]) for e in entry_order)
    unbalanced = []
    for eid in entry_order:
        e = entries[eid]
        dr_total = sum(l["amount"] or 0 for k, l in e["named_legs"].items() if k[1] == "Dr") \
                   + sum(l["amount"] or 0 for l in e["orphan_legs"] if l["dr_cr"] == "Dr")
        cr_total = sum(l["amount"] or 0 for k, l in e["named_legs"].items() if k[1] == "Cr") \
                   + sum(l["amount"] or 0 for l in e["orphan_legs"] if l["dr_cr"] == "Cr")
        if abs(dr_total - cr_total) >= 0.01:
            unbalanced.append(eid)

    print(f"Parsed {len(occurrences)} raw voucher occurrences across pages {start_page}-{end_page}.")
    print(f"Merged into {len(entry_order)} canonical vouchers "
          f"({total_named_legs} named legs + {total_orphan_legs} orphan legs) -> {entries_path}")
    print(f"Unbalanced entries (Dr != Cr): {len(unbalanced)} / {len(entry_order)}")
    if unbalanced:
        print(f"  entry_ids: {unbalanced[:10]}")
    print(f"State events (opening/closing/brought-forward/carried-over): "
          f"{len(state_events)} -> {state_path}")
    print(f"Unresolved lines + conflicts: {len(unresolved)} -> {unresolved_path}")

    print("\nSample canonical vouchers:")
    for entry_id in entry_order[:3]:
        e = entries[entry_id]
        print(f"\n  {entry_id} | {e['date']} | {e['vch_type']} {e['vch_no']}")
        for (particulars, dr_cr), leg in e["named_legs"].items():
            print(f"    {particulars} | {leg['amount']} | {dr_cr} | "
                  f"pages={leg['source_pages']} | corroboration={len(leg['source_pages'])}")
        for leg in e["orphan_legs"]:
            print(f"    UNRESOLVED | {leg['amount']} | {leg['dr_cr']} | page={leg['source_page']}")


if __name__ == "__main__":
    main()
