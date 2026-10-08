"""
source_resolver.py
------------------
Content-based source-document discovery for the dashboard's upload flow.

Two jobs, both about making "upload a document and it gets analysed" true
for ANY filename:

1. A persistent classification cache (scan_documents()).
   classify_document() has to open each PDF with pdfplumber (~0.1-1.5s per
   file, ~7.8s for this project's 11 baseline documents) and the dashboard
   used to repeat that scan for every new browser session. Results are now
   kept in output/classification_cache.json, keyed by filename and
   fingerprinted by (mtime_ns, size), and mirrored in a process-wide
   in-memory dict -- so only a new or changed file is ever classified, and
   only once, no matter how many sessions or restarts follow. A missing,
   unreadable or corrupt cache file simply means "classify fresh"; it can
   never break a scan.

2. Content-based source selection (resolve_tax_sources(),
   gst_source_paths()). The tax and GST adapters used to select their
   inputs by exact filename (annual_tax_statement_redacted.pdf, gstr1_*_
   redacted.pdf, ...), so a correctly-recognised upload with any other name
   was saved but never read. Here the choice is made from what
   classify_document() found inside each file:
     - Form 26AS / ITR computation / ITR-V acknowledgement are one filing
       and must describe the SAME assessment year. The newest 26AS wins,
       then the newest computation and acknowledgement carrying that same
       assessment year are paired with it. If a matching partner is
       missing, the result says so explicitly (status "incomplete") rather
       than silently mixing two different years.
     - Trial balance: newest one wins (its own period is read from the
       header so it can be matched to the ledger covering the same dates).
     - GSTR-1 / GSTR-3B: every file of those types contributes (each may
       hold one month or a whole year).
   "Newest" means latest modification time, so a freshly uploaded document
   takes over from an older one automatically. Nothing is ever deleted.
"""
import json
import os
import re
import sys
import threading
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
RAW_PDFS_DIR = os.path.join(DATA_DIR, "raw_pdfs")
CLASSIFICATION_CACHE_PATH = os.path.join(OUTPUT_DIR, "classification_cache.json")

_CACHE_VERSION = 1
_lock = threading.RLock()
# Process-wide mirror of the on-disk cache: {cache_file_path: {name: entry}}.
# Keyed by cache path so tests (and any caller using a different cache
# file) never see each other's entries.
_memory = {}


# ---------------------------------------------------------------------------
# Persistent classification cache
# ---------------------------------------------------------------------------

def _fingerprint(path):
    st = os.stat(path)
    return [st.st_mtime_ns, st.st_size]


def _load_disk_cache(cache_path):
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == _CACHE_VERSION and isinstance(data.get("entries"), dict):
            return data["entries"]
    except Exception:
        pass
    return {}


def _save_disk_cache(cache_path, entries):
    """Atomic write (temp file + os.replace) so a crash or a second process
    can never leave a half-written cache; any failure is swallowed -- the
    cache is a convenience, never load-bearing."""
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = f"{cache_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": _CACHE_VERSION, "entries": entries}, f)
        os.replace(tmp, cache_path)
    except Exception:
        pass


def _entries_for(cache_path):
    if cache_path not in _memory:
        _memory[cache_path] = _load_disk_cache(cache_path)
    return _memory[cache_path]


def _predates_issue_field(result):
    """An "unknown" result cached before classify_document() recorded WHY
    (its "issue" field): classify it again -- there are only ever a few of
    these -- so a scanned or unreadable file gets the clear explanation
    instead of the old generic one."""
    return result.get("doctype") == "unknown" and "issue" not in result


def classify_cached(path, cache_path=None, classify=None):
    """classify_document(path), reusing a cached result when the file's
    (mtime_ns, size) fingerprint is unchanged. Returns
    {"doctype", "evidence", "error"} exactly like classify_document()."""
    cache_path = cache_path or CLASSIFICATION_CACHE_PATH
    name = os.path.basename(path)
    try:
        fp = _fingerprint(path)
    except OSError:
        fp = None
    with _lock:
        entries = _entries_for(cache_path)
        hit = entries.get(name)
        if hit is not None and fp is not None and hit.get("fingerprint") == fp \
                and not _predates_issue_field(hit["result"]):
            return dict(hit["result"])
    if classify is None:
        import classify_document as _cd
        classify = _cd.classify_document
    result = classify(path)  # slow part -- deliberately outside the lock
    with _lock:
        entries = _entries_for(cache_path)
        if fp is not None:
            entries[name] = {"fingerprint": fp, "result": dict(result)}
            _save_disk_cache(cache_path, entries)
    return dict(result)


