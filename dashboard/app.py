"""
dashboard/app.py
-----------------
Streamlit UI for the audit project. Pure presentation: every number or
table shown here comes from dashboard/data.py's loader/transform
functions, which are independently unit-tested (tests/test_dashboard_data.py)
without needing Streamlit at all. Run with:

    streamlit run dashboard/app.py

from the project root (or `cd dashboard && streamlit run app.py` -- data.py
resolves paths relative to its own file, not the current working
directory, so either works). The dark theme's base colors live in
.streamlit/config.toml; the glass-card/dashed-upload-zone styling below is
layered on top of that via custom CSS, since Streamlit's built-in theming
doesn't expose borders, blur, or per-container styling on its own.
"""
import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data as dd  # dashboard/data.py
import ingest  # dashboard/ingest.py -- uploads -> background jobs
import plain  # dashboard/plain.py -- plain-language wording (display only)
import jobs  # dashboard/jobs.py -- the background worker
import source_resolver  # scripts/source_resolver.py (scripts/ is on sys.path via data.py)

st.set_page_config(page_title="Audit Findings Dashboard", layout="wide")

# ---------------------------------------------------------------------------
# Theme: dark, professional, glass-card KPIs, dashed drop-zone for uploads.
#
# Target selectors, verified against the actual rendered DOM (Streamlit
# 1.64) before writing this, not guessed:
#   - data-testid="stAppViewContainer" / "stSidebar" / "stHeader" -- stable,
#     documented Streamlit test ids for the main layout regions.
#   - st.container(key="...") renders a stable CSS class "st-key-<key>" on
#     its wrapper div (alongside Streamlit's own hashed emotion-cache
#     classes, which are NOT stable across versions and are never targeted
#     here). Every KPI card below is given a key starting with "card_", so
#     one attribute-contains selector styles all of them.
#   - data-testid="stFileUploaderDropzone" is Streamlit's own stable id for
#     the uploader's drag-and-drop area.
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Sans:wght@600;700&family=IBM+Plex+Mono:wght@500;600&display=swap');

:root {
    --bg-primary: #0a0d12;
    --bg-secondary: #10141b;
    --border-subtle: rgba(255,255,255,0.09);
    --accent: #4f8ff7;
    --accent-soft: rgba(79,143,247,0.35);
    --text-muted: #8b94a3;
}

html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
    background-color: var(--bg-primary) !important;
    font-family: 'Inter', -apple-system, sans-serif !important;
}
[data-testid="stHeader"] { background-color: transparent !important; }
[data-testid="stSidebar"] {
    background-color: var(--bg-secondary) !important;
    border-right: 1px solid var(--border-subtle);
}
/* NOTE: deliberately no blanket `[data-testid="stSidebar"] *` font-family
   override here -- the global html/body rule above already cascades into
   the sidebar, and a wildcard override here was previously found to also
   catch data-testid="stIconMaterial" icon-ligature elements (Streamlit
   renders icons as text like "upload" or "arrow_right" in a special icon
   font; forcing a text font onto them makes the literal ligature text
   show up instead of the glyph). Verified by screenshot during review --
   this comment exists so it doesn't get re-added "for consistency" later. */

/* h1 (the page title, st.title -- there's exactly one) gets its own
   typeface, not just a bigger weight of the body font, so the masthead
   reads as a deliberate heading rather than "body text, but bigger" --
   IBM Plex Sans pairs naturally with Inter (both humanist grotesques)
   and with the IBM Plex Mono already used for the stat numbers below.
   h2/h3 (st.header/st.subheader -- section titles) stay in Inter at 600,
   one step down from the masthead but still a clear step up from the
   400-weight body text. */
h1 { font-family: 'IBM Plex Sans', 'Inter', sans-serif !important; font-weight: 700 !important; letter-spacing: -0.02em; }
h2, h3 { font-family: 'Inter', sans-serif !important; font-weight: 600 !important; letter-spacing: -0.01em; }

[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace !important;
    font-weight: 600 !important;
}
[data-testid="stMetricLabel"] {
    color: var(--text-muted) !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.72rem !important;
}

/* Glass KPI cards: any st.container(key="card_...") */
div[class*="st-key-card_"] {
    background: linear-gradient(145deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015));
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    box-shadow: 0 4px 28px rgba(0,0,0,0.4);
}

/* The one KPI that should visually lead the Overview stat row -- bigger
   number, accent-colored label and border -- so a first glance lands on
   "how many transactions need a human look" (distinct vouchers flagged)
   instead of reading all four stats as equally weighted, even though the
   caption right below them explains they're intentionally different
   units. Driven by the "card_headline_" key prefix on that one
   glass_metric() call; it still starts with "card_" so it also picks up
   the base glass-card rule above, this just layers more on top. */
div[class*="st-key-card_headline_"] {
    border-color: var(--accent-soft) !important;
}
div[class*="st-key-card_headline_"] [data-testid="stMetricValue"] {
    font-size: 2rem !important;
}
div[class*="st-key-card_headline_"] [data-testid="stMetricLabel"] {
    color: var(--accent) !important;
}

/* Status panels (clean/flagged boxes): st.container(key="status_...") */
div[class*="st-key-status_"] {
    background: rgba(255,255,255,0.025);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    padding: 0.25rem 0.5rem;
}

/* Dashed glass drop-zone for the raw-document uploader */
[data-testid="stFileUploaderDropzone"] {
    background: rgba(79,143,247,0.05) !important;
    border: 1.5px dashed var(--accent-soft) !important;
    border-radius: 12px !important;
    transition: all 0.15s ease;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--accent) !important;
    background: rgba(79,143,247,0.09) !important;
}

[data-testid="stDataFrame"] {
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    overflow: hidden;
}
/* glide-data-grid (the engine behind st.dataframe) keeps a hidden
   <textarea class="gdg-input"> positioned over whichever cell last had
   keyboard focus, for copy/paste and in-cell text editing. Our tables are
   read-only display -- nothing here is ever edited -- but that textarea
   still sits in front of the canvas, and a click landing on it focuses
   the textarea instead of reaching the canvas's own click handler, so
   the row-click-to-investigate silently does nothing. Confirmed via
   document.elementFromPoint() during testing (2026-10-02): the exact
   click that failed to open the Investigate dialog had this textarea,
   not the canvas, at that point. Disabling its pointer-events lets every
   click pass through to the grid underneath; nothing here relies on
   mouse-interacting with the textarea itself (no cell editing happens),
   so this is safe. */
[data-testid="stDataFrame"] textarea.gdg-input {
    pointer-events: none !important;
}

hr { border-color: var(--border-subtle) !important; }
[data-testid="stExpander"] {
    border: 1px solid var(--border-subtle) !important;
    border-radius: 10px !important;
    background: rgba(255,255,255,0.015);
}

/* Centered multi-stage "Scanning your document" processing card -- added
   2026-10-04 (now built by _job_card_html() below). Built as CSS on a
   single HTML string rather than several separate st.markdown()/
   st.spinner() calls, because splitting an opening and closing <div>
   across multiple Streamlit calls does not reliably nest them (each call
   is its own DOM insertion) -- true nested flexbox centering with an
   animated spinner needs the whole card in one unsafe_allow_html block. */
