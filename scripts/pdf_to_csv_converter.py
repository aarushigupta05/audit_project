"""
pdf_to_csv_converter.py

Sub-module: converts PDF input into CSV automatically so the main
pipeline never has to handle raw PDF files directly.

Dependencies:
    pip install pdfplumber pandas openpyxl xlrd
    (optional, for scanned/image-based PDFs)
    pip install pytesseract pdf2image
    (also requires system binaries: tesseract-ocr, poppler-utils)

IMPORTANT - two-step pipeline, not one:
    This module solves "PDF/Excel -> clean CSV". It does NOT map columns
    to your canonical schema (entry_id, entered_by, approved_by, amount,
    account...). A PDF's own header text ("Particulars", "Amt (Rs.)", ...)
    will NOT automatically match what your rule scripts expect. A separate
    schema-adapter/column-mapping step is still required after this.

FINAL SCOPE (v1 finalization, 2026-10-04): every real source document this
project has ever been given (all 11 files in data/raw_pdfs/) is a PDF.
Tally itself can export the same ledger as Excel (.xlsx) or XML instead of
print-to-PDF, and a real export in either format would likely be EASIER to
ingest than the PDF path -- almost all of reconstruct_ledger_entries.py's
header-detection machinery (Shape 1/2/3 parsing, the bare-Book/Account
qualifier fix, continuation-page name aliasing -- see
BASELINE_CHECKPOINT_V3.13.md) exists only to undo damage PDF print-layout
does to a ledger's structure; a genuine tabular export wouldn't need any
of it. There is no Excel/XML adapter in this project because no sample
file in either format has ever been supplied, not because the format is
hard -- building one by guessing Tally's real column layout would violate
this project's "never guess" discipline. Revisit only once a real sample
export exists to verify an adapter against.
"""

import os
import re
import csv
import logging
import pdfplumber
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdf_to_csv_converter")

# Characters that trigger formula execution if a cell is opened in Excel
_CSV_INJECTION_CHARS = ("=", "+", "-", "@", "\t", "\r")


# ---------------------------------------------------------------------
# Small standalone helpers (usable on their own, and used internally)
# ---------------------------------------------------------------------

def normalize_indian_number(value) -> "float | None":
    """
    Converts Indian-formatted numeric strings to a proper float.
    Handles:
        "12,00,395.21"  -> 1200395.21   (lakh/crore-style commas)
        "(1,001.00)"    -> -1001.0      (accounting-style negative)
        "1,001.00-"     -> -1001.0      (trailing-minus negative)
        "Rs. 500"       -> 500.0
        "-"             -> None
        ""              -> None
    Returns None if unparseable, so callers can decide how to handle
    non-numeric cells instead of crashing on float(x).
    """
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "--", "NA", "N/A", "nan", "None"):
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.endswith("-"):
        negative = True
        s = s[:-1].strip()
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()

    s = re.sub(r"[^\d.]", "", s)  # strip currency symbols, commas, spaces

    if s == "" or not re.match(r"^\d+(\.\d+)?$", s):
        return None

    num = float(s)
    return -num if negative else num


