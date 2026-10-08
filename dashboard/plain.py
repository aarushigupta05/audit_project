"""
dashboard/plain.py
------------------
Plain-language wording for what the dashboard shows. Display only: nothing
here changes a number, a flag or a report file -- it turns the engine's
technical phrases (column names, check names, statistics) into sentences a
business owner or accountant can read without knowing how the checks work.
No streamlit import, so it is unit-tested directly (tests/test_plain.py).

The exact technical figures (chi-square, MAD, anomaly scores) are never
removed; the dashboard keeps them in a collapsed "Technical details" box.
"""
import re
from datetime import datetime

# Check names as the data layer produces them -> what the reader sees.
CHECK_LABELS = {
    "Round-Number Bias": "Round amount",
    "Duplicate Transaction": "Possible duplicate",
    "Segregation of Duties": "Same person entered and approved",
}


def check_label(name):
    return CHECK_LABELS.get(name, name)


def plain_check_names(text):
    """'Round-Number Bias, Duplicate Transaction' -> 'Round amount, Possible duplicate'."""
    if not text:
        return text
    for tech, plain in CHECK_LABELS.items():
        text = text.replace(tech, plain)
    return text


_ROUND_RE = re.compile(r"^Suspiciously round amount \(([\d.,]+)\)$")
_DUP_RE = re.compile(
    r"^Duplicate transaction candidate: matches voucher\(s\) (.+?) on (.+?) "
    r"\((Dr|Cr) (.+) Rs\.([\d.,]+)\)$"
)


def _money(text):
    try:
        value = float(str(text).replace(",", ""))
    except ValueError:
        return f"Rs.{text}"
    return f"Rs.{value:,.0f}" if value == int(value) else f"Rs.{value:,.2f}"


def plain_reason(reason):
    """One engine reason sentence -> plain wording; anything unrecognised is
    returned unchanged (never hidden, never altered into something wrong)."""
    if not reason:
        return reason
    m = _ROUND_RE.match(reason.strip())
    if m:
        return f"Round amount ({_money(m.group(1))}) -- an exact multiple of Rs.10,000"
    m = _DUP_RE.match(reason.strip())
    if m:
        vouchers, date, side, account, amount = m.groups()
        return (
            f"Same account ({account}) and same amount ({_money(amount)}) on {date} "
            f"as voucher {vouchers} -- possible repeated entry"
        )
    return reason


def plain_why(text):
    """The Priority table's 'why' cell: several reasons joined by '; ' (a
    voucher's two legs often repeat the same sentence) -> plain, de-duplicated."""
    if not text:
        return text
    seen, out = set(), []
    for part in str(text).split("; "):
        plain = plain_reason(part)
        # The two legs of one matched pair differ only in Dr/Cr account; once
        # reworded they read almost the same -- keep each distinct sentence once.
        if plain not in seen:
            seen.add(plain)
            out.append(plain)
    return "; ".join(out)


def plain_evidence(item):
    """'Round-Number Bias: Suspiciously round amount (X)' -> 'Round amount: ...'."""
    if ": " in item:
        name, reason = item.split(": ", 1)
        return f"{check_label(name)}: {plain_reason(reason)}"
    return item


# Analysis step names (pinned in dashboard/data.py's _ANALYSIS_STEPS) -> the
# wording shown while Re-run Analysis works and when it finishes.
STEP_LABELS = {
    "Tax & ledger reconciliation": "Tax return and trial balance checks",
    "GST reconciliation": "GST return checks",
    "Ledger analysis refresh": "Ledger checks",
    "Ledger vs GST reconciliation": "Ledger vs GST return comparison",
}


def step_label(name):
    return STEP_LABELS.get(name, name)


def ledger_name(label):
    """Ledger labels carry an internal tag for the protected sample ledger
    ('FY2024-25 (frozen baseline)'); readers see 'sample ledger'."""
    return label.replace("(frozen baseline)", "(sample ledger)") if label else label


# --- Benford's Law ---------------------------------------------------------

MAD_PLAIN = {
    "Close conformity": "Close to the expected pattern",
    "Acceptable conformity": "Within the acceptable range",
    "Marginally acceptable": "Marginal deviation",
    "Nonconformity": "Significant deviation",
}


def mad_plain(band):
    return MAD_PLAIN.get(band, band)