.proc-card-wrap {
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 55vh;
}
.proc-card { text-align: center; max-width: 440px; }
/* Compact live card for a background job (sits above the uploader). */
.job-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.7rem;
}
.proc-card-title {
    font-family: 'IBM Plex Sans', 'Inter', sans-serif;
    font-weight: 700;
    font-size: 1.15rem;
    margin-bottom: 0.3rem;
}
.proc-card-filename {
    font-family: 'IBM Plex Mono', monospace;
    color: var(--text-muted);
    font-size: 0.85rem;
    margin-bottom: 0.4rem;
    word-break: break-all;
}
.proc-card-sub { color: var(--text-muted); margin-bottom: 1.4rem; font-size: 0.9rem; }
.proc-stage-list {
    text-align: left;
    display: inline-block;
    min-width: 230px;
}
.proc-stage { padding: 0.22rem 0; font-size: 0.92rem; }
.proc-stage-done { color: #4ade80; }
.proc-stage-active { color: #e6e8eb; font-weight: 600; }
.proc-stage-pending { color: var(--text-muted); }
.mini-spinner {
    display: inline-block;
    width: 0.85em;
    height: 0.85em;
    border: 2px solid rgba(230,232,235,0.25);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: mini-spin 0.8s linear infinite;
    margin-right: 0.4em;
    vertical-align: -0.1em;
}
@keyframes mini-spin { to { transform: rotate(360deg); } }
</style>
""", unsafe_allow_html=True)


def glass_metric(col, key, label, value, help=None):
    """Renders one st.metric inside a bordered, glass-styled container
    (see the CSS above, targeting div[class*="st-key-card_"]). `key` must
    be unique on the page and should start with "card_" to pick up the
    glass styling -- or with "card_headline_" for the one stat per row
    that should visually lead (bigger number, accent border/label; see
    that CSS rule), when a row has a single number that matters most."""
    with col:
        with st.container(border=True, key=key):
            st.metric(label, value, help=help)


def severity_bar_chart(df, value_col, title):
    """A severity-colored bar chart (CRITICAL/HIGH/MEDIUM/LOW/NONE, using
    the same palette as the rest of the dashboard) with a dark background
    matching the page, built with Plotly instead of st.bar_chart because
    st.bar_chart can't color bars by category -- it would render every
    severity in the same default blue, which defeats the point of a
    severity breakdown."""
    fig = px.bar(
        df, x="severity", y=value_col, color="severity",
        category_orders={"severity": [s for s in dd.SEVERITY_ORDER if s in df["severity"].values]},
        color_discrete_map=dd.SEVERITY_COLORS,
        title=title,
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#e6e8eb",
        showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
        height=280,
    )
    fig.update_xaxes(title=None, gridcolor="rgba(255,255,255,0.06)")
    fig.update_yaxes(title=None, gridcolor="rgba(255,255,255,0.06)")
    return fig


@st.cache_data(show_spinner=False)
def cached_trial_balance_summary(_report_generated_at, report):
    """reconcile_trial_balance.reconcile() re-parses the trial balance PDF
    on every call (~0.3s) -- cheap once, but Streamlit reruns this whole
    script on every widget interaction, so it's cached. Keyed partly on
    `_report_generated_at` (a plain string, always hashable) rather than
    relying on Streamlit's hashing of the `report` dict alone, so a
    sidebar re-run (which changes generated_at) reliably invalidates it;
    leading underscore on the dict param tells Streamlit not to hash that
    argument itself."""
    return dd.trial_balance_summary(report)


def discover_documents_fast(on_progress=None):
    """Every PDF in data/raw_pdfs/, classified by CONTENT -- the one scan
    this whole script run uses.

    The slow part is classify_document() opening each PDF (~0.1-1.5s per
    file; ~7.8s for the 11 established documents, measured). It used to be
    repeated for every new browser session (the old per-file cache lived in
    st.session_state). Results are now kept on disk and in process memory
    (scripts/source_resolver.py's scan_documents()), keyed by (filename,
    mtime, size): only a new or changed file is ever classified, once, no
    matter how many sessions or server restarts follow -- a warm scan takes
    ~0.0s. An interrupted scan (the person uploading mid-load makes
    Streamlit abort and rerun the script) loses nothing either, because
    each file's result is saved the moment it is known.

    `on_progress(done, total, filename)` drives the loading banner."""
    return source_resolver.scan_documents(raw_dir=dd.RAW_PDFS_DIR, on_progress=on_progress)


@st.cache_data(show_spinner=False)
def cached_segmentation_summary():
    """analyze_benford_segmentation.py's segmentation analysis is pure CSV
    math (no PDF parsing) but still re-reads and re-processes all 14,625
    ledger rows -- cheap, but still worth caching across reruns within a
    session. Uses the TTL-free default cache; the ledger itself only
    changes when someone re-runs the main pipeline with new data, which is
    infrequent enough that a manual page refresh (clearing Streamlit's
    cache via the hamburger menu) is an acceptable way to pick that up."""
    seg_report = dd.run_segmentation_pipeline()
    return dd.benford_segmentation_summary(seg_report)


# Nigrini's 4 published MAD conformity bands (see
# scripts/rule_benfords_law.py's classify_mad_conformity -- these are the
# literal strings it returns, not labels invented here), colored on the
# same green/amber/red scale as SEVERITY_COLORS so "good vs. needs a
# look" reads at a glance without having to parse the label text.
MAD_CONFORMITY_COLOR = {
    "Close conformity": "#4ade80",
    "Acceptable conformity": "#4ade80",
    "Marginally acceptable": "#fbbf24",
    "Nonconformity": "#f87171",
}

# How to phrase each MAD band in the Interpretation sentence below without
# implying it's an overall verdict on the data (see that sentence's
# comment) -- these describe MAD's OWN statistic relative to ITS OWN
# threshold, nothing broader.
MAD_THRESHOLD_PHRASE = {
    "Close conformity": "comfortably within its own threshold",
    "Acceptable conformity": "within its own threshold",
    "Marginally acceptable": "right at the edge of its own threshold",
    "Nonconformity": "outside its own threshold",
}


def fmt_rs(amount):
    """Rs.12,34,567.00-style formatting isn't what this does (that needs
    real lakh/crore grouping, which Python's , separator doesn't produce)
    -- this is plain Rs.1,234,567.00 international grouping, kept
    consistent with every other Rs. figure already on this dashboard
    (tax_reconciliation_summary's caption, etc.), not a new convention."""
    return f"Rs.{amount:,.2f}"


def verdict_line(ok, label):
    """A single-line colored pass/fail verdict (green check / red cross),
    styled to match the Benford chi-square/MAD cards -- added 2026-10-02
    so every reconciliation verdict on the dashboard (Overview's three
    status boxes, both Tax & Compliance cards, GSTR Status) reads the
    same way, instead of some tabs using a full-width st.success/st.error
    banner (a lot of colored padding for one line of text) and others a
    slim colored line. Pass/fail only -- informational st.info boxes
    (the Benford interpretation note, GSTR's ledger-overlap caveat) are a
    different kind of message and are left as st.info."""
    color = "#4ade80" if ok else "#f87171"
    mark = "✓" if ok else "✗"
    st.markdown(
        f"<span style='color:{color}; font-weight:600'>{mark} {label}</span>",
        unsafe_allow_html=True,
    )


def render_voucher_investigation(detail):
    """Renders the full investigation view for one voucher inside the
    Investigate dialog -- the transaction's complete set of legs (so both
    the Dr and Cr side are visible, not just whichever leg was flagged),
    an evidence checklist per flagged leg, and -- only when a
    duplicate_transaction signal is present -- the other voucher(s) it
    matches, shown the same way. `detail` is a dashboard.data.
    voucher_detail() result; the caller has already checked it's not
    None."""
    legs_df = pd.DataFrame([
        {
            "Line": leg["leg_num"],
            "Account": leg["account"],
            "Dr/Cr": leg.get("dr_cr") or leg.get("entry_type"),
            "Amount": float(leg["amount"]),
            "Voucher type": leg.get("vch_type"),
            "Voucher no.": leg.get("vch_no"),
            "Narration": leg.get("narration") or "",
        }
        for leg in detail["legs"]
    ])
    st.dataframe(
        legs_df, width="stretch", hide_index=True,
        column_config={"Amount": st.column_config.NumberColumn(format="Rs.%,.2f")},
    )

    st.markdown("**Why this was flagged**")
    for fl in detail["flagged_legs"]:
        leg_num = fl["raw_row"]["leg_num"] if fl["raw_row"] else "?"
        st.markdown(f"_Line {leg_num} -- {fl['flag']['account']}_")
        for item in fl["evidence"]:
            st.markdown(f"✓ {plain.plain_evidence(item)}")

    if detail["related_entry_ids"]:
        st.divider()
        st.markdown(
            f"**Matching / related transactions:** "
            f"{', '.join(detail['related_entry_ids'])}"
        )
        related_df = pd.DataFrame([
            {
                "Voucher": leg["entry_id"],
                "Line": leg["leg_num"],
                "Account": leg["account"],
                "Dr/Cr": leg.get("dr_cr") or leg.get("entry_type"),
                "Amount": float(leg["amount"]),
                "Date": leg.get("date"),
            }
            for leg in detail["related_legs"]
        ])
        st.dataframe(
            related_df, width="stretch", hide_index=True,
            column_config={"Amount": st.column_config.NumberColumn(format="Rs.%,.2f")},
        )


def investigate_table(df, report, key, column_config=None, ledger_path=None):
    """Renders `df` (priority_findings() or ledger_flags_dataframe()'s
    filtered view -- either way, a DataFrame with an `entry_id` column) as
    a selectable table, and shows the full investigation for whichever
    voucher the person clicks in an inline panel directly below the
    table. `column_config` lets a caller set per-column display widths
    without touching the shared logic below.

    Uses selection_mode="single-cell" rather than Streamlit's own
    "single-row" mode -- NOT a stylistic choice. Verified empirically
    (2026-10-02, headless Playwright click-mapping across both modes)
    that "single-row" only registers a click on the ~15px row-selector
    checkbox glyph Streamlit draws in the first column; clicking any
    actual data cell does nothing in that mode. "single-cell" responds to
    a click on ANY cell in the row and reports it as
    `event.selection.cells == [[row_index, column_name]]`, so the row
    index is always `cells[0][0]` regardless of which column was clicked
    -- this is what actually delivers "click anywhere in the row."

    The investigation itself is rendered INLINE (a bordered container
    right after the table), not in an st.dialog popup -- this was tried
    first and dropped after it turned out to be the actual source of the
    unreliable clicks the investigation was supposed to fix. Verified
    empirically (2026-10-02, headless Playwright, 20+ open/close/re-click
    cycles): with st.dialog, the FIRST click on the table after closing
    ANY dialog is reliably swallowed (0/1) and only the second click
    (same spot) registers -- reproduced with the dialog's own native
    close control, so it isn't about this app's code, and ruled out as a
    timing issue (persisted at waits up to 8s). A plain st.button rerun
    with no dialog involved never showed this (6/6).

    What actually mattered turned out to be narrower than "dialog vs.
    inline": the SAME alternating failure came straight back, inline
    panel and all, the moment "Close" tried to programmatically reset
    THIS dataframe's own selection state (first by remounting it under a
    new key, then by writing an empty selection into
    st.session_state[key] on the next run before the widget re-renders)
    -- every click right after one of those resets was swallowed, every
    click after that worked, alternating indefinitely. Forcing the
    grid's selection back to empty from code, by any method tried, is
    what breaks the NEXT real click -- not the modal. So "Close" here
    does NOT touch st.session_state[key] or the widget's selection at
    all; it only clears this function's own `panel_key`, which is what
    the panel actually renders from. The dataframe's last-clicked cell
    stays selected underneath (invisible once the panel is hidden), with
    one narrow, honest consequence: re-clicking the EXACT same row right
    after closing its own panel toggles that cell off rather than
    re-opening it (since nothing about the selection actually changed),
    needing one extra click to reselect it. Clicking any OTHER row,
    which is the realistic next action, works first-click every time --
    confirmed 8/8 across repeated open-different-row/close cycles, vs.
    the ~50% either previous approach produced.

    `ledger_path` (added 2026-10-04) is passed straight through to
    dd.voucher_detail() -- None keeps that function's own default
    (FY2024-25's GENERAL_LEDGER_PATH), so every existing call site here
    is unaffected. The new FY2025-26 tab passes dd.FY2025_26_GL_PATH so
    its own Investigate drill-down joins against the right financial
    year's ledger rows instead of the wrong one (entry_ids aren't unique
    across the two ledgers -- see voucher_detail()'s own docstring)."""
    panel_key = f"{key}_selected_entry"
    if panel_key not in st.session_state:
        st.session_state[panel_key] = None

    # "Investigate" goes FIRST (not trailing) and pinned, so the action
    # cue is visible without any horizontal scroll regardless of how many
    # columns a given table has -- added 2026-10-02 after screenshots
    # showed it getting clipped off the right edge on wider tables.
    display_df = df.copy()
    display_df.insert(0, "Investigate", "→ Investigate")

    base_config = {
        c: st.column_config.NumberColumn(format="Rs.%,.2f")
        for c in display_df.columns if c == "amount" or c == "Amount"
    }
    if column_config:
        base_config.update(column_config)
    base_config["Investigate"] = st.column_config.TextColumn(
        label="", width=115, pinned=True
    )

    event = st.dataframe(
        display_df, width="stretch", hide_index=True,
        on_select="rerun", selection_mode="single-cell",
        key=key,
        column_config=base_config,
    )
    if event.selection.cells:
        row_idx = event.selection.cells[0][0]
        entry_id_col = "entry_id" if "entry_id" in df.columns else "Voucher"
        st.session_state[panel_key] = df.iloc[row_idx][entry_id_col]

    entry_id = st.session_state[panel_key]
    if entry_id is not None:
        with st.container(border=True, key=f"card_investigate_{key}"):
            header_col, close_col = st.columns([6, 1])
            with header_col:
                st.markdown(f"**Investigating: {entry_id}**")
            with close_col:
                if st.button("✕ Close", key=f"{key}_close"):
                    st.session_state[panel_key] = None
                    st.rerun()
            detail = dd.voucher_detail(
                report, entry_id,
                **({"ledger_path": ledger_path} if ledger_path else {}),
            )
            if detail is None:
                st.caption("No ledger rows found for this voucher.")
            else:
                render_voucher_investigation(detail)


def render_generic_ledger_tab(info):
    """Renders one ledger document's intrinsic-checks tab (KPIs, priority
    findings, severity breakdown, full ledger-flags table, Benford's Law,
    voucher-number gaps, ML anomaly detection) -- the SAME content the
    hand-written "FY2025-26 Ledger" tab used to show, now parameterized by
    `info` (a dashboard.data.ledger_tab_info() result) instead of being
    written out once for that one specific document.

    Built 2026-10-04 so the dashboard's ledger tabs come from WHATEVER
    ledger-shaped documents are actually in data/raw_pdfs/ (see
    dd.discover_ledger_documents()/dd.ledger_tab_info()), not from a
    hardcoded tab per filename -- this is the direct fix for "why have u
    written it on dashboard itself ledger 2025 2026... that shud work
    only if i upload the documents".

    The confidence banner at the top is the OTHER explicit requirement
    this exists to satisfy: "if reconstruction produces a high unresolved
    -line count or many unbalanced vouchers on a new document, surface
    that prominently... rather than quietly showing numbers with the same
    confidence as the fully-verified ones". A document scoring "low"
    (dd.assess_reconstruction_confidence()'s verdict, carried through
    process_ledger_document()/ledger_tab_info()) gets a loud st.error
    listing the SPECIFIC reasons, placed before any number from this
    document is shown -- not a generic "proceed with caution" note a
    reader could skim past.

    Wrapped entirely in try/except by its caller (the Audit ledger-picker
    block, dashboard/app.py's top-level script) so a document whose
    numbers are merely unusual, rather than outright broken, can never
    crash the whole page -- this function itself still lets a genuinely
    unexpected error surface (it doesn't swallow exceptions), since
    that's the caller's job, not this renderer's."""
    key_prefix = f"ledger_{info['slug']}"
    label = info["label"]
    confidence = info.get("confidence", "high")
    confidence_reasons = info.get("confidence_reasons") or []

    st.subheader(f"{label} ledger -- audit results")
    # "Current audit" line (2026-10-04, direct feedback): the result page
    # itself had no freshness context of its own -- only the sidebar did,
    # and only for the fixed baseline pipeline, not per-document. No
    # fabricated "company" field here (nothing in this project extracts
    # one from any document -- it's a stopword in reconstruct_ledger_
    # entries.py, not a parsed field; inventing one would be worse than
    # not having it). Just what's actually known: which document this is
    # and when its own analysis was actually produced. Every document
    # reaching this renderer via Audit's picker now has a real
    # `processed_at` (Addendum 12 excludes the one case -- the 2
    # established baselines -- where it would be None).
    _processed_at = info.get("processed_at")
    st.caption(
        f"📊 Showing: **{label}** -- document read on {plain.friendly_time(_processed_at)}" if _processed_at
        else f"📊 Showing: **{label}**"
    )
    # The ITR-vs-26AS/Trial-Balance half of this caption is conditional
    # (added 2026-10-04, same day this function was promoted to the
    # primary "Audit" tab and the frozen FY2024-25 baseline started being
    # rendered through it too): that data genuinely IS available for the
    # frozen baseline specifically -- it's just shown one tab over, under
    # Tax & Compliance, not duplicated here -- so the old blanket
    # "not shown here... out of scope" wording would have been factually
    # wrong for that one document the moment it started flowing through
    # this same renderer. Every OTHER ledger (FY2025-26, anything newly
    # uploaded) keeps the original wording: for those, it's a genuine,
    # documented gap, not just "shown elsewhere".
    if info.get("slug") == dd.FROZEN_BASELINE_SLUG:
        _tax_tb_note = (
            "The income-tax return vs Form 26AS and trial balance checks for "
            "this ledger are in the **Tax & Compliance** tab."
        )
    else:
        _tax_tb_note = (
            "The income-tax return vs Form 26AS and trial balance checks are "
            "in the **Tax & Compliance** tab; a trial balance is compared "
            "with the ledger covering the same dates."
        )
    st.caption(
        "What was checked: whether one person entered and approved the same "
        "entry, round amounts, possible duplicate entries, whether the digits "
        "of the amounts look natural (Benford's Law), missing voucher numbers, "
        "and entries that look unlike the rest. " + _tax_tb_note
    )

    if confidence == "low":
        st.error(
            f"⚠ **This document's layout was only partly recognised -- "
            f"results below may be less reliable.** {label} scored low "
            f"confidence when its entries were read:"
        )
        for reason in confidence_reasons:
            st.markdown(f"- {reason}")
        st.caption(
            "This isn't a verdict on the document's contents. It means the "
            "program that reads entries out of the PDF didn't fully recognise "
            "this document's layout, so the numbers below are less certain "
            "than for ledgers it knows well. Worth a manual look before "
            "relying on them."
        )

    report = dd.load_combined_report(info["report_path"])
    if report is None:
        st.warning("No results found for this document yet -- try processing it again.")
        return

    kpis = dd.kpi_summary(report)
    row_count = dd.ledger_flag_row_count(report)
    voucher_count = dd.distinct_voucher_count(report)

    if kpis["generated_at"]:
        st.caption(f"Results updated {plain.friendly_time(kpis['generated_at'])}")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.25])
    glass_metric(c1, f"card_{key_prefix}_total_legs", "Ledger lines checked", f"{kpis['total_legs']:,}")
    glass_metric(c2, f"card_{key_prefix}_flag_rows", "Findings", f"{row_count:,}")
    glass_metric(c3, f"card_{key_prefix}_legs_flagged", "Lines with a finding", f"{kpis['total_legs_flagged']:,}")
    glass_metric(
        c4, f"card_{key_prefix}_headline_vouchers_flagged", "Vouchers with a finding", f"{voucher_count:,}",
    )

    st.subheader("What to look at first")
    st.caption("The ten most important findings. Click a row to see the full voucher.")
    priority_df = dd.priority_findings(report, limit=10)
    if priority_df.empty:
        st.caption("No findings to show yet.")
    else:
        priority_df = priority_df.assign(
            checks=priority_df["checks"].map(plain.plain_check_names),
            why=priority_df["why"].map(plain.plain_why),
        )
        priority_display = priority_df.drop(columns=["leg_count"]).rename(columns={
            "entry_id": "Voucher", "severity": "Severity", "accounts": "Account(s)",
            "amount": "Amount", "date": "Date", "checks": "Checks", "why": "Why it was flagged",
        })
        investigate_table(
            priority_display, report, key=f"{key_prefix}_priority_table",
            ledger_path=info["output_csv"],
            column_config={
                "Voucher": st.column_config.TextColumn(width="small"),
                "Severity": st.column_config.TextColumn(width="small"),
                "Account(s)": st.column_config.TextColumn(width=210),
                "Date": st.column_config.TextColumn(width="small"),
                "Checks": st.column_config.TextColumn(width=140),
                "Why it was flagged": st.column_config.TextColumn(width=300),
            },
        )
        concentration_note = dd.priority_findings_concentration_note(priority_df)
        if concentration_note:
            st.caption(concentration_note)

    st.subheader("How serious are the findings?")
    leg_sev_df = dd.severity_breakdown_df(report)
    voucher_sev_df = dd.voucher_severity_breakdown_df(report)
    col_leg, col_voucher = st.columns(2)
    with col_leg:
        if not leg_sev_df.empty:
            st.plotly_chart(
                severity_bar_chart(leg_sev_df, "count", f"By ledger line (n={kpis['total_legs_flagged']:,})"),
                width="stretch", config={"displayModeBar": False},
            )
        else:
            st.caption("Nothing to show yet.")
    with col_voucher:
        if not voucher_sev_df.empty:
            st.plotly_chart(
                severity_bar_chart(voucher_sev_df, "voucher_count", f"By voucher (n={voucher_count:,})"),
                width="stretch", config={"displayModeBar": False},
            )
        else:
            st.caption("Nothing to show yet.")

    st.subheader("All findings")
    ledger_df = dd.ledger_flags_dataframe(report)
    if ledger_df.empty:
        st.caption("No findings to show.")
    else:
        severities_present = [s for s in dd.SEVERITY_ORDER if s in ledger_df["severity"].unique()]
        checks_present = sorted(ledger_df["rule_display"].unique(), key=plain.check_label)
        sev_filter = st.multiselect(
            "Severity", severities_present, default=severities_present, key=f"{key_prefix}_sev_filter",
        )
        check_filter = st.multiselect(
            "Check", checks_present, default=checks_present, key=f"{key_prefix}_check_filter",
            format_func=plain.check_label,
        )
        filtered = ledger_df[
            ledger_df["severity"].isin(sev_filter)
            & ledger_df["rule_display"].isin(check_filter)
        ].sort_values(["severity", "signal_count"], ascending=False, kind="stable")
        ledger_display = filtered.drop(
            columns=["date_parsed", "rule", "reason_severity"]
        ).rename(columns={
            "leg_key": "Line", "entry_id": "Voucher", "date": "Date", "account": "Account",
            "amount": "Amount", "rule_display": "Check", "reason": "Reason",
            "signal_count": "Checks flagging this line", "severity": "Severity",
        })
        ledger_display["Line"] = (
            "Line " + ledger_display["Line"].str.extract(r"\(leg (\d+)\)$", expand=False)
        ).fillna(ledger_display["Line"])
        ledger_display["Check"] = ledger_display["Check"].map(plain.check_label)
        ledger_display["Reason"] = ledger_display["Reason"].map(plain.plain_reason)
        investigate_table(
            ledger_display, report, key=f"{key_prefix}_ledger_flags_table",
            ledger_path=info["output_csv"],
            column_config={
                "Line": st.column_config.TextColumn(width="small"),
                "Voucher": st.column_config.TextColumn(width=150),
                "Date": st.column_config.TextColumn(width="small"),
                "Account": st.column_config.TextColumn(width=180),
                "Check": st.column_config.TextColumn(width=150),
                "Reason": st.column_config.TextColumn(width=320),
                "Checks flagging this line": st.column_config.TextColumn(width="small"),
                "Severity": st.column_config.TextColumn(width="small"),
            },
        )
        st.caption(f"Showing {len(filtered):,} of {len(ledger_df):,} findings.")

    st.subheader("Digit pattern check (Benford's Law)")
    benford = dd.benford_summary(report)
    if benford is None or benford["chi_square"] is None:
        st.caption("Not generated yet.")
    else:
        st.caption(
            "In genuine accounting data, about 30% of amounts begin with the digit 1 "
            "and far fewer with 9. Large departures from this can indicate rounded, "
            "repeated or artificially chosen amounts, although fixed prices can "
            "produce the same effect."
        )
        _headline = plain.benford_headline(benford)
        if _headline:
            st.info(_headline)
        col_chi, col_mad = st.columns(2)
        with col_chi:
            with st.container(border=True, key=f"card_{key_prefix}_chi_square"):
                st.markdown("**Strict test**")
                _color = "#f87171" if benford["deviates"] else "#4ade80"
                _label = "Differs from the expected pattern" if benford["deviates"] else "Matches the expected pattern"
                st.markdown(
                    f"<span style='color:{_color}; font-weight:600; font-size:1.05rem'>{_label}</span>",
                    unsafe_allow_html=True,
                )
                st.caption("Highly sensitive: large ledgers often fail it on small differences.")
        with col_mad:
            with st.container(border=True, key=f"card_{key_prefix}_mad"):
                st.markdown("**Practical test (used for the overall result)**")
                if benford["mad"] is not None and benford["mad_conformity"] is not None:
                    _mad_color = MAD_CONFORMITY_COLOR.get(benford["mad_conformity"], "#94a3b8")
                    st.markdown(
                        f"<span style='color:{_mad_color}; font-weight:600; font-size:1.05rem'>"
                        f"{plain.mad_plain(benford['mad_conformity'])}</span>",
                        unsafe_allow_html=True,
                    )
                    st.caption("Measures how far the digits are from the expected pattern overall.")
                else:
                    st.caption("N/A")
        with st.expander("Technical details"):
            st.caption(
                f"Chi-square = {benford['chi_square']:.2f} (limit {benford['threshold']}). "
                + (
                    f"MAD (Nigrini) = {benford['mad']:.5f}, band: {benford['mad_conformity']}."
                    if benford["mad"] is not None and benford["mad_conformity"] is not None
                    else "MAD not available."
                )
            )
            st.caption(
                "A deeper breakdown by voucher type and account exists only for the "
                "FY2024-25 reference ledger; for this ledger the two tests above "
                "are what is available."
            )

    st.subheader("Missing voucher numbers")
    gap_df = dd.voucher_gap_dataframe(report)
    if gap_df.empty:
        verdict_line(True, "No voucher numbers are missing in any series.")
    else:
        total_missing = int(gap_df["missing_count"].sum())
        verdict_line(False, f"{total_missing} voucher number(s) are missing across {len(gap_df)} voucher series.")
        st.dataframe(plain.rename_columns(gap_df, plain.GAP_COLUMNS), width="stretch", hide_index=True)
    st.caption(
        "A missing number is not proof that something was deleted or hidden: "
        "cancelled or voided vouchers also leave gaps, and Tally can reuse "
        "number series across voucher types. Larger gaps are worth "
        "following up."
    )

    st.subheader("Unusual entries (found by pattern analysis)")
    ml_summary = dd.ml_anomaly_summary(report)
    if ml_summary["total_flagged"] == 0:
        st.caption("No unusual entries found.")
    else:
        _sev = ", ".join(f"{k.title()}: {v}" for k, v in ml_summary["by_severity"].items())
        st.caption(
            f"A statistical model compared every entry with the rest of the ledger "
            f"(amount, account, date, Dr/Cr) and picked out {ml_summary['total_flagged']:,} "
            f"that differ most from the rest ({_sev}). These entries are worth "
            f"reviewing; being unusual does not by itself indicate an error."
        )
        _ml_sorted = ml_summary["flags_df"].sort_values("anomaly_rank_pct")
        st.dataframe(
            plain.rename_columns(_ml_sorted.drop(columns=["anomaly_score"], errors="ignore"), plain.ML_COLUMNS),
            width="stretch", hide_index=True,
        )
        with st.expander("Technical details"):
            st.caption(
                "Method: Isolation Forest. 'Unusualness' is the entry's rank among "
                "all entries (smaller = more unusual); the raw score is below."
            )
            st.dataframe(_ml_sorted, width="stretch", hide_index=True)
    st.caption(
        "The model does not know who entered an entry or when -- that "
        "information is not in Tally exports -- so it cannot catch problems "
        "that depend on it."
    )