def normalize_date(value, dayfirst: bool = True) -> "str | None":
    """
    Best-effort date normalizer -> returns ISO 'YYYY-MM-DD' string, or
    None if unparseable. dayfirst=True matches Indian conventions
    (dd-mm-yyyy, dd-MMM-yyyy as used in 26AS/AIS exports).
    """
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = pd.to_datetime(str(value).strip(), dayfirst=dayfirst, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return None


def sanitize_for_csv(value):
    """
    Prevents CSV-injection: if a cell's text starts with a character
    that Excel/Sheets treats as a formula trigger, prefix it with a
    single quote so it's opened as literal text, not executed.
    """
    if isinstance(value, str) and value.startswith(_CSV_INJECTION_CHARS):
        return "'" + value
    return value


def check_is_ledger_document(pdf_path) -> bool:
    """
    Evidence-based detector for accounting ledger PDFs. Pulled out of
    PDFToCSVConverter.is_ledger_document() as a standalone, read-only
    function (2026-10-04, dashboard genericization) -- that method's own
    class constructor does `os.makedirs(output_dir)` as a side effect,
    which is wrong for a pure classification question with nothing to
    write anywhere. PDFToCSVConverter.is_ledger_document() is now a thin
    wrapper around this; both share the exact same logic, not a forked
    copy.

    Checks initial pages (up to 5) for strong structural ledger markers:
      - 'Date Particulars Vch Type'
      - 'Ledger Account' or 'Ledger :'
    If detected on multiple pages or with high confidence, identifies
    the document as an accounting ledger requiring the dedicated
    reconstruction path (reconstruct_ledger_entries.py), not the generic
    table/text/OCR extraction strategies below.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            sample_count = min(5, len(pdf.pages))
            ledger_evidence_count = 0
            for i in range(sample_count):
                text = pdf.pages[i].extract_text() or ""
                has_col_hdr = "Date Particulars" in text and ("Vch Type" in text or "Debit Credit" in text)
                has_acc_hdr = "Ledger Account" in text or "Ledger :" in text or ("Cash :" in text and "Book" in text)
                if has_col_hdr or (has_acc_hdr and "Particulars" in text):
                    ledger_evidence_count += 1
            return ledger_evidence_count >= 1 and (ledger_evidence_count / sample_count) >= 0.4
    except Exception as e:
        logger.warning(f"Could not check ledger markers for {pdf_path}: {e}")
        return False


def _looks_like_header(row) -> bool:
    """
    Heuristic: a real header row is mostly short text labels, not
    currency amounts. Used to avoid mistaking a data row for a header
    when a PDF has grid lines around every individual transaction
    (causing pdfplumber to detect one micro-table per row instead of
    one table per page).
    """
    non_empty = [str(c).strip() for c in row if c is not None and str(c).strip() != ""]
    if not non_empty:
        return False
    numeric_like = sum(1 for c in non_empty if normalize_indian_number(c) is not None)
    return (numeric_like / len(non_empty)) < 0.3


def _is_fragmented_ledger_page(tables_on_page, min_tables=3, max_median_rows=2):
    """
    True if this page looks like it was split into many small
    per-transaction micro-tables (grid lines drawn around every row)
    rather than one real table with a header. Decided from page-level
    shape statistics, not any single fragment's text content -- this
    avoids narration-only rows (no numbers, but not headers either,
    e.g. "Being cheque received bounced due to insufficient balance")
    being misclassified as headers by _looks_like_header, which can
    only see one fragment at a time.
    """
    if len(tables_on_page) < min_tables:
        return False
    row_counts = sorted(len(t) for t in tables_on_page if t)
    if not row_counts:
        return False
    median_rows = row_counts[len(row_counts) // 2]
    return median_rows <= max_median_rows


def _normalize_header(value):
    """Normalize a header so equivalent headers can be grouped."""
    if value is None:
        return ""

    value = str(value).replace("\n", " ").strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-zA-Z0-9%/().& -]", "", value)

    return value.lower()


def _header_signature(df):
    """Return a normalized signature for the real columns of a table."""
    return tuple(
        _normalize_header(c)
        for c in df.columns
        if not str(c).startswith("__")
    )


def _is_separator_row(row):
    """Detect PDF formatting rows such as ----- or =====."""
    text = " ".join(
        str(v) for v in row
        if pd.notna(v)
    ).strip()

    if not text:
        return True

    compact = re.sub(r"\s+", "", text)

    return (
        len(compact) >= 5
        and set(compact) <= set("-=_|.")
    )


def _is_page_marker(row):
    """Detect rows such as 'Page 1 of 7'."""
    text = " ".join(
        str(v) for v in row
        if pd.notna(v)
    ).strip()

    return bool(
        re.search(
            r"\bpage\s+\d+\s*(of\s*\d+)?\b",
            text,
            re.IGNORECASE
        )
    )


def _clean_header_names(columns):
    """Make extracted column names readable and unique."""
    cleaned = []
    seen = {}

    for i, col in enumerate(columns):
        name = "" if col is None else str(col)
        name = name.replace("\n", " ")
        name = re.sub(r"\s+", " ", name).strip()

        if not name:
            name = f"column_{i + 1}"

        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0

        cleaned.append(name)

    return cleaned

# ---------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------
def _split_embedded_pipe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Split cells containing embedded pipe-delimited values into real columns.

    Example:
        "JAMMU | JAKA0EBATRA | SIDHRA,JAMMU | SAVING | | No"

    becomes separate columns while preserving empty fields.

    Existing column names are preserved wherever possible.
    Additional columns created by splitting receive generated names.
    """
    if df is None or df.empty:
        return df

    expanded_rows = []

    for _, row in df.iterrows():
        expanded = []

        for value in row.tolist():
            if pd.isna(value):
                expanded.append("")
                continue

            text = str(value).strip()

            if "|" in text:
                # Keep empty fields because || represents a real
                # missing/blank column.
                parts = [part.strip() for part in text.split("|")]
                expanded.extend(parts)
            else:
                expanded.append(text)

        expanded_rows.append(expanded)

    if not expanded_rows:
        return df

    max_cols = max(len(row) for row in expanded_rows)

    normalized_rows = [
        row + [""] * (max_cols - len(row))
        for row in expanded_rows
    ]

    original_columns = list(df.columns)

    # Preserve existing column names.
    new_columns = original_columns[:max_cols]

    # Create names for any newly introduced columns.
    while len(new_columns) < max_cols:
        new_columns.append(f"column_{len(new_columns) + 1}")

    result = pd.DataFrame(
        normalized_rows,
        columns=new_columns
    )

    return result

class PDFToCSVConverter:
    """
    Converts a PDF file into one or more CSV files.
    """

    def __init__(self, pdf_path: str, output_dir: str = "converted_csv"):
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        if not os.path.exists(self.pdf_path):
            raise FileNotFoundError(f"PDF not found: {self.pdf_path}")

        self.base_name = os.path.splitext(os.path.basename(self.pdf_path))[0]
        self.output_csv_path = os.path.join(self.output_dir, f"{self.base_name}.csv")

    def is_pdf(self) -> bool:
        return self.pdf_path.lower().endswith(".pdf")

    # -----------------------------------------------------------------
    # Strategy 1: structured table extraction
    # -----------------------------------------------------------------

    def extract_tables(self):
        """
        Pads/trims each row to match the header length instead of
        letting pandas crash on a length mismatch (merged cells and
        ragged rows are extremely common in real PDF tables).
        """
        all_tables = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables()
                if not tables:
                    continue

                # Decided once per page, from page-level shape, not
                # per-fragment content -- see _is_fragmented_ledger_page.
                ledger_mode = _is_fragmented_ledger_page(tables)

                for t_index, table in enumerate(tables):
                    if not table:
                        continue

                    header, *rows = table
                    # Force every header cell to a safe string. Real PDFs
                    # frequently produce None (blank header cell) or, in
                    # rare cases, non-string types from merged/misread
                    # cells -- anything downstream that assumes header
                    # names are text (column filtering, dedup, etc.) will
                    # crash on those otherwise.
                    header = [
                        str(h).strip() if h is not None and str(h).strip() != "" else f"col_{i+1}"
                        for i, h in enumerate(header)
                    ]

                    # If the "header" row actually looks like a data row
                    # (mostly numbers/amounts), this table is really a
                    # fragment of a bigger table that pdfplumber split
                    # into micro-tables (common when a PDF draws grid
                    # lines around every transaction row). Treat the
                    # whole table as data with generic column names
                    # instead of losing/misreading that row as a header.
                    if ledger_mode or not _looks_like_header(table[0]):
                        n_cols = max(len(r) for r in table)
                        header = [f"col_{i+1}" for i in range(n_cols)]
                        rows = table
                    n_cols = len(header)

                    fixed_rows = []

                    for row in rows:
                        row = list(row)

                        if len(row) < n_cols:
                            row = row + [None] * (n_cols - len(row))

                        # Do not discard or squash extra extracted cells.
                        # We will normalize the width after examining all rows.
                        fixed_rows.append(row)

                    # Find the widest extracted row.
                    max_row_cols = max(
                        len(row) for row in fixed_rows
                    ) if fixed_rows else n_cols

                    # If some rows contain more cells than the detected header,
                    # preserve those cells by extending the header.
                    if max_row_cols > n_cols:
                        header = header + [
                            f"column_{i + 1}"
                            for i in range(n_cols, max_row_cols)
                        ]

                    n_cols = max_row_cols

                    # Normalize every row to the same width.
                    normalized_fixed_rows = []

                    for row in fixed_rows:
                        if len(row) < n_cols:
                            row = row + [None] * (n_cols - len(row))
                        else:
                            row = row[:n_cols]

                        normalized_fixed_rows.append(row)

                    fixed_rows = normalized_fixed_rows

                    df = pd.DataFrame(fixed_rows, columns=header)

                    # Recover real columns when PDF extraction places multiple
                    # pipe-delimited fields inside a single cell.
                    df = _split_embedded_pipe_columns(df)

                    df["__source_page"] = page_num
                    df["__table_index"] = t_index
                    all_tables.append(df)

        if not all_tables:
            logger.warning("No tables detected in PDF. Falling back to raw text extraction.")
            return None

        return all_tables

    def group_and_merge_tables(self, tables):
        """
        Group extracted table fragments into logical tables.

        Strategy:
        1. Preserve the original extraction order.
        2. Use meaningful headers as strong evidence that a new logical
        table has started.
        3. Repeated versions of the same header continue the existing
        logical table.
        4. Generic/headerless fragments are attached to the most recent
        compatible logical table with the same number of columns.
        5. A new meaningful header with the same column count but different
        semantics starts a new logical table.

        This avoids blindly merging every table that happens to have the
        same number of columns.
        """

        def is_generic_header(name):
            """
            Detect headers that carry no semantic information.
            """
            if name is None:
                return True

            text = str(name).strip().lower()

            if not text:
                return True

            generic_patterns = (
                r"^col_\d+$",
                r"^column_\d+$",
                r"^unnamed:?\s*\d*$",
            )

            return any(
                re.match(pattern, text)
                for pattern in generic_patterns
            )

        def normalize_header(name):
            """
            Normalize a header for comparison only.
            """
            if name is None:
                return ""

            text = str(name).replace("\n", " ").strip()
            text = re.sub(r"\s+", " ", text)
            return text.lower()

        def get_real_columns(df):
            return [
                c
                for c in df.columns
                if not str(c).startswith("__")
            ]

        def get_header_signature(df):
            """
            Return the normalized header signature for a table.
            """
            columns = get_real_columns(df)

            return tuple(
                normalize_header(c)
                for c in columns
            )

        def has_meaningful_header(df):
            """
            Decide whether the table's current column names contain useful
            semantic header information.

            A header is considered meaningful when at least half of its
            columns are non-generic.
            """
            columns = get_real_columns(df)

            if not columns:
                return False

            meaningful_count = sum(
                1
                for c in columns
                if not is_generic_header(c)
            )

            return meaningful_count >= max(
                1,
                len(columns) * 0.5
            )

        def header_similarity(sig_a, sig_b):
            """
            Compare two header signatures by position.

            Returns a value between 0 and 1.
            """
            if not sig_a or not sig_b:
                return 0.0

            if len(sig_a) != len(sig_b):
                return 0.0

            matches = sum(
                1
                for a, b in zip(sig_a, sig_b)
                if a == b and a != ""
            )

            return matches / len(sig_a)

        # ---------------------------------------------------------------
        # We now build logical groups in extraction order.
        # ---------------------------------------------------------------
        logical_groups = []

        for df in tables:
            if df is None or df.empty:
                continue

            real_columns = get_real_columns(df)

            if not real_columns:
                continue

            column_count = len(real_columns)
            meaningful = has_meaningful_header(df)
            signature = get_header_signature(df)

            # -----------------------------------------------------------
            # Case 1: meaningful header.
            #
            # Search existing groups from newest to oldest. A strong
            # signature match means this is probably a continuation of the
            # same logical table on another page.
            # -----------------------------------------------------------
            if meaningful:
                matched_group = None

                for group in reversed(logical_groups):
                    if group["column_count"] != column_count:
                        continue

                    if group["header_signature"] is None:
                        continue

                    similarity = header_similarity(
                        signature,
                        group["header_signature"]
                    )

                    if similarity >= 0.5:
                        matched_group = group
                        break

                if matched_group is not None:
                    matched_group["dfs"].append(df)
                    continue

                # No compatible existing group: this begins a new logical
                # table.
                logical_groups.append({
                    "column_count": column_count,
                    "header_signature": signature,
                    "canonical_header": [
                        str(c).strip()
                        for c in real_columns
                    ],
                    "dfs": [df],
                })

                continue

            # -----------------------------------------------------------
            # Case 2: generic/headerless fragment.
            #
            # Attach it to the most recent logical table having the same
            # number of columns.
            #
            # This is important for PDFs where pdfplumber creates a
            # one-row/table fragment containing data but no real header.
            # -----------------------------------------------------------
            matched_group = None

            for group in reversed(logical_groups):
                if group["column_count"] != column_count:
                    continue

                matched_group = group
                break

            if matched_group is not None:
                matched_group["dfs"].append(df)
                continue

            # No compatible table seen yet. Keep it as its own group.
            logical_groups.append({
                "column_count": column_count,
                "header_signature": None,
                "canonical_header": None,
                "dfs": [df],
            })

        # ---------------------------------------------------------------
        # Convert logical groups into output DataFrames.
        # ---------------------------------------------------------------
        merged = {}

        for i, group in enumerate(logical_groups, start=1):
            dfs = group["dfs"]

            if not dfs:
                continue

            label = (
                f"table_group_{i}"
                if len(logical_groups) > 1
                else self.base_name
            )

            canonical_header = group["canonical_header"]

            renamed = []

            for df in dfs:
                data_part = df.drop(
                    columns=[
                        "__source_page",
                        "__table_index"
                    ],
                    errors="ignore"
                ).copy()

                if data_part.empty:
                    continue

                n_cols = data_part.shape[1]

                # -------------------------------------------------------
                # Use the meaningful canonical header whenever one exists.
                # -------------------------------------------------------
                if canonical_header is not None:
                    if len(canonical_header) == n_cols:
                        data_part.columns = canonical_header
                    else:
                        data_part.columns = [
                            canonical_header[j]
                            if j < len(canonical_header)
                            else f"column_{j + 1}"
                            for j in range(n_cols)
                        ]

                else:
                    data_part.columns = [
                        f"col_{j + 1}"
                        for j in range(n_cols)
                    ]

                # -------------------------------------------------------
                # Preserve extraction metadata.
                # -------------------------------------------------------
                for meta_col in [
                    "__source_page",
                    "__table_index"
                ]:
                    if meta_col in df.columns:
                        data_part[meta_col] = df[meta_col].values

                renamed.append(data_part)

            if not renamed:
                continue

            merged[label] = pd.concat(
                renamed,
                ignore_index=True,
                sort=False
            )

        return merged

    # -----------------------------------------------------------------
    # Strategy 2: text fallback using word POSITIONS, not whitespace splitting
    # -----------------------------------------------------------------

    def extract_text_fallback(self):
        """
        Clusters words into columns using x-coordinates from
        pdfplumber's word bounding boxes, rather than splitting on
        whitespace runs - whitespace splitting drifts row to row and
        does not guarantee column N means the same thing across rows.
        """
        all_rows = []
        col_boundaries = None

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
                if not words:
                    continue

                if col_boundaries is None:
                    col_boundaries = self._infer_column_boundaries(words)

                lines = {}
                for w in words:
                    line_key = round(w["top"] / 3)
                    lines.setdefault(line_key, []).append(w)

                logical_rows = []

                for line_key in sorted(lines.keys()):
                    line_words = sorted(
                        lines[line_key],
                        key=lambda w: w["x0"]
                    )

                    row = self._assign_words_to_columns(
                        line_words,
                        col_boundaries
                    )

                    if not any(
                        str(cell).strip()
                        for cell in row
                    ):
                        continue

                    # ---------------------------------------------------
                    # Determine whether this physical PDF line is a
                    # continuation of the previous logical row.
                    # ---------------------------------------------------
                    if logical_rows:
                        previous_row = logical_rows[-1]

                        if self._is_probable_continuation_row(
                            row,
                            previous_row
                        ):
                            logical_rows[-1] = (
                                self._merge_continuation_row(
                                    previous_row,
                                    row
                                )
                            )
                            continue

                    logical_rows.append(row)

                all_rows.extend(logical_rows)

        if not all_rows:
            return None

        n_cols = max(len(r) for r in all_rows)
        padded = [r + [""] * (n_cols - len(r)) for r in all_rows]
        columns = [f"col_{i+1}" for i in range(n_cols)]
        return pd.DataFrame(padded, columns=columns)

    @staticmethod
    def _infer_column_boundaries(words):
        """
        Infer column boundaries from the actual horizontal layout of the PDF.

        Unlike the old implementation, this does not assume that a page has
        exactly six equally-sized columns.

        The method:
            1. Collects word x-positions.
            2. Groups nearby left-edge positions into clusters.
            3. Looks for meaningful horizontal gaps between clusters.
            4. Places column boundaries in the middle of those gaps.

        This is intended for the text-extraction fallback, where the PDF did
        not provide reliable table cells but word coordinates are available.
        """

        if not words:
            return [0]

        # ---------------------------------------------------------------
        # Collect x-coordinates of word starts.
        # ---------------------------------------------------------------
        x_positions = sorted(
            float(w["x0"])
            for w in words
            if "x0" in w
        )

        if not x_positions:
            return [0]

        # ---------------------------------------------------------------
        # Remove extremely close duplicate positions.
        #
        # A large number of words often begin at exactly the same x-position
        # because they belong to the same column.
        # ---------------------------------------------------------------
        position_clusters = []

        tolerance = 12.0

        current_cluster = [x_positions[0]]

        for x in x_positions[1:]:
            if x - current_cluster[-1] <= tolerance:
                current_cluster.append(x)
            else:
                position_clusters.append(current_cluster)
                current_cluster = [x]

        position_clusters.append(current_cluster)

        # Represent each cluster by its median x-position.
        anchors = []

        for cluster in position_clusters:
            cluster_sorted = sorted(cluster)
            middle = len(cluster_sorted) // 2

            if len(cluster_sorted) % 2 == 0:
                anchor = (
                    cluster_sorted[middle - 1]
                    + cluster_sorted[middle]
                ) / 2
            else:
                anchor = cluster_sorted[middle]

            anchors.append(anchor)

        anchors = sorted(anchors)

        if len(anchors) <= 1:
            min_x = min(w["x0"] for w in words)
            max_x = max(w["x1"] for w in words)
            return [min_x, max_x + 1]

        # ---------------------------------------------------------------
        # Find meaningful gaps between x-position clusters.
        #
        # Tiny gaps generally occur between words inside the same logical
        # column. Large gaps are more likely to represent column separation.
        # ---------------------------------------------------------------
        gaps = []

        for i in range(len(anchors) - 1):
            gap = anchors[i + 1] - anchors[i]

            gaps.append({
                "left": anchors[i],
                "right": anchors[i + 1],
                "gap": gap,
            })

        if not gaps:
            min_x = min(w["x0"] for w in words)
            max_x = max(w["x1"] for w in words)
            return [min_x, max_x + 1]

        # ---------------------------------------------------------------
        # Determine a dynamic threshold for a meaningful column gap.
        #
        # We use both an absolute minimum and a relative threshold so that
        # the algorithm works on different page sizes.
        # ---------------------------------------------------------------
        sorted_gap_values = sorted(
            gap_info["gap"]
            for gap_info in gaps
        )

        median_gap = sorted_gap_values[
            len(sorted_gap_values) // 2
        ]

        gap_threshold = max(
            18.0,
            median_gap * 2.5
        )

        significant_gaps = [
            gap_info
            for gap_info in gaps
            if gap_info["gap"] >= gap_threshold
        ]

        # ---------------------------------------------------------------
        # If there are too many candidate boundaries, retain only the
        # strongest gaps. This prevents normal spaces between words from
        # becoming dozens of fake columns.
        # ---------------------------------------------------------------
        if len(significant_gaps) > 20:
            significant_gaps = sorted(
                significant_gaps,
                key=lambda item: item["gap"],
                reverse=True
            )[:20]

        significant_gaps = sorted(
            significant_gaps,
            key=lambda item: item["left"]
        )

        # ---------------------------------------------------------------
        # Build actual boundaries.
        # ---------------------------------------------------------------
        min_x = min(
            float(w["x0"])
            for w in words
        )

        max_x = max(
            float(w["x1"])
            for w in words
        )

        boundaries = [min_x]

        for gap_info in significant_gaps:
            boundary = (
                gap_info["left"]
                + gap_info["right"]
            ) / 2

            # Avoid duplicate / almost-identical boundaries.
            if boundary - boundaries[-1] >= 5:
                boundaries.append(boundary)

        boundaries.append(max_x + 1)

        return boundaries

    @staticmethod
    def _assign_words_to_columns(line_words, boundaries):
        """
        Assign words to columns using their actual horizontal position.

        Words are assigned based on the center of their bounding box rather
        than only their left edge. This is more stable for numeric cells and
        right-aligned values.
        """

        n_cols = max(
            len(boundaries) - 1,
            1
        )

        row = [""] * n_cols

        for word in line_words:
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            # Use the horizontal center of the word. This reduces incorrect
            # assignments when a word begins very close to a boundary.
            center_x = (x0 + x1) / 2

            col_idx = n_cols - 1

            for i in range(len(boundaries) - 1):
                left = boundaries[i]
                right = boundaries[i + 1]

                if left <= center_x < right:
                    col_idx = i
                    break

            col_idx = min(
                max(col_idx, 0),
                n_cols - 1
            )

            text = str(
                word.get("text", "")
            ).strip()

            if not text:
                continue

            if row[col_idx]:
                row[col_idx] += " " + text
            else:
                row[col_idx] = text

        return row

    @staticmethod
    def _is_probable_continuation_row(
        current_row,
        previous_row,
        first_column_index=0
    ):
        """
        Determine whether the current physical row is probably a
        continuation of the previous logical row.

        This is intentionally conservative.

        A row is more likely to be a continuation when:
          - it contains useful text,
          - it does not appear to start a new record,
          - it has text concentrated in non-key columns,
          - and the previous row already contains meaningful data.

        This is a structural heuristic, not a document-specific parser.
        """

        if not current_row or not previous_row:
            return False

        current = [
            str(cell).strip()
            if cell is not None
            else ""
            for cell in current_row
        ]

        previous = [
            str(cell).strip()
            if cell is not None
            else ""
            for cell in previous_row
        ]

        current_non_empty = [
            i
            for i, value in enumerate(current)
            if value
        ]

        previous_non_empty = [
            i
            for i, value in enumerate(previous)
            if value
        ]

        if not current_non_empty or not previous_non_empty:
            return False

        # -----------------------------------------------------------
        # If the current row begins with a strong record identifier
        # (for example a serial number), it is more likely to be a new
        # record rather than a continuation.
        # -----------------------------------------------------------
        first_value = current[first_column_index]

        if first_value:
            normalized_first = first_value.strip()

            if re.fullmatch(
                r"\d{1,6}",
                normalized_first
            ):
                return False

        # -----------------------------------------------------------
        # A continuation row often has data only in later columns
        # while the identifying columns at the beginning are blank.
        # -----------------------------------------------------------
        leading_blank_count = 0

        for value in current:
            if value:
                break
            leading_blank_count += 1

        if leading_blank_count > 0:
            return True

        # -----------------------------------------------------------
        # If the current row has only one or two populated cells and
        # the previous row is substantially populated, the current row
        # is often wrapped text.
        # -----------------------------------------------------------
        if (
            len(current_non_empty) <= 2
            and len(previous_non_empty) >= 2
        ):
            return True

        return False
    
    @staticmethod
    def _merge_continuation_row(
        previous_row,
        continuation_row
    ):
        """
        Merge a physical continuation row into the previous logical row.

        Text is appended to the corresponding column rather than
        replacing existing content.
        """

        merged = []

        max_len = max(
            len(previous_row),
            len(continuation_row)
        )

        for i in range(max_len):
            previous_value = (
                previous_row[i]
                if i < len(previous_row)
                else ""
            )

            continuation_value = (
                continuation_row[i]
                if i < len(continuation_row)
                else ""
            )

            previous_text = (
                str(previous_value).strip()
                if previous_value is not None
                else ""
            )

            continuation_text = (
                str(continuation_value).strip()
                if continuation_value is not None
                else ""
            )

            if previous_text and continuation_text:
                merged.append(
                    f"{previous_text} {continuation_text}"
                )
            elif previous_text:
                merged.append(previous_text)
            else:
                merged.append(continuation_text)

        return merged

    # -----------------------------------------------------------------
    # Strategy 3: OCR fallback
    # -----------------------------------------------------------------

    def extract_text_ocr(self, dpi: int = 200):
        """
        Catches broad Exception, not just ImportError - missing system
        binaries (tesseract, poppler) raise OSError/EnvironmentError
        even when the Python packages themselves are installed fine.
        """
        try:
            import pytesseract
            from pdf2image import convert_from_path
        except ImportError:
            logger.error("OCR fallback requires pytesseract and pdf2image. "
                         "Install with: pip install pytesseract pdf2image")
            return None

        try:
            images = convert_from_path(self.pdf_path, dpi=dpi)
        except Exception as e:
            logger.error(f"OCR fallback failed - likely missing system binary "
                         f"(poppler/tesseract): {e}")
            return None

        rows = []
        try:
            for image in images:
                text = pytesseract.image_to_string(image)
                for line in text.split("\n"):
                    line = line.strip()
                    if line:
                        rows.append([line])
        except Exception as e:
            logger.error(f"OCR text extraction failed: {e}")
            return None

        if not rows:
            return None
        return pd.DataFrame(rows, columns=["extracted_text"])
  
    # -----------------------------------------------------------------
    # Cleaning
    # -----------------------------------------------------------------
    

    def clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        

        if df is None or df.empty:
            return df

        df = df.map(lambda x: " ".join(str(x).split()) if isinstance(x, str) else x)
        

        df = df.replace(["", "None", "none", "nan", "NaN", "N/A", "n/a"], pd.NA)
        df = df.dropna(axis=0, how="all")
        df = df.dropna(axis=1, how="all")
        # Remove PDF formatting artifacts
        df = df[
            ~df.apply(_is_separator_row, axis=1)
        ]

        df = df[
            ~df.apply(_is_page_marker, axis=1)
        ]

        if len(df.columns) > 0:
            header_row_mask = df.apply(
                lambda row: list(row.astype(str)) == list(df.columns.astype(str)),
                axis=1
            )
            df = df[~header_row_mask]

        df.columns = _clean_header_names(df.columns)

        df = df.map(sanitize_for_csv)
        df = df.reset_index(drop=True)
        return df

    @staticmethod
    def _quality_score(df: pd.DataFrame) -> float:
        """
        0-1 score used to reject near-useless extraction results.
        Two DISTINCT failure modes, handled separately:
          1. No real (non-meta) columns at all -> 0.0, reject.
          2. Real columns exist but are all/mostly blank (the
             Computation_of_Income.pdf case: pdfplumber found "tables"
             with zero actual cell content) -> low score, reject.
        A single real column FULL OF TEXT (the ledger day-book case,
        where the whole row is one merged text cell) is valid data and
        must NOT be penalized just for having one column -- content
        density is what matters, not column count.
        """
        if df is None or df.empty:
            return 0.0

        real_cols = [c for c in df.columns if not str(c).startswith("__")]
        if not real_cols:
            return 0.0

        real_df = df[real_cols]
        non_empty = real_df.notna().sum().sum()
        total = real_df.shape[0] * real_df.shape[1]
        return non_empty / total if total else 0.0

    # -----------------------------------------------------------------
    # Orchestration
    # -----------------------------------------------------------------

    def is_ledger_document(self) -> bool:
        """
        Evidence-based detector for accounting ledger PDFs. Thin wrapper
        around the module-level check_is_ledger_document() (pulled out
        2026-10-04 so the generic document classifier in
        classify_document.py can call this same, already-proven check
        without instantiating PDFToCSVConverter -- that constructor's
        own os.makedirs(output_dir) side effect is wrong for a read-only
        classification question with nothing to write anywhere).
        """
        return check_is_ledger_document(self.pdf_path)

    def convert_to_csv(self) -> "str | list[str]":
        """
        Tries ledger reconstruction directly if document is an accounting ledger;
        otherwise tries table extraction -> text fallback -> OCR, in order,
        keeping whichever result clears a basic quality bar. If table
        extraction finds structurally distinct tables (e.g. 26AS Part I
        vs Part II), writes ONE CSV PER GROUP and returns a list of
        paths instead of a single path.
        """
        logger.info(f"Starting PDF-to-CSV conversion: {self.pdf_path}")

        # Strategy 0: Dedicated accounting ledger reconstruction path
        if self.is_ledger_document():
            logger.info(f"Accounting ledger detected: {self.pdf_path}. Routing to dedicated reconstruct_ledger_entries path.")
            from reconstruct_ledger_entries import collect_occurrences, merge_occurrences, write_output

            with pdfplumber.open(self.pdf_path) as pdf:
                total_pages = len(pdf.pages)

            occurrences, state_events, unresolved = collect_occurrences(self.pdf_path, 1, total_pages)
            entries, entry_order, conflicts = merge_occurrences(occurrences)
            all_unresolved = unresolved + conflicts

            out_prefix = os.path.join(self.output_dir, f"{self.base_name}_reconstructed")
            entries_path, unres_path, state_path = write_output(
                entries, entry_order, all_unresolved, state_events, out_prefix
            )
            logger.info(f"Ledger conversion complete: {entries_path}, {unres_path}, {state_path}")
            return [entries_path, unres_path, state_path]

        tables = self.extract_tables()
        if tables:
            groups = self.group_and_merge_tables(tables)
            output_paths = []
            for label, df in groups.items():
                df = self.clean_dataframe(df)
                if df.empty:
                    continue
                score = self._quality_score(df)
                if score < 0.15:
                    logger.warning(
                        f"Group '{label}' failed quality check (score {score:.2f}). "
                        f"Quarantining flagged extraction rather than silently dropping."
                    )
                    safe_label = f"{self.base_name}__{label}_quarantined"
                    path = os.path.join(self.output_dir, f"{safe_label}.csv")
                    df_out = prepare_final_csv(df)
                    df_out.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
                    output_paths.append(path)
                    continue

                # Namespace every output file by the source PDF's own
                # name. Without this, running the converter on a SECOND
                # pdf silently overwrites table_group_1.csv etc. from a
                # PREVIOUS pdf -- a real data-loss bug, not a hypothetical
                # one (it happened: 26AS deductor data got overwritten
                # by an unrelated document's empty output).
                safe_label = f"{self.base_name}__{label}"
                path = os.path.join(self.output_dir, f"{safe_label}.csv")
                df = prepare_final_csv(df)
                df.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
                logger.info(f"Saved table group '{safe_label}' -> {path}")
                output_paths.append(path)

            if output_paths:
                return output_paths[0] if len(output_paths) == 1 else output_paths
            logger.warning("All table groups failed quality check. Falling back to text extraction.")

        # Strategy 2
        df = self.extract_text_fallback()
        method = "text_fallback"

        if df is None or self._quality_score(self.clean_dataframe(df.copy())) < 0.1:
            logger.info("Text fallback insufficient. Attempting OCR...")
            ocr_df = self.extract_text_ocr()
            if ocr_df is not None:
                df = ocr_df
                method = "ocr_fallback"

        if df is None:
            raise RuntimeError(
                f"Failed to extract any usable data from PDF: {self.pdf_path}. "
                f"Tried table extraction, text fallback, and OCR."
            )

        df = self.clean_dataframe(df)
        if df.empty:
            raise RuntimeError(f"Extraction produced only empty/junk data for: {self.pdf_path}")

        df.to_csv(self.output_csv_path, index=False, quoting=csv.QUOTE_MINIMAL)
        logger.info(f"Conversion successful via '{method}'. Saved to: {self.output_csv_path}")
        return self.output_csv_path

def prepare_final_csv(df):
    # """
    # Final cleanup specifically for human-readable CSV output.
    # Removes internal extraction metadata and guarantees clean columns.
    # """
    if df is None or df.empty:
        return df

    df = df.copy()

    # Remove internal metadata from final CSV
    df = df.drop(
        columns=[
            "__source_page",
            "__table_index"
        ],
        errors="ignore"
    )

    # Clean column names
    df.columns = _clean_header_names(df.columns)

    # Remove completely empty rows/columns
    df = df.dropna(axis=0, how="all")
    df = df.dropna(axis=1, how="all")

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------
# Excel handling (multi-sheet aware)
# ---------------------------------------------------------------------

def _convert_excel_to_csv(file_path: str, output_dir: str) -> "str | list[str]":
    """
    Reads ALL sheets (pd.read_excel defaults to the first sheet only,
    which silently drops data on multi-sheet ledger exports). Writes
    one CSV per sheet if there's more than one. .xls (old format)
    needs the `xlrd` package installed.
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    try:
        sheets = pd.read_excel(file_path, sheet_name=None)
    except ImportError as e:
        raise RuntimeError(
            f"Reading '{file_path}' failed - for legacy .xls files you need: "
            f"pip install xlrd. Original error: {e}"
        )

    if len(sheets) == 1:
        (_, df), = sheets.items()
        path = os.path.join(output_dir, f"{base_name}.csv")
        df.to_csv(path, index=False)
        return path

    paths = []
    for sheet_name, df in sheets.items():
        safe_sheet = re.sub(r"[^A-Za-z0-9_-]+", "_", sheet_name)
        path = os.path.join(output_dir, f"{base_name}__{safe_sheet}.csv")
        df.to_csv(path, index=False)
        paths.append(path)
    logger.info(f"Multi-sheet Excel detected ({len(sheets)} sheets) - wrote {len(paths)} CSVs.")
    return paths


# ---------------------------------------------------------------------
# Universal entry points
# ---------------------------------------------------------------------

def ensure_csv_input(file_path: str, output_dir: str = "converted_csv") -> "str | list[str]":
    """
    Guarantees CSV output for .csv / .xlsx / .xls / .pdf input.
    NOTE: this still RAISES RuntimeError on unrecoverable failure - it
    standardizes the error, it does not suppress it. If your pipeline
    requires "never throw, ever," call safe_ensure_csv_input() instead.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".csv":
            logger.info("Input is already CSV. No conversion needed.")
            return file_path

        elif ext in (".xlsx", ".xls"):
            logger.info("Excel file detected. Converting to CSV.")
            return _convert_excel_to_csv(file_path, output_dir)

        elif ext == ".pdf":
            logger.info("PDF detected. Routing to sub-model for conversion.")
            converter = PDFToCSVConverter(file_path, output_dir=output_dir)
            return converter.convert_to_csv()

        else:
            raise ValueError(f"Unsupported file type '{ext}'. Expected .csv, .xlsx, .xls, or .pdf.")

    except Exception as e:
        logger.error(f"File conversion failed for {file_path}: {e}")
        raise RuntimeError(f"Could not prepare '{file_path}' for processing. Reason: {e}") from e


def safe_ensure_csv_input(file_path: str, output_dir: str = "converted_csv"):
    """
    Non-raising version for pipelines that must never crash on a bad
    input file. Returns (result, error):
        success: (csv_path_or_list, None)
        failure: (None, error_message_string)
    """
    try:
        result = ensure_csv_input(file_path, output_dir=output_dir)
        return result, None
    except Exception as e:
        logger.error(f"safe_ensure_csv_input caught failure for {file_path}: {e}")
        return None, str(e)


if __name__ == "__main__":
    import sys

    DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
        result, error = safe_ensure_csv_input(input_file, output_dir=DATA_DIR)
        if error:
            print(f"FAILED: {error}")
        else:
            print(f"Ready for processing: {result}")
    else:
        print("Usage: python pdf_to_csv_converter.py <path_to_file>")