def scan_documents(raw_dir=None, cache_path=None, on_progress=None, classify=None):
    """Classifies every PDF in raw_dir (default data/raw_pdfs/), using the
    persistent cache. Returns a list of
    {"filename", "path", "doctype", "evidence", "error"} sorted by filename
    -- the same shape dashboard/data.py's discover_documents() returns.

    `on_progress(done, total, filename)` is called after each file so a UI
    can show a real progress bar; exceptions it raises are ignored.
    Never raises for a missing directory (empty list); drops cache entries
    for files that no longer exist."""
    raw_dir = raw_dir or RAW_PDFS_DIR
    cache_path = cache_path or CLASSIFICATION_CACHE_PATH
    if not os.path.isdir(raw_dir):
        return []
    names = [
        n for n in sorted(os.listdir(raw_dir))
        if n.lower().endswith(".pdf") and os.path.isfile(os.path.join(raw_dir, n))
    ]
    results = []
    for i, name in enumerate(names):
        path = os.path.join(raw_dir, name)
        result = classify_cached(path, cache_path=cache_path, classify=classify)
        results.append({"filename": name, "path": path, **result})
        if on_progress is not None:
            try:
                on_progress(i + 1, len(names), name)
            except Exception:
                pass
    with _lock:
        entries = _entries_for(cache_path)
        stale = [n for n in entries if n not in set(names)]
        if stale:
            for n in stale:
                del entries[n]
            _save_disk_cache(cache_path, entries)
    return results


def pending_scan_count(raw_dir=None, cache_path=None):
    """How many PDFs in raw_dir are NOT yet in the (valid) cache -- i.e.
    how many a scan_documents() call would actually have to classify.
    Cheap (stat only); lets the UI tell "instant" loads from slow ones."""
    raw_dir = raw_dir or RAW_PDFS_DIR
    cache_path = cache_path or CLASSIFICATION_CACHE_PATH
    if not os.path.isdir(raw_dir):
        return 0
    pending = 0
    with _lock:
        entries = _entries_for(cache_path)
        for n in os.listdir(raw_dir):
            p = os.path.join(raw_dir, n)
            if not (n.lower().endswith(".pdf") and os.path.isfile(p)):
                continue
            hit = entries.get(n)
            try:
                fp = _fingerprint(p)
            except OSError:
                pending += 1
                continue
            if hit is None or hit.get("fingerprint") != fp:
                pending += 1
    return pending


# ---------------------------------------------------------------------------
# Period extraction (read from each document's own first page)
# ---------------------------------------------------------------------------

_AY_26AS = re.compile(r"Assessment Year\s+(\d{4}-\d{2})")
_AY_COI = re.compile(r"Asstt\.\s*Year\s*:?\s*(\d{4})\s*-\s*(\d{2,4})")
_AY_ACK = re.compile(r"Assessment[\s\S]{0,200}?Year[\s\S]{0,60}?(\d{4}-\d{2})")
_TB_PERIOD = re.compile(
    r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+to\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})"
)


def _first_page_text(pdf_path):
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text() or ""