_PROCESSING_STAGES = [
    ("uploaded", "Document uploaded"),
    ("scanning", "Scanning document"),
    ("extracting", "Extracting data"),
    ("auditing", "Running audit checks"),
    ("preparing", "Preparing results"),
]

# How long a finished job's result stays on screen (unless dismissed).
_RESULT_TTL_SECONDS = 600


def _job_card_html(title, filename, sub, stage_items):
    """One job's live card as a single HTML string -- deliberately one
    st.markdown() call, not several (a multi-call open/close <div>
    sequence does not reliably nest in Streamlit, each call being its own
    DOM insertion). `stage_items` is a list of (state, label), state one of
    "done" / "active" / "pending". No fake percentage anywhere -- only real,
    named stages."""
    import html as _html
    rows = []
    for state, label in stage_items:
        label = _html.escape(label)
        if state == "done":
            rows.append(f'<div class="proc-stage proc-stage-done">✓ {label}</div>')
        elif state == "active":
            rows.append(
                f'<div class="proc-stage proc-stage-active">'
                f'<span class="mini-spinner"></span>{label}</div>'
            )
        else:
            rows.append(f'<div class="proc-stage proc-stage-pending">○ {label}</div>')
    return (
        '<div class="job-card">'
        f'<div class="proc-card-title">{_html.escape(title)}</div>'
        f'<div class="proc-card-filename">{_html.escape(filename)}</div>'
        f'<div class="proc-card-sub">{_html.escape(sub)}</div>'
        f'<div class="proc-stage-list">{"".join(rows)}</div>'
        '</div>'
    )