def benford_headline(benford):
    """One plain sentence for the whole Benford result. The strict test
    (chi-square) flags almost any large ledger, so the practical test (MAD)
    drives the wording -- the same reason this project leans on MAD."""
    if not benford or benford.get("chi_square") is None:
        return ""
    band = benford.get("mad_conformity")
    deviates = benford.get("deviates")
    if band in ("Close conformity", "Acceptable conformity"):
        if deviates:
            return (
                "Overall, the first digits of the amounts follow the expected pattern. "
                "The strict test is highly sensitive on large ledgers and flags minor "
                "differences, so the practical test is used for the overall result."
            )
        return "Both tests agree: the first digits of the amounts follow the expected pattern."
    if band == "Marginally acceptable":
        return (
            "The first digits of the amounts differ slightly from the expected "
            "pattern. The largest and most repeated amounts are worth reviewing."
        )
    if band == "Nonconformity":
        return (
            "The first digits of the amounts differ markedly from the expected "
            "pattern. This can result from fixed pricing or artificially chosen "
            "amounts; review the largest and most repeated amounts."
        )
    return ""


# --- Table column names ------------------------------------------------------

GAP_COLUMNS = {
    "vch_type": "Voucher type", "prefix": "Series prefix", "missing_count": "Missing",
    "group_min": "First number", "group_max": "Last number", "group_size": "Numbers present",
    "severity": "Severity", "missing_numbers": "Missing numbers",
}

ML_COLUMNS = {
    "entry_id": "Voucher", "account": "Account", "dr_cr": "Dr/Cr", "amount": "Amount",
    "date": "Date", "vch_type": "Voucher type", "severity": "Severity",
    "anomaly_rank_pct": "Unusualness (top %)",
}

TAX_FLAG_COLUMNS = {
    "entity": "Deductor", "reason": "What differs", "severity": "Severity", "source": "Source",
}

TB_FLAG_COLUMNS = {
    "account": "Account", "side": "Side", "tb_amount": "Per trial balance",
    "gl_amount": "Per ledger", "mismatch_amount": "Difference", "severity": "Severity",
    "variants_used": "Name variants matched",
}

GST_FLAG_COLUMNS = {
    "period": "Month", "check": "Check", "reason": "What differs", "severity": "Severity",
}


# GST finding codes (the "check" column) -> what the reader sees.
GST_CHECK_LABELS = {
    "missing_filing": "Return missing",
    "gstr1_vs_gstr3b_taxable_supplies": "Taxable sales: GSTR-1 vs GSTR-3B",
    "gstr1_vs_gstr3b_non_gst_supplies": "Sales without GST: GSTR-1 vs GSTR-3B",
    "gstr3b_payment_completeness": "Tax paid in GSTR-3B",
    "ledger_activity_with_no_gstr1_filing": "No GSTR-1 for a month with sales",
    "ledger_vs_gstr1_taxable_supply": "Taxable sales: ledger vs GSTR-1",
    "ledger_vs_gstr1_non_gst_supply": "Sales without GST: ledger vs GSTR-1",
}


def rename_gst_flags(df):
    """A GST findings table in display wording: readable column names and
    readable check names (unknown codes are left as they are)."""
    out = df.copy()
    if "check" in out.columns:
        out["check"] = out["check"].map(lambda c: GST_CHECK_LABELS.get(c, c))
    return rename_columns(out, GST_FLAG_COLUMNS)


def rename_columns(df, mapping):
    """Rename whichever of `mapping`'s columns exist; leave the rest."""
    return df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})


# --- Times -----------------------------------------------------------------

def friendly_time(value):
    """'2026-10-07T18:31:15' -> '7 Oct 2026, 6:31 pm'; unparseable text is
    returned unchanged."""
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    hour = dt.hour % 12 or 12
    return f"{dt.day} {dt.strftime('%b %Y')}, {hour}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


# --- Ledger vs GSTR-1: why a comparison did or did not happen ---------------

def ledger_gst_status_message(summary):
    """Text for a ledger-vs-GSTR-1 result that could NOT compare anything, or
    None when it did (status ok / unknown). `summary` is
    data.ledger_gstr_summary()'s dict."""
    status = (summary or {}).get("status")
    if status == "no_returns":
        return (
            "No GSTR-1 return has been uploaded for this ledger yet. Upload the "
            "GSTR-1 returns of the same business and period under **Process "
            "Documents**, then click **Re-run Analysis**."
        )
    if status == "no_overlap":
        return (
            "GSTR-1 returns have been uploaded, but none of them covers the "
            "months in this ledger, so there is nothing to compare. Upload the "
            "GSTR-1 for the ledger's months and click **Re-run Analysis**."
        )
    if status == "no_sales":
        return (
            "No sales vouchers were found in this ledger, so there is nothing to "
            "compare with a GSTR-1 return."
        )
    return None