def parse_dmy(text):
    """'1-Apr-24' / '31-Mar-2025' -> datetime, or None."""
    for fmt in ("%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def assessment_year(pdf_path, doctype):
    """The assessment year ('2025-26') a tax document belongs to, read from
    its own first page; None when it can't be found."""
    try:
        text = _first_page_text(pdf_path)
    except Exception:
        return None
    if doctype == "form_26as":
        m = _AY_26AS.search(text)
        return m.group(1) if m else None
    if doctype == "itr_computation":
        m = _AY_COI.search(text)
        if not m:
            return None
        start, end = m.group(1), m.group(2)
        return f"{start}-{end[-2:]}"
    if doctype == "itr_acknowledgement":
        m = _AY_ACK.search(text)
        return m.group(1) if m else None
    return None


def trial_balance_period(pdf_path):
    """(start_datetime, end_datetime) from a Tally trial balance's header
    line ('1-Apr-24 to 31-Mar-25'), or None."""
    try:
        text = _first_page_text(pdf_path)
    except Exception:
        return None
    m = _TB_PERIOD.search(text)
    if not m:
        return None
    start, end = parse_dmy(m.group(1)), parse_dmy(m.group(2))
    if start is None or end is None:
        return None
    return start, end


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------

def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def _newest(docs):
    """Newest first by modification time; filename breaks exact ties so the
    choice is deterministic."""
    return sorted(docs, key=lambda d: (_mtime(d["path"]), d["filename"]), reverse=True)


def resolve_tax_sources(documents):
    """Pick the ITR-vs-26AS source set from classified `documents` (the
    list scan_documents() returns).

    Returns {
      "status": "ok" | "incomplete" | "none",
      "assessment_year": "2025-26" | None,
      "form_26as": path | None, "itr_computation": path | None,
      "itr_acknowledgement": path | None,
      "filenames": {role: basename},
      "missing": [roles that could not be paired],
      "reason": human-readable explanation when not "ok",
    }
    """
    by_type = {}
    for d in documents:
        by_type.setdefault(d["doctype"], []).append(d)

    out = {
        "status": "none", "assessment_year": None,
        "form_26as": None, "itr_computation": None, "itr_acknowledgement": None,
        "filenames": {}, "missing": [], "reason": "",
    }
    as26_docs = _newest(by_type.get("form_26as", []))
    if not as26_docs:
        out["reason"] = "No Form 26AS (Annual Tax Statement) has been uploaded."
        out["missing"] = ["form_26as", "itr_computation", "itr_acknowledgement"]
        return out

    chosen = as26_docs[0]
    ay = assessment_year(chosen["path"], "form_26as")
    out["form_26as"] = chosen["path"]
    out["filenames"]["form_26as"] = chosen["filename"]
    out["assessment_year"] = ay

    for role in ("itr_computation", "itr_acknowledgement"):
        candidates = _newest(by_type.get(role, []))
        match = None
        for c in candidates:
            if ay is not None and assessment_year(c["path"], role) == ay:
                match = c
                break
        if match is not None:
            out[role] = match["path"]
            out["filenames"][role] = match["filename"]
        else:
            out["missing"].append(role)

    if out["missing"]:
        names = {
            "itr_computation": "Computation of Income",
            "itr_acknowledgement": "ITR-V acknowledgement",
        }
        wanted = " and ".join(names[r] for r in out["missing"])
        if ay is None:
            out["reason"] = (
                f"Could not read the assessment year from {chosen['filename']}, "
                f"so its {wanted} could not be matched."
            )
        else:
            out["reason"] = (
                f"Form 26AS ({chosen['filename']}) is for assessment year {ay}, "
                f"but no {wanted} for the same year has been uploaded."
            )
        out["status"] = "incomplete"
    else:
        out["status"] = "ok"
    return out


def resolve_trial_balance(documents):
    """Newest trial balance among `documents`: {"path", "filename",
    "period": (start, end) | None} or None when there isn't one."""
    docs = _newest([d for d in documents if d["doctype"] == "trial_balance"])
    if not docs:
        return None
    d = docs[0]
    return {
        "path": d["path"], "filename": d["filename"],
        "period": trial_balance_period(d["path"]),
    }


def gst_source_paths(documents):
    """({gstr1 paths}, {gstr3b paths}) -- every classified GSTR-1 / GSTR-3B
    document, oldest filename first (stable order)."""
    gstr1 = sorted(d["path"] for d in documents if d["doctype"] == "gstr1")
    gstr3b = sorted(d["path"] for d in documents if d["doctype"] == "gstr3b")
    return gstr1, gstr3b