def _render_active_job(job, position):
    """Live card for one queued/running job. Ledger-style jobs ("upload"
    and "ledger") show the five real processing stages; an analysis job
    shows its own checks."""
    if job["status"] == "queued":
        st.markdown(
            _job_card_html(
                "Queued", job["label"],
                f"Waiting for {position} earlier job(s) to finish -- it starts "
                "automatically.",
                [("pending", "Waiting in line")],
            ),
            unsafe_allow_html=True,
        )
        return
    detail = job.get("detail")
    if job["kind"] == "analysis":
        total = detail[2] if isinstance(detail, tuple) and len(detail) == 3 else len(dd._ANALYSIS_STEPS)
        idx = detail[1] if isinstance(detail, tuple) and len(detail) == 3 else 1
        items = []
        for i, (name, _fn) in enumerate(dd._ANALYSIS_STEPS, start=1):
            items.append(("done" if i < idx else "active" if i == idx else "pending", name))
        st.markdown(
            _job_card_html(
                "Running analysis", "All documents on file",
                "Re-checking every document against the checks that apply to it...",
                items,
            ),
            unsafe_allow_html=True,
        )
        return
    order = [key for key, _ in _PROCESSING_STAGES]
    current = detail if isinstance(detail, str) and detail in order else "scanning"
    current_idx = order.index(current)
    items = []
    for i, (_key, label) in enumerate(_PROCESSING_STAGES):
        items.append(("done" if i < current_idx else "active" if i == current_idx else "pending", label))
    st.markdown(
        _job_card_html(
            "Scanning your document", job["label"],
            "Reading and analyzing your document -- you can keep using the "
            "dashboard; this continues in the background.",
            items,
        ),
        unsafe_allow_html=True,
    )


def _render_analysis_outcome(result):
    """The Re-run Analysis outcome: what refreshed, and -- in plain
    language -- anything that failed. A missing optional package is shown as
    the setup problem it is, never as an audit finding; the raw exception
    text sits in a collapsed "Technical details" expander."""
    if result["ran"]:
        st.success("✅ Updated: " + ", ".join(plain.step_label(r["name"]) for r in result["ran"]))
    for _f in result["failed"]:
        if _f["is_dependency_error"]:
            st.error(
                f"⚠️ Setup issue, not an audit finding -- **{plain.step_label(_f['name'])}** "
                f"needs a Python package that isn't installed in this "
                f"environment. Run `pip install -r requirements.txt` "
                f"(see the project README) and try again."
            )
        else:
            st.error(f"**{plain.step_label(_f['name'])}** could not be completed.")
        with st.expander("Technical details"):
            st.code(_f["error"], language=None)
    if not result["ran"] and not result["failed"]:
        st.info(
            "Nothing to update yet -- no document that needs these checks "
            "was found. Upload documents under **Process Documents** to get started."
        )


def _render_finished_job(job):
    """Result line(s) for one finished job, with a Dismiss button."""
    result = job.get("result") or {}
    with st.container(border=True, key=f"card_jobresult_{job['id']}"):
        if job["status"] == "failed":
            st.error(f"**{job['label']}** -- could not be processed: {job['error']}")
        elif job["kind"] == "analysis":
            _render_analysis_outcome(result)
        elif result.get("kind") == "unrecognized":
            st.warning(f"**{job['label']}** -- {result['error']}")
        elif result.get("kind") == "routed":
            st.success(
                f"✅ **{job['label']}** -- recognised as "
                f"**{dd.DOCTYPE_DISPLAY_NAMES.get(result['doctype'], result['doctype'])}**; "
                f"its results are in **Tax & Compliance**."
            )
        elif result.get("ok"):
            st.success(
                f"✅ **{result['filename']}** -- processed -- see "
                f"**\"{result['label']}\"** in **Audit**."
            )
            _conf = result.get("confidence", "high")
            _vcount = result.get("total_vouchers", 0)
            st.markdown(
                f"- ✓ Recognised as a ledger (Tally export)\n"
                f"- ✓ Read {_vcount:,} voucher{'s' if _vcount != 1 else ''}; "
                f"{result.get('unbalanced_count', 0)} don't balance (debits ≠ credits)\n"
                f"- {'✓' if _conf == 'high' else '⚠'} Reliability of the reading -- {_conf}\n"
                f"- ✓ Audit checks -- done"
            )
        else:
            st.error(
                f"**{result.get('filename', job['label'])}** -- could not be "
                f"processed: {result.get('error', 'unknown error')}"
            )
        if st.button("Dismiss", key=f"_dismiss_job_{job['id']}"):
            st.session_state.setdefault("_dismissed_jobs", set()).add(job["id"])
            st.rerun()


def _jobs_panel_body():
    """What is happening in the background right now, plus recent results.
    Runs as a polling fragment while anything is active (see
    render_jobs_panel()), so the cards advance live without re-running the
    whole page; the moment a job finishes it triggers ONE full rerun so
    every tab shows the new data."""
    import time as _time
    snap = jobs.snapshot()
    seen = st.session_state.get("_jobs_seq_seen")
    if seen is None:
        st.session_state["_jobs_seq_seen"] = snap["seq"]
    elif snap["seq"] != seen:
        st.session_state["_jobs_seq_seen"] = snap["seq"]
        st.cache_data.clear()
        done_now = [j for j in snap["finished"] if j["finished_at"] and _time.time() - j["finished_at"] < 5]
        _msgs = st.session_state.setdefault("_pending_toasts", [])
        for j in done_now[:3]:
            if j["status"] == "failed" or not (j.get("result") or {}).get("ok", True):
                _msgs.append(f"⚠️ {j['label']} could not be fully processed")
            elif j["kind"] == "analysis":
                _msgs.append("✅ Analysis refreshed -- Tax & Compliance is up to date")
            else:
                _msgs.append(f"✅ {j['label']} is ready")
        st.rerun()

    for pos, job in enumerate(snap["active"]):
        _render_active_job(job, pos)

    dismissed = st.session_state.get("_dismissed_jobs", set())
    shown = 0
    for job in snap["finished"]:
        if job["id"] in dismissed or shown >= 4:
            continue
        if job["finished_at"] and _time.time() - job["finished_at"] > _RESULT_TTL_SECONDS:
            continue
        _render_finished_job(job)
        shown += 1


def render_jobs_panel():
    """Top of the Process tab: live cards for queued/running work and the
    recent results. Polls every 1.5s only while a job is active."""
    active = jobs.is_active()
    st.fragment(_jobs_panel_body, run_every=1.5 if active else None)()