# --- While a document is still being read --------------------------------------

def results_pending_note(active_jobs, have_results=True):
    """The notice shown above results while background work is queued or
    running. The results themselves stay on screen -- they are real results
    for documents already processed, and blanking them would hide those for
    as long as a scan takes -- so the notice must say whose results they
    are: they do not include the document still being read. With
    `have_results` False there is nothing below the notice yet, so it must
    not point at results that are not there. `active_jobs` is
    jobs.snapshot()["active"] (dicts with "kind" and "label")."""
    names = []
    for job in active_jobs or []:
        if job.get("kind") in ("upload", "ledger") and job.get("label") and job["label"] not in names:
            names.append(job["label"])
    if not names:
        if not have_results:
            return "The checks are running. Results will appear here when they finish."
        return (
            "The checks are being re-run. The results below are from the last "
            "completed run and update automatically when it finishes."
        )
    shown = ", ".join(names[:2]) + (f" and {len(names) - 2} more" if len(names) > 2 else "")
    if not have_results:
        return (
            f"Still reading **{shown}**. There is nothing to show yet; the "
            f"results will appear here when the analysis finishes."
        )
    return (
        f"Still reading **{shown}**. The results below come from the documents "
        f"already processed and do not include "
        f"{'it' if len(names) == 1 else 'them'} yet; they update automatically "
        f"when the analysis finishes."
    )


# --- What the dashboard covers, and what a result does and does not mean -----

COVERAGE_NOTE = """\
**Documents it can read** -- digital PDFs of: a Tally ledger export, a trial \
balance, Form 26AS, an ITR computation of income, an ITR-V acknowledgement, \
GSTR-1 and GSTR-3B. Scans and photos have no readable text and cannot be \
analysed; neither can other kinds of document.

**What it checks**
- *Ledger:* round amounts, possible duplicates, entries made and approved by \
the same person, the first-digit pattern of amounts (Benford's Law), missing \
voucher numbers, and statistically unusual entries.
- *Income tax:* income tax return against Form 26AS (income and tax deducted, \
for the same assessment year); trial balance against the ledger for the same \
period.
- *GST:* GSTR-1 against GSTR-3B month by month; ledger sales against the \
GSTR-1 returns you upload.

**What a result means** -- a finding is something to review, not proof of an \
error or of wrongdoing. "No findings" means only that none of the checks \
above flagged anything; it is not an assurance that the records are free of \
mistakes or tax issues. Anything outside these checks -- for example \
purchases and input tax credit, bank reconciliation or TDS returns -- is not \
examined.
"""


# --- GSTR-1 vs GSTR-3B: why a comparison did or did not happen ---------------

def gst_status_message(summary):
    """Text for a GSTR-1 vs GSTR-3B result that compared nothing, or None
    when it did. `summary` is data.gstr_summary()'s dict. Always says "not
    compared" -- a missing document is never described as a filing failure."""
    status = (summary or {}).get("status")
    if status == "no_gstr1":
        return ("Not compared: only GSTR-3B returns are on file. Upload the GSTR-1 "
                "for the same months under **Process Documents** to compare them.")
    if status == "no_gstr3b":
        return ("Not compared: only GSTR-1 returns are on file. Upload the GSTR-3B "
                "for the same months under **Process Documents** to compare them.")
    if status == "no_overlap":
        return ("Not compared: GSTR-1 and GSTR-3B have both been uploaded, but not "
                "for any of the same months, so there is nothing to compare.")
    return None


def gst_not_compared_notes(summary):
    """One line per kind of month that has only one of the two returns."""
    notes = []
    if summary.get("only_in_gstr1"):
        notes.append("Not compared -- GSTR-1 is on file but no GSTR-3B for: "
                     + ", ".join(summary["only_in_gstr1"]) + ".")
    if summary.get("only_in_gstr3b"):
        notes.append("Not compared -- GSTR-3B is on file but no GSTR-1 for: "
                     + ", ".join(summary["only_in_gstr3b"]) + ".")
    return notes