def render_upload_section():
    """The upload control, at the TOP of the Process tab and rendered
    before the document scan, so it is on screen immediately and usable
    while the rest of the page is still loading.

    An uploaded file is saved to disk the moment this runs and handed to
    the background worker (ingest.submit_upload()) -- which classifies it
    by CONTENT and runs the matching analysis: a ledger is processed (and
    appears in Audit), a Form 26AS / ITR document / trial balance / GSTR
    return triggers the Tax & Compliance analysis, anything else is reported
    as not recognised. Nothing here waits for the document scan, and a
    rerun at any moment (the person clicking a tab, another upload) cannot
    cancel it: the work lives in the server process (dashboard/jobs.py),
    not in this script run. That is the answer to "what if they upload
    before the dashboard has loaded": the file is saved and queued at once,
    and processing starts without waiting for the page.

    A file whose name belongs to one of the project's established documents
    is never overwritten (dd.save_upload()): it is kept under a new name and
    analysed as its own document, so uploading a copy of an established
    ledger yields results in Audit instead of silently replacing the
    baseline. Each uploaded file is handled once, tracked by Streamlit's own
    file_id (name+size would wrongly ignore removing a file and adding it
    again).

    Messages about what happened are stashed in session_state and shown on
    the next run, because one shown right before st.rerun() would be gone
    before anyone could read it."""
    _upload_msgs = st.session_state.pop("_upload_last_results", None)
    if _upload_msgs:
        for kind, name, detail in _upload_msgs:
            if kind == "replaced":
                st.success(f"✓ {name} replaced -- re-analysing it now (see below).")
            elif kind == "saved":
                st.success(f"✓ {name} received -- analysing it now (see below).")
            elif kind == "saved_alongside":
                st.success(
                    f"✓ {detail} has the same filename as one of this project's "
                    f"established documents, which is never overwritten -- saved "
                    f"as **{name}** and analysing it as its own document."
                )
            else:
                st.error(detail)

    st.markdown("#### Add source documents")
    st.caption(
        "Drop one or more PDFs below. Each is recognised by its content, "
        "not its filename, and analysed automatically: a ledger appears "
        "under **Audit**; a Form 26AS, ITR document, trial balance or GST "
        "return appears under **Tax & Compliance**. Use the digital PDF -- a "
        "scan or photo has no readable text and cannot be analysed."
    )
    if _BOOT_PENDING:
        st.caption(
            "The dashboard is still reading your existing documents -- you "
            "can upload right now: the file is saved immediately and "
            "analysed in the background, without waiting for the page."
        )
    uploaded_files = st.file_uploader(
        "Drop PDF files here, or browse to select one or more",
        type=["pdf"], accept_multiple_files=True, key="raw_pdf_uploader",
        help="PDF only, up to 200MB per file, multiple files supported.",
    )
    if "processed_uploads" not in st.session_state:
        st.session_state.processed_uploads = set()
    results = []
    if uploaded_files:
        for uf in uploaded_files:
            upload_id = getattr(uf, "file_id", None) or f"{uf.name}:{uf.size}"
            if upload_id in st.session_state.processed_uploads:
                continue
            try:
                saved_path, outcome = dd.save_upload(uf.name, uf.getvalue())
                st.session_state.processed_uploads.add(upload_id)
                ingest.submit_upload(saved_path)
                results.append((outcome, os.path.basename(saved_path), uf.name))
            except ValueError as e:
                st.session_state.processed_uploads.add(upload_id)
                results.append(("error", uf.name, str(e)))
    if results:
        st.session_state["_upload_last_results"] = results
        st.rerun()
    st.divider()


def render_document_library(all_documents):
    """The "upload a document, get it tested" landing surface: a document
    classified as a ledger (scripts/classify_document.py, by CONTENT, not
    filename) gets a **Process this document** button, one click away.

    Originally this showed every document as a full card with its
    classification evidence spelled out, all the time. Simplified
    2026-10-04 after direct feedback ("who wants to see the dataset we've
    taken -- in the end results matter, I think it's unnecessary"):
    anything that doesn't need a decision right now (already processed,
    one of the two established baselines, not ledger-shaped at all) is
    now collapsed into a single one-line entry inside a closed expander,
    so what's actually actionable -- a newly uploaded, not-yet-processed
    ledger -- is the only thing shown open by default. The underlying
    guarantee this tab exists for is unchanged: ANY ledger-shaped PDF
    dropped into data/raw_pdfs/, any filename, gets a working path to its
    own results tab, with zero code changes.

    `all_documents`: the one discover_documents_fast() scan this script
    run already did for the dynamic-tab loop above -- passed in rather
    than calling dd.document_library() with no arguments (which would
    re-scan data/raw_pdfs/ a second time). See discover_documents_fast()'s
    own docstring for why a second, independent scan anywhere else would
    silently double this page's load time again.

    Does NOT call render_upload_section() itself (it did, until the
    2026-10-04 lazy-loading restructure) -- the upload widget is now
    rendered separately, earlier in the script, in its own
    `with top_process:` block, specifically so it's on screen before the
    one-time document scan `all_documents` depends on even starts (see
    the "Lazy-loading restructure" comment lower in this file for the
    full reasoning). This function is now called a second time, in a
    second `with top_process:` block right after that scan completes,
    and renders everything in the tab EXCEPT the uploader: the upload ->
    process sequence still reads top-to-bottom on screen exactly as
    before, it's just two script-level calls instead of one so the first
    half doesn't have to wait on the second."""
    library = dd.document_library(all_documents)
    if not library:
        st.caption("No documents on file yet -- upload one above.")
        return

    # Uploaded ledgers are processed automatically (see render_upload_
    # section()); a card appears here only for a ledger that has NO results
    # yet -- e.g. one dropped straight into data/raw_pdfs/, or whose earlier
    # processing was interrupted by a server restart -- so there is always a
    # manual way to run it.
    awaiting_reprocess = set()
    needs_action = [
        d for d in library
        if d["is_ledger"] and not d["is_known"] and not d["is_processed"]
    ]
    rest = [d for d in library if d not in needs_action]

    if needs_action:
        st.caption(
            f"**{len(needs_action)}** document"
            f"{'s' if len(needs_action) != 1 else ''} ready to process:"
        )
        for doc in needs_action:
            _was_replaced = doc["filename"] in awaiting_reprocess and doc["is_processed"]
            with st.container(border=True, key=f"card_doclib_{doc['filename']}"):
                _badge = (
                    "● Replaced -- Ready to Re-process" if _was_replaced
                    else "● Ready to Process"
                )
                st.markdown(
                    f"**📄 {doc['filename']}** -- {doc['doctype_display']}  \n"
                    f"<span style='color:#fbbf24; font-size:0.8rem; "
                    f"font-weight:600'>{_badge}</span>",
                    unsafe_allow_html=True,
                )
                _busy = jobs.is_active(key=doc["path"])
                if st.button(
                    "Processing..." if _busy else "Process Document",
                    key=f"_process_{doc['filename']}",
                    width="stretch", type="primary", disabled=_busy,
                ):
                    ingest.submit_ledger(doc["path"])
                    st.rerun()
    else:
        st.caption("Nothing new to process. Upload a PDF above to add one.")

    if rest:
        with st.expander(f"{len(rest)} document(s) already on file", expanded=False):
            _usage = dd.document_usage(all_documents)
            _table_rows = []
            for doc in rest:
                if not doc["is_ledger"]:
                    _table_rows.append({
                        "Document": doc["filename"], "Type": doc["doctype_display"],
                        "Status": _usage.get(doc["filename"], "--"), "Last processed": "--",
                    })
                    continue
                info = doc["tab_info"]
                if doc["is_known"] and info is None:
                    _table_rows.append({
                        "Document": doc["filename"], "Type": doc["doctype_display"],
                        "Status": "Uploaded", "Last processed": "--",
                    })
                    continue
                conf_suffix = " (low confidence)" if info.get("confidence") == "low" else ""
                when = plain.friendly_time(info.get("processed_at")) or "built-in sample"
                _table_rows.append({
                    "Document": doc["filename"], "Type": doc["doctype_display"],
                    "Status": f"Processed{conf_suffix}",
                    "Last processed": when,
                })
            st.dataframe(
                pd.DataFrame(_table_rows), width="stretch", hide_index=True,
            )
            st.caption(
                "Ledgers are processed automatically when uploaded and "
                "appear under **Audit**. Form 26AS, ITR, trial-balance and "
                "GST documents are analysed automatically too, and "
                "**🔁 Re-run Analysis** in the sidebar refreshes them "
                "again at any time."
            )
            for doc in rest:
                if doc["is_ledger"] and doc["is_processed"]:
                    _busy = jobs.is_active(key=doc["path"])
                    if st.button(
                        f"Re-processing {doc['filename']}..." if _busy
                        else f"Re-process {doc['filename']}",
                        key=f"_process_{doc['filename']}", disabled=_busy,
                    ):
                        ingest.submit_ledger(doc["path"])
                        st.rerun()


st.title("Audit Findings Dashboard")
st.caption(
    "Upload ledgers and tax documents to identify unusual entries, "
    "mismatches against the income tax return and Form 26AS, and "
    "differences between the books and the GST returns."
)
with st.expander("What this dashboard checks -- and what it does not"):
    st.markdown(plain.COVERAGE_NOTE)

# Toasts queued by the jobs panel when a background job finished (shown once,
# on the full rerun that follows).
for _toast_msg in st.session_state.pop("_pending_toasts", []):
    st.toast(_toast_msg)

# Loading banner slot, right under the title: filled below ONLY when the
# document scan actually has files to read (a warm scan is instant and shows
# nothing). Cleared the moment the scan finishes.
_boot_slot = st.empty()
_BOOT_PENDING = source_resolver.pending_scan_count(raw_dir=dd.RAW_PDFS_DIR)
if _BOOT_PENDING:
    _boot_slot.info(
        f"⏳ **Loading the dashboard** -- reading {_BOOT_PENDING} document"
        f"{'s' if _BOOT_PENDING != 1 else ''} for the first time. This only "
        f"happens once; the next load is instant. You can already upload "
        f"below -- it is saved and analysed in the background."
    )

report = dd.load_combined_report()
gstr_reports = dd.load_gstr_reports()   # sample returns and/or the person's own, never merged
tax_report = dd.load_tax_compliance_report()
# The ledger-vs-GSTR-1 taxable-supply reconciliation -- existed in output/
# already (computed and tested) but had no dashboard tab at all until
# 2026-10-04's finalization pass; see dashboard/data.py's
# load_ledger_gstr_report() docstring. Generalised 2026-10-07: one report
# per ledger (the sample ledger's, plus one for every ledger the person
# has processed, each checked against the GSTR-1 returns they uploaded) --
# see dd.run_ledger_gstr_pipeline().
ledger_gstr_reports = dd.load_ledger_gstr_reports()

# A running background analysis/processing job: the tabs below still show the
# last finished results, with a one-line note so nobody mistakes them for
# the final numbers.
_JOBS_ACTIVE = jobs.is_active()
# Says which document is still being read and that the numbers below do not
# include it yet (plain.results_pending_note) -- not a bare "running" line.
_ACTIVE_JOBS = jobs.snapshot()["active"] if _JOBS_ACTIVE else []
_REFRESH_NOTE = plain.results_pending_note(_ACTIVE_JOBS) if _JOBS_ACTIVE else ""


def _pending_note(have_results):
    """The in-progress notice for a tab; `have_results` says whether there is
    anything on screen below it yet."""
    return _REFRESH_NOTE if have_results else plain.results_pending_note(_ACTIVE_JOBS, have_results=False)

# ---------- Top-level navigation: 3 groups, nested tabs inside ----------
# Restructured 2026-10-04 from one flat row of 8 tabs (Process Documents,
# Overview, Ledger Flags, Tax & Compliance, Benford's Law, GSTR Status,
# [dynamic ledger tabs], Ledger vs GSTR-1) into 4 top-level groups, then
# (later the same day) from 4 groups to 3 -- the 4th, "Documents", is
# retired here: it was the ONLY place a non-frozen-baseline ledger's own
# analysis could be seen, which is exactly what caused "whether i put
# 2024 file or 2025 file it shows the same page" (the "Audit" tab always
# read output/combined_report.json -- the frozen FY2024-25 baseline --
# no matter what was actually processed, while a correctly-per-document
# view already existed one tab over, in "Documents", easy to miss).
# Fixed by making "Audit" itself the document-picker "Documents" used to
# be (see the dynamic-ledger-picker block further down, after the
# document scan it needs), so there is one place, not two, to look --
# "Documents" is retired as a name, not as a feature.
#
# Each remaining group is itself a top-level st.tabs() entry; Streamlit
# supports a second, nested st.tabs() call inside a tab's own `with`
# block (verified against the installed Streamlit 1.64 before relying on
# it), so the top bar shows only 3 names and clicking one reveals its own
# items as a second tab row underneath -- same pill styling throughout,
# nothing custom-built, and no new dependency.
#
# "Process" has exactly one item, so it's just the group tab directly --
# a nested single-item tab row would be pure clutter for a group with
# nothing to choose between. "Audit" now also has no nested tab row of
# its own (a selectbox picker instead -- see below), for the same reason.
top_process, top_audit, top_tax = st.tabs(
    ["Process", "Audit", "Tax & Compliance"]
)

with top_process:
    # Live cards for anything queued or running in the background, then the
    # recent results, then the uploader -- all of it rendered BEFORE the
    # document scan below, so none of it waits for the page to finish
    # loading (see render_upload_section()'s docstring for why an upload
    # at any moment is safe). The rest of this tab (the status table and
    # "ready to process" cards, which DO need the scan result) is rendered
    # after that scan -- see render_document_library().
    render_jobs_panel()
    render_upload_section()

# ---------- Audit (restructured 2026-10-04) ----------
# Content moved below, after the one-time document scan it needs (a
# selectbox over every processed ledger, not three tabs hardcoded to
# the frozen FY2024-25 baseline) -- see the ledger-picker block further
# down the script, right after `_all_documents` is computed, for why
# and exactly what replaced this.

with top_tax:
    tab_tax, tab_gstr, tab_ledger_gstr = st.tabs(
        ["Income tax & trial balance", "GST returns", "Ledger sales vs GST return"]
    )

# ---------- Tax & Compliance ----------
# Rebuilt 2026-10-07: this tab used to read the established FY2024-25
# documents through fixed filenames, so a tax statement / trial balance
# uploaded under any other name never reached it. It now reads
# output/tax_compliance_report.json, built by scripts/tax_compliance_report.py
# from WHICHEVER documents are on file, picked by their content (newest
# document wins; a 26AS is paired only with a Computation of Income and
# ITR-V for the same assessment year; a trial balance is reconciled only
# against a ledger covering the same dates). Every card says which files its
# numbers came from, and a check that cannot run says exactly what is
# missing instead of showing nothing.
with tab_tax:
    if _JOBS_ACTIVE:
        st.info(_pending_note(tax_report is not None), icon="🔄")
    if tax_report is None:
        st.caption(
            "No tax reconciliation results yet. Upload a Form 26AS (with "
            "its Computation of Income and ITR-V) and/or a trial balance "
            "under **Process** -- they are analysed automatically."
        )
    else:
        tax_summary = dd.tax_compliance_itr_summary(tax_report)
        tb_summary = dd.tax_compliance_tb_summary(tax_report)
        col_tax, col_tb = st.columns(2)

        with col_tax:
            with st.container(border=True, key="card_tax_detail"):
                st.markdown("**Income-tax return vs Form 26AS**")
                st.caption(
                    "Is the tax deducted at source (TDS) claimed in the return the "
                    "same as what the deductors reported?"
                )
                if tax_summary["status"] == "ok":
                    if tax_summary["flags_df"].empty:
                        verdict_line(True, "No mismatches")
                        st.caption(
                            f"All {tax_summary['deductor_count']} deductor(s) match"
                        )
                        st.caption(
                            f"{fmt_rs(tax_summary['total_claimed'])} claimed = "
                            f"{fmt_rs(tax_summary['total_reported'])} reported"
                        )
                    else:
                        verdict_line(
                            False,
                            f"{len(tax_summary['flags_df'])} of {tax_summary['deductor_count']} "
                            f"deductor(s) mismatch"
                        )
                        st.dataframe(
                            plain.rename_columns(tax_summary["flags_df"], plain.TAX_FLAG_COLUMNS),
                            width="stretch", hide_index=True,
                        )
                    st.caption(
                        f"Assessment year {tax_summary['assessment_year']} -- from "
                        + ", ".join(tax_summary["sources"].values())
                    )
                elif tax_summary["status"] == "incomplete":
                    st.warning(tax_summary["reason"])
                    if tax_summary["sources"]:
                        st.caption("Found: " + ", ".join(tax_summary["sources"].values()))
                elif tax_summary["status"] == "error":
                    st.error(tax_summary["reason"])
                else:
                    st.caption(tax_summary["reason"] or "No Form 26AS has been uploaded.")

        with col_tb:
            with st.container(border=True, key="card_tb_detail"):
                st.markdown("**Trial balance vs ledger**")
                st.caption(
                    "Does each account's closing balance in the trial balance agree "
                    "with the ledger for the same dates?"
                )
                if tb_summary["status"] == "ok":
                    if tb_summary["flags_df"].empty:
                        verdict_line(True, "No mismatches")
                        st.caption(
                            f"{tb_summary['clean_count']} of {tb_summary['total_checked']} "
                            f"accounts checked agree"
                        )
                        # st.info, not st.caption -- same "read this, it
                        # matters" pattern as the Benford Interpretation box
                        # below; only shown when there actually are
                        # unmatched rows.
                        if tb_summary["unmatched_count"]:
                            st.info(
                                f"{tb_summary['unmatched_count']} trial-balance line(s) are group "
                                f"headings or totals rather than accounts, so they are not part "
                                f"of this check."
                            )
                    else:
                        verdict_line(
                            False,
                            f"{tb_summary['mismatched_count']} of {tb_summary['total_checked']} "
                            f"accounts checked do not agree"
                        )
                        st.dataframe(
                            plain.rename_columns(tb_summary["flags_df"], plain.TB_FLAG_COLUMNS),
                            width="stretch", hide_index=True,
                        )
                    st.caption(
                        f"{', '.join(tb_summary['sources'].values())} ({tb_summary['period']}) "
                        f"compared with the ledger: {plain.ledger_name(tb_summary['ledger_label'])}"
                    )
                elif tb_summary["status"] == "incomplete":
                    st.warning(tb_summary["reason"])
                elif tb_summary["status"] == "error":
                    st.error(tb_summary["reason"])
                else:
                    st.caption(tb_summary["reason"] or "No trial balance has been uploaded.")

# ---------- GSTR Status ----------
with tab_gstr:
    st.subheader("GST returns: GSTR-1 (sales filed) vs GSTR-3B (tax paid)")
    st.caption("For each month, do the sales declared in GSTR-1 agree with the tax paid in GSTR-3B?")
    if _JOBS_ACTIVE:
        st.info(_pending_note(bool(gstr_reports)), icon="🔄")
    st.caption(
        "Sample returns and returns you upload are checked separately. Your "
        "GSTR-1 is compared only with your GSTR-3B, never with a sample "
        "return, and a month with only one of the two is shown as not "
        "compared, not as a missing filing."
    )
    if not gstr_reports:
        gsum = dd.gstr_summary(None)
        st.caption(
            "No GST results yet. Upload a GSTR-1 and a GSTR-3B (one month "
            "each, or a whole-year bundle) under **Process** -- they are "
            "analysed automatically."
        )
    else:
        if len(gstr_reports) > 1:
            _gs_labels = [e["label"] for e in gstr_reports]
            _gs_pick = st.selectbox("Returns", _gs_labels, key="gstr_pick")
            _gs_entry = gstr_reports[_gs_labels.index(_gs_pick)]
        else:
            _gs_entry = gstr_reports[0]
            st.caption(f"Returns: {_gs_entry['label']}")
        gsum = dd.gstr_summary(_gs_entry["report"])
        st.caption(f"Results updated {plain.friendly_time(gsum['generated_at'])}")
        for _w in gsum["warnings"]:
            st.warning(f"Skipped: {_w}")

        if gsum["gstr1_sources"] is not None:
            st.caption(
                "GSTR-1 used: " + (", ".join(gsum["gstr1_sources"]) or "none")
                + "  \nGSTR-3B used: " + (", ".join(gsum["gstr3b_sources"] or []) or "none")
            )
        _gst_message = plain.gst_status_message(gsum)
        if _gst_message:
            st.info(_gst_message)
        _gst_periods = dd.sort_gst_periods(gsum["periods"])
        if gsum["periods_compared"] is not None:
            _compared = dd.sort_gst_periods(gsum["periods_compared"])
            if _compared:
                st.write(
                    f"Compared {len(_compared)} month{'s' if len(_compared) != 1 else ''}: "
                    f"{_compared[0]} to {_compared[-1]}"
                )
        else:
            st.write(f"Periods on file: {', '.join(_gst_periods) or 'none'}")
        for _note in plain.gst_not_compared_notes(gsum):
            st.caption(_note)

        col1, col2 = st.columns(2)
        col1.metric("Findings", gsum["total_flagged"])
        col2.metric(
            "By severity",
            ", ".join(f"{k.title()}: {v}" for k, v in gsum["by_severity"].items()) or "none",
        )

        if gsum["flags_df"].empty:
            if gsum["status"] == "ok":
                verdict_line(True, "Every month compared agrees (within a small rounding allowance).")
        else:
            st.dataframe(
                plain.rename_gst_flags(gsum["flags_df"]),
                width="stretch", hide_index=True,
            )

        # Data-driven (this used to be a fixed sentence claiming "16 real
        # months ... April 2025 - July 2026", true only for the original
        # development documents and wrong the moment anything else was
        # uploaded).
        _n_periods = len(_gst_periods)
        if _n_periods:
            st.info(
                f"{_n_periods} filed period{'s' if _n_periods != 1 else ''} on file "
                f"({_gst_periods[0]} to {_gst_periods[-1]}). This section is "
                f"kept separate from the ledger checks because GST returns cover "
                f"their own periods. Upload another month under **Process** to add it."
            )

# ---------- Ledger vs GSTR-1 ----------
# Added 2026-10-04: the check that resolved this project's ORIGINAL
# motivating question (the ledger's Sales account Dr/Cr pattern, later
# traced to a header-detection bug -- see
# rule_ledger_gstr_reconciliation.py's own module docstring). Fully
# computed and tested since the header-detection bug chain was closed out
# (BASELINE_CHECKPOINT_V3.13.md), but had zero dashboard presence until
# this tab.
with tab_ledger_gstr:
    st.subheader("Ledger sales vs GSTR-1")
    # 2026-10-04, direct feedback: this was a wall of text under the
    # heading before a reader got to anything else on the page. Trimmed
    # to one sentence; nothing in the original explanation was deleted --
    # it's unchanged, just moved behind an opt-in expander instead of
    # being forced on every reader whether or not they wanted the detail.
    if _JOBS_ACTIVE:
        st.info(_pending_note(bool(ledger_gstr_reports)), icon="🔄")
    st.caption(
        "Do the sales recorded in the ledger agree with what was declared "
        "in GSTR-1, for the months both cover?"
    )
    with st.expander("ⓘ Learn more"):
        st.caption(
            "Adds up the sales the ledger itself records -- both sales that "
            "carry GST and sales that carry none -- and compares them month by "
            "month with what was declared in GSTR-1. Each ledger you upload is "
            "compared only with the GSTR-1 returns you upload (the built-in "
            "sample ledger with the built-in sample returns), and the "
            "comparison assumes those returns belong to the same business as "
            "the ledger. Months the uploaded returns do not cover are listed "
            "as not compared; they are not treated as missing filings."
        )

    if not ledger_gstr_reports:
        st.caption(
            "Not generated yet. Click **🔁 Re-run Analysis** in the sidebar."
        )
    else:
        if len(ledger_gstr_reports) > 1:
            _lg_labels = [plain.ledger_name(e["label"]) for e in ledger_gstr_reports]
            _lg_pick = st.selectbox("Ledger", _lg_labels, key="ledger_gstr_pick")
            _lg_entry = ledger_gstr_reports[_lg_labels.index(_lg_pick)]
        else:
            _lg_entry = ledger_gstr_reports[0]
            st.caption(f"Ledger: {plain.ledger_name(_lg_entry['label'])}")
        lg_summary = dd.ledger_gstr_summary(_lg_entry["report"])

        st.caption(f"Results updated {plain.friendly_time(lg_summary['generated_at'])}")
        _lg_message = plain.ledger_gst_status_message(lg_summary)
        for _w in lg_summary.get("gstr1_warnings") or []:
            st.warning(f"A GSTR-1 file could not be read and was left out: {_w}")
        if _lg_message:
            st.info(_lg_message)
            if lg_summary["status"] == "no_overlap" and lg_summary.get("gstr1_sources"):
                st.caption("Returns used: " + ", ".join(lg_summary["gstr1_sources"]))
        else:
            st.write(f"Months with GST sales: {', '.join(dd.sort_gst_periods(lg_summary['taxable_periods'])) or 'none'}")
            st.write(f"Months with sales that carry no GST: {', '.join(dd.sort_gst_periods(lg_summary['non_gst_periods'])) or 'none'}")
            if lg_summary.get("periods_compared") is not None:
                _compared = dd.sort_gst_periods(lg_summary["periods_compared"])
                st.caption(
                    f"Compared {len(_compared)} month{'s' if len(_compared) != 1 else ''} "
                    f"against: {', '.join(lg_summary.get('gstr1_sources') or ['the GSTR-1 data on file'])}."
                )
            if lg_summary.get("not_covered_periods"):
                st.caption(
                    "Not compared -- the uploaded returns do not cover these months: "
                    + ", ".join(dd.sort_gst_periods(lg_summary["not_covered_periods"])) + "."
                )

            lg_col1, lg_col2 = st.columns(2)
            lg_col1.metric("Findings", lg_summary["total_flagged"])
            lg_col2.metric(
                "By severity",
                ", ".join(f"{k.title()}: {v}" for k, v in lg_summary["by_severity"].items()) or "none",
            )

            if lg_summary["flags_df"].empty:
                verdict_line(
                    True,
                    "In every month covered by both, the ledger's GST sales and "
                    "its no-GST sales agree with what was filed in GSTR-1 (within Rs.10)."
                )
                # Shown ONLY when nothing is flagged: this used to be printed
                # unconditionally, i.e. "Strong cross-validation" even directly
                # under a table of mismatches.
                st.info("✓ Both documents agree: the ledger and the GST return were produced independently.")
                with st.expander("ⓘ Why this matters"):
                    st.caption(
                        "The ledger and the GSTR-1 return come from two separate "
                        "sources. When they agree month after month, that supports "
                        "the accuracy of both for those months."
                    )
            else:
                st.dataframe(
                    plain.rename_gst_flags(lg_summary["flags_df"]),
                    width="stretch", hide_index=True,
                )
            if lg_summary.get("non_gst_accounts"):
                with st.expander("Technical details"):
                    st.caption(
                        "Sales without GST were taken from sales-type vouchers that "
                        "carry no CGST/SGST/IGST line. Accounts counted: "
                        + ", ".join(lg_summary["non_gst_accounts"]) + "."
                    )
# ---------- The one document scan this script run uses ----------
# History (2026-10-04): profiling found ~83% of the page's server-side time
# was classify_document() opening every PDF in data/raw_pdfs/, so the Tax &
# Compliance tabs (which read only finished output/*.json) were moved to
# render BEFORE it, and a per-file cache was added -- but that cache lived in
# st.session_state, so every new browser session / refresh paid the full
# scan again (~7.8s for the 11 established documents, ~12s+ with more).
#
# 2026-10-07: the cache is now persistent (source_resolver.scan_documents():
# output/classification_cache.json + process memory, keyed by filename,
# mtime and size). Only a file never seen before is ever classified, and
# each result is saved the moment it is known, so even a scan interrupted by
# the person uploading mid-load keeps its progress. A warm load does no PDF
# work at all.
#
# What the person sees while a scan is actually needed (a new or changed
# file): a banner under the title with a real progress bar ("Reading
# documents: 3 of 16 -- name.pdf"), a "Loading documents..." note in the
# Audit tab and the sidebar (which are empty until the scan result exists),
# and the uploader already usable. A warm load shows none of this because
# it takes no time.
with top_audit:
    _audit_slot = st.empty()
with st.sidebar:
    _sidebar_slot = st.empty()
if _BOOT_PENDING:
    _audit_slot.caption("Loading documents...")
    _sidebar_slot.caption("Loading documents...")

    with _boot_slot.container():
        _boot_bar = st.progress(
            0.0, text=f"Reading documents: 0 of {_BOOT_PENDING + 0} new..."
        )

    def _boot_progress(done, total, name):
        _boot_bar.progress(
            done / max(total, 1), text=f"Reading documents: {done} of {total} -- {name}"
        )

    _all_documents = discover_documents_fast(on_progress=_boot_progress)
else:
    _all_documents = discover_documents_fast()
_boot_slot.empty()
_audit_slot.empty()
_sidebar_slot.empty()

# First-ever load of a fresh deployment (or one whose outputs were cleared):
# documents are on file but no analysis has ever run. Rather than showing
# empty tabs and asking the person to find "Re-run Analysis", run it once in
# the background. Guarded per server process so it can never loop.
@st.cache_resource
def _bootstrap_state():
    return {"done": False}


_has_analysable = any(
    d["doctype"] in ("form_26as", "itr_computation", "itr_acknowledgement",
                     "trial_balance", "gstr1", "gstr3b")
    for d in _all_documents
)
_boot_state = _bootstrap_state()
if (
    not _boot_state["done"] and _has_analysable
    and (tax_report is None or not gstr_reports)
):
    _boot_state["done"] = True
    ingest.submit_analysis()
    st.rerun()
_boot_state["done"] = True

with top_process:
    # The other half of this tab (see the early `with top_process:`
    # block above, and render_document_library()'s own docstring) --
    # the status table and "ready to process" cards, which need
    # `_all_documents` and so had to wait for the scan just above.
    render_document_library(_all_documents)

# ---------- Sidebar: compact Data panel (doc count + upload) + Pipeline (stage status + re-run) ----------
with st.sidebar:
    _status_report = dd.load_combined_report()
    _status_gstr = dd.newest_report(dd.load_gstr_reports())
    _status = dd.pipeline_status(_status_report, _status_gstr)
    _sidebar_library = dd.document_library(_all_documents)
    _sidebar_processed = sum(1 for d in _sidebar_library if d["is_ledger"] and d["is_processed"])

    st.header("Data")
    _data_col1, _data_col2 = st.columns(2)
    _data_col1.metric("Documents", len(_sidebar_library))
    _data_col2.metric("Processed", _sidebar_processed)
    st.caption(
        f"Last updated: {plain.friendly_time(_status['last_run'])}" if _status["last_run"]
        else "Not processed yet."
    )
    # 2026-10-04: direct feedback -- "Processed 2" here next to an empty
    # Audit tab reads as a contradiction. It isn't one (Addendum 12:
    # Audit deliberately excludes this project's 2 pre-built baseline
    # documents), but a reader has no way to know that from the numbers
    # alone, so it's spelled out once, here, where the confusing number
    # actually is -- not buried in a README.
    if _sidebar_processed:
        st.caption(
            "Includes the built-in sample documents. **Audit** does not list "
            "those -- only ledgers you upload yourself appear there."
        )

    # Upload moved to the Process Documents tab (render_upload_section(),
    # its own `with top_process:` block near the top of the script) on
    # 2026-10-04, after direct feedback that upload and process being in
    # two different places --
    # sidebar vs. main tab -- made the workflow read as two disconnected
    # actions instead of one sequence ("he shud upload document click on
    # a process tab below upload... so it shud follow a sequence"). Only
    # a pointer lives here now; this mirrors the earlier move of the
    # Process button itself out of the sidebar for the same reason.
    st.caption(
        "📄 To upload or process a document, go to **Process Documents** "
        "under **Process**, above."
    )
    # Collapsed by default (expanded=False, Streamlit's own default) --
    # narrowed 2026-10-04 to just filename + status per row (was a bare
    # list of filenames) so this panel doesn't need the Process Documents
    # tab's own status table open in another tab just to answer "is this
    # one already done".
    with st.expander("View documents"):
        _sidebar_usage = dd.document_usage(_all_documents)
        st.dataframe(
            pd.DataFrame([
                {"Document": d["filename"], "Status": (
                    "Processed" if d["is_ledger"] and d["is_processed"]
                    else "Processing..." if d["is_ledger"] and jobs.is_active(key=d["path"])
                    else "Ready to Process" if d["is_ledger"]
                    else _sidebar_usage.get(d["filename"], "Uploaded")
                )}
                for d in _sidebar_library
            ]),
            width="stretch", hide_index=True,
        )
    st.caption(
        "Each document is recognised from its contents and given the "
        "right checks automatically. Nothing to choose here."
    )

    st.divider()
    st.header("Analysis")

    # Analysis now runs on the background worker (dashboard/jobs.py), so its
    # outcome is not available in the same script run as the click. The
    # Process tab's jobs panel shows it in full (including the friendly
    # "setup issue, not an audit finding" wording for a missing package);
    # here only the live state is shown.
    if jobs.is_active("analysis"):
        st.info("🔄 Analysis is running in the background...")

    # UI-copy correction (2026-10-04, follow-up to the architecture
    # correction above): the previous version of this caption explained
    # the INTERNAL reasoning for the change itself (old button names,
    # function names, before/after narrative) -- implementation history
    # that belongs in BASELINE_CHECKPOINT_V3.14.md (Addendum 10) and
    # dashboard/data.py's run_full_analysis() docstring, not in a
    # user-facing dashboard a real auditor is looking at. Replaced with
    # one plain sentence describing what the button actually does, at the
    # level a user needs: nothing about HOW it was built.
    st.caption("Re-runs all checks on the uploaded documents.")
    # Same reasoning as the old three-buttons' own st.rerun() (now one
    # call): the Audit/Tax & Compliance tabs read report/gstr_report/
    # ledger_gstr_reports EARLIER in script order than this sidebar runs
    # (see the "Lazy-loading restructure" comment further down), so
    # without an explicit rerun the refreshed numbers wouldn't show up
    # until some unrelated later interaction. The result is stashed first
    # so it survives to be shown on the run that follows.
    if st.button(
        "🔄 Analysis running..." if jobs.is_active("analysis") else "🔁 Re-run Analysis",
        width="stretch", type="primary", disabled=jobs.is_active("analysis"),
    ):
        ingest.submit_analysis()
        st.rerun()
    if _status["gstr_last_run"]:
        st.caption(f"GST checks last run: {plain.friendly_time(_status['gstr_last_run'])}")

    st.divider()
    with st.expander("Advanced: re-read the two sample ledgers"):
        st.caption(
            "For maintenance only -- you do not need this for your own "
            "documents (upload them under **Process Documents** instead). "
            "This re-reads one of the two built-in sample ledgers from its "
            "PDF, start to finish. Afterwards, click **Re-run Analysis** "
            "above to refresh the findings."
        )
        for _pdf_basename, _info in dd.LEDGER_PDF_INFO.items():
            _btn_label = f"Re-read the full ledger -- {_info['label']}"
            if _info["is_frozen"]:
                # The frozen FY2024-25 baseline must stay byte-for-byte untouched
                # unless a change is explicitly reviewed and approved (a standing
                # project rule, enforced here as this button's only extra
                # guard -- reconstruct_full_document() itself is verified safe
                # and byte-identical against this PDF, so the gate is about
                # deliberate human sign-off before overwriting the committed
                # baseline file, not about distrust of the function).
                _ack_key = f"_ack_frozen_{_pdf_basename}"
                _acknowledged = st.checkbox(
                    f"I've reviewed this and want to re-read the protected "
                    f"{_info['label']} reference ledger",
                    key=_ack_key,
                )
                _disabled = not _acknowledged
            else:
                _disabled = False
            if st.button(_btn_label, width="stretch", disabled=_disabled, key=f"_recon_btn_{_pdf_basename}"):
                with st.spinner(f"Re-reading {_info['label']} from {os.path.basename(_info['pdf_path'])}... this takes under a minute."):
                    try:
                        _recon = dd.run_full_ledger_reconstruction(_pdf_basename)
                        st.cache_data.clear()
                        st.success(
                            f"Reconstructed {_recon['total_vouchers']:,} vouchers across "
                            f"{_recon['total_pages']:,} pages -> {_recon['output_csv']}"
                        )
                        if _recon["unbalanced_count_changed"]:
                            st.warning(
                                f"Unbalanced-voucher count is {_recon['unbalanced_count']}, "
                                f"differing from this PDF's known baseline of "
                                f"{_recon['known_unbalanced']} -- worth a manual look before "
                                f"trusting this re-run (could mean a genuinely different "
                                f"source PDF, or a new header/format shape this parser "
                                f"hasn't seen -- see reconstruct_full_document()'s docstring)."
                            )
                        else:
                            st.caption(
                                f"Unbalanced vouchers: {_recon['unbalanced_count']} "
                                f"(matches known baseline)."
                            )
                        st.caption(
                            "Click Re-run Analysis above to refresh the flags "
                            "against this new data."
                        )
                    except Exception as e:
                        st.error(f"Re-reading failed: {e}")


# ---------- Audit: ledger picker (replaces the retired "Documents" group) ----------
# Direct bug report this fixes: "whether i put 2024 file or 2025 file it
# shows the same page" -- the OLD "Audit" tab (removed above) always read
# output/combined_report.json with no argument, which only ever resolves
# to the frozen FY2024-25 baseline, regardless of what was actually
# processed; meanwhile a correctly per-document view already existed one
# tab over, in "Documents" (the loop this block replaces), which excluded
# that same FY2024-25 baseline on purpose (see its old comment, now
# removed) because the baseline kept its own separate hardcoded tab set.
# Fixing both problems at once meant retiring the split itself, not
# patching either side alone: one selectbox, here, over EVERY processed
# ledger -- the frozen baseline included -- driving a single call to
# render_generic_ledger_tab(), the same per-ledger renderer already
# proven correct for FY2025-26. A brand-new uploaded ledger gets a picker
# entry the moment it's processed, with zero code changes here.
#
# 2026-10-04: the above "frozen baseline included" behavior was deliberately
# reversed on explicit request. For the *generalized* portal, Audit must
# never silently show the two development-era stored reports
# (FY2024-25/FY2025-26 -- both keyed in dd.LEDGER_PDF_INFO) just because
# they happen to sit in data/raw_pdfs/ with old output already computed.
# Audit now shows results only for ledgers that were actually processed
# in *this* run of the generic upload-and-process flow -- so a document
# whose slug is a LEDGER_PDF_INFO key (i.e. one of our own two baked-in
# development baselines) is excluded here, full stop, regardless of
# whether its report file happens to already exist on disk. This makes
# "no document processed yet" genuinely mean "no results," rather than
# quietly falling back to whichever of our two dev datasets sorts first.
_audit_ledger_infos = []
for _doc in dd.discover_ledger_documents(_all_documents):
    _info = dd.ledger_tab_info(_doc["path"])
    if _info is None:
        continue
    if _info["slug"] in dd.LEDGER_PDF_INFO:
        continue
    _audit_ledger_infos.append(_info)

with top_audit:
    if _JOBS_ACTIVE:
        # Nothing finished yet while a document is being read: say so, rather
        # than telling the person to upload what they just did.
        st.info(_pending_note(bool(_audit_ledger_infos)), icon="🔄")
    if not _audit_ledger_infos:
        if not _JOBS_ACTIVE:
            st.caption(
                "No audit results available yet. Upload a ledger under "
                "**Process** -- it is analysed automatically and appears here "
                "when it finishes."
            )
    else:
        # 2026-10-04, direct feedback: this picker IS the "switch which
        # audit you're looking at" control this project has -- there's no
        # separate company/tenant model to switch between (see the
        # "Current audit" caption inside render_generic_ledger_tab()
        # above for why no company field is invented here either), so
        # relabeled from the generic "Ledger" to say that plainly, with a
        # one-line pointer shown only when there's actually more than one
        # processed document to switch between.
        if len(_audit_ledger_infos) > 1:
            st.caption(
                "More than one document has been processed -- pick which "
                "one's results to view below."
            )
        # Two documents can cover the same period (two companies, or one
        # ledger uploaded twice) and so share the same label; the picker
        # looks the choice up BY LABEL, so duplicates would always resolve
        # to the first one -- the "different file, same page" bug again.
        # Duplicated labels get the filename appended; unique ones are
        # left exactly as they were.
        _label_counts = {}
        for _i in _audit_ledger_infos:
            _label_counts[_i["label"]] = _label_counts.get(_i["label"], 0) + 1
        _audit_labels = [
            f"{_i['label']} -- {_i.get('filename', _i['slug'])}"
            if _label_counts[_i["label"]] > 1 else _i["label"]
            for _i in _audit_ledger_infos
        ]
        _audit_pick = st.selectbox(
            "Switch audit", _audit_labels, key="_audit_ledger_pick",
            help="Each processed document gets its own entry here.",
        )
        _audit_selected = _audit_ledger_infos[_audit_labels.index(_audit_pick)]

        # try/except around the call itself (not just inside the renderer)
        # so a single document whose report turns out to be unexpectedly
        # malformed can never take the rest of the dashboard down with it
        # -- the explicit "I don't want it to fail on any document"
        # requirement, applied at the UI layer as well as the processing
        # layer. Same guard the old per-tab loop had.
        _audit_render_ok = True
        try:
            render_generic_ledger_tab(_audit_selected)
        except Exception as e:
            _audit_render_ok = False
            st.error(
                f"Couldn't render this view for "
                f"{_audit_selected.get('filename', _audit_selected.get('label'))}: {e}"
            )

        # The frozen FY2024-25 baseline has extra, deeper analysis that
        # genuinely only exists for IT specifically (scripts/analyze_
        # benford_segmentation.py reads data/general_ledger.csv directly --
        # see render_generic_ledger_tab()'s own caption on this).
        #
        # 2026-10-04: left intact on purpose even though the exclusion
        # filter above now means _audit_selected["slug"] can never equal
        # FROZEN_BASELINE_SLUG in practice -- the frozen baseline no
        # longer appears in the picker at all, by design, so this branch
        # is currently unreachable dead code rather than a working
        # feature quietly removed. Not deleted here because that's a
        # judgment call (losing the segmentation walkthrough entirely vs.
        # keeping the code for a possible future "view baseline anyway"
        # affordance) flagged back to the user rather than made silently.
        if _audit_render_ok and _audit_selected["slug"] == dd.FROZEN_BASELINE_SLUG:
            st.divider()
            st.subheader("Why trust MAD here? The segmentation evidence")
            seg_summary = cached_segmentation_summary()
            if seg_summary is None:
                st.caption("Segmentation analysis not available.")
            else:
                # Checklist (restructured 2026-10-02) built from the same
                # numbers the full verdict paragraph already uses -- see
                # benford_segmentation_summary()'s docstring -- so an
                # auditor can scan this instead of reading a wall of text;
                # the full prose plus the breakdown tables are one click
                # away, not deleted.
                breadth_ok = seg_summary["deviating_count"] == seg_summary["substantial_count"]
                st.markdown(
                    f"{'✓' if breadth_ok else '⚠'} {seg_summary['deviating_count']} of "
                    f"{seg_summary['substantial_count']} voucher types (n≥10 legs) deviate on chi-square"
                )
                st.markdown(
                    f"{'✓' if seg_summary['both_exclusions_deviate'] else '⚠'} Deviation "
                    f"{'persists' if seg_summary['both_exclusions_deviate'] else 'does not persist'} "
                    "after excluding the highest-volume accounts and fuel-sale voucher types"
                )
                if seg_summary["account_substantial_count"]:
                    acct_ok = seg_summary["account_deviating_count"] == seg_summary["account_substantial_count"]
                    st.markdown(
                        f"{'✓' if acct_ok else '⚠'} {seg_summary['account_deviating_count']} of "
                        f"{seg_summary['account_substantial_count']} accounts (n≥10 legs) also deviate -- "
                        f"{'not concentrated in one account' if acct_ok else 'unevenly spread across accounts, worth a closer look'}"
                    )
                for _name, _chi in zip(seg_summary["outlier_segments"], seg_summary["outlier_chi_squares"]):
                    st.markdown(
                        f"⚠ **{_name}** shows one of the strongest deviations "
                        f"(chi-square {_chi:.0f}) -- worth a manual look as a precaution"
                    )

                with st.expander("Detailed segmentation evidence"):
                    st.write(seg_summary["verdict"])

                    st.markdown("**By voucher type (all)**")
                    st.dataframe(
                        seg_summary["by_vch_type_df"].style.format({
                            "pct_of_total": "{:.1f}%", "chi_square": "{:.2f}", "mad": "{:.5f}",
                        }),
                        width="stretch", hide_index=True,
                    )
                    st.markdown("**By account (top 15 by leg count)**")
                    st.dataframe(
                        seg_summary["by_account_df"].style.format({
                            "pct_of_total": "{:.1f}%", "chi_square": "{:.2f}", "mad": "{:.5f}",
                        }),
                        width="stretch", hide_index=True,
                    )
                    st.markdown("**Exclusion checks (removing the obvious repetitive-pricing candidates)**")
                    for check in seg_summary["exclusion_checks"]:
                        verdict = "Deviates" if check["deviates"] else "Conforms"
                        st.write(
                            f"**{check['label']}** -- n={check['n']:,} ({check['pct_of_total']:.1f}% "
                            f"of all legs), chi-square={check['chi_square']:.2f} ({verdict}), "
                            f"MAD={check['mad']:.5f} ({check['mad_conformity']})"
                        )

