"""
parse_trial_balance.py
-----------------------
Parses data/raw_pdfs/trial_balance_redacted.pdf (the partnership firm's
official Tally trial balance, 1-Apr-24 to 31-Mar-25 -- same FY as the
reconstructed ledger) into structured rows: account name, opening balance,
transactions debit, transactions credit, closing balance (each with its
Dr/Cr sign where Tally prints one).

Column layout is fixed-width in the PDF but NOT reliably separated by
pdfplumber's extract_text() -- wrapped-continuation rows (wider numbers,
e.g. more digits) shift a number's LEFT edge (x0) enough to spill into the
neighboring column's x0 bucket. Right edges (x1) are stable for right-
aligned accounting columns regardless of digit count, so bucketing is done
on x1. Column boundaries below were measured directly from the header row's
word positions (see header x1 values: Opening~288, Debit~370, Credit~457,
Closing~547) with margin on each side.

A line with amounts but no label text is a distinct account row whose name
was redacted for privacy (this is a partnership firm; individual partners'
capital accounts are blanked out, not merged into one row) -- NOT a
wrapped continuation of the previous line. It's kept as its own row with
account="" so it's traceable back to its page/position, but it will never
match a general_ledger.csv account name (expected and fine).
"""
import os
import re
import sys

import pdfplumber

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
PDF_PATH = os.path.join(DATA_DIR, "raw_pdfs", "trial_balance_redacted.pdf")

_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")

# (column_name, x1_min, x1_max) -- inclusive/exclusive, measured from the
# header row's word x1 positions with margin.
_COLUMNS = [
    ("opening", 150, 320),
    ("debit", 320, 410),
    ("credit", 410, 490),
    ("closing", 490, 600),
]


def _bucket(x1):
    for name, lo, hi in _COLUMNS:
        if lo <= x1 < hi:
            return name
    return None


def _normalize_amount(text):
    return float(text.replace(",", ""))


def parse_trial_balance(pdf_path=PDF_PATH):
    """Returns a list of row dicts:
        {account, page, opening, opening_sign, debit, credit,
         closing, closing_sign}
    amounts are float (0.0 if the cell was empty/omitted, as Tally omits
    zero cells rather than printing 0.00); *_sign is "Dr"/"Cr"/None.
    """
    rows = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            if not words:
                continue

            # Group words into lines by y-position ("top"), tolerant of the
            # ~1pt jitter pdfplumber sometimes gives same-line tokens.
            lines = []
            for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
                top_bucket = round(w["top"])
                if lines and abs(lines[-1]["top"] - top_bucket) <= 2:
                    lines[-1]["words"].append(w)
                else:
                    lines.append({"top": top_bucket, "words": [w]})

            for line in lines:
                ws = sorted(line["words"], key=lambda w: w["x0"])
                texts = [w["text"] for w in ws]

                # Skip header/title lines
                joined = " ".join(texts)
                if joined in ("Opening Transactions Closing",
                              "Balance Debit Credit Balance") \
                        or joined.startswith("Trial Balance") \
                        or joined.startswith("Page ") \
                        or re.match(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4} to \d{1,2}-[A-Za-z]{3}-\d{2,4}$", joined):
                    continue

                label_tokens = []
                values = {"opening": (0.0, None), "debit": (0.0, None),
                          "credit": (0.0, None), "closing": (0.0, None)}
                i = 0
                seen_amount = False
                while i < len(ws):
                    w = ws[i]
                    if _AMOUNT_RE.match(w["text"]):
                        seen_amount = True
                        col = _bucket(w["x1"])
                        amt = _normalize_amount(w["text"])
                        sign = None
                        if i + 1 < len(ws) and ws[i + 1]["text"] in ("Dr", "Cr") \
                                and ws[i + 1]["x0"] - w["x1"] < 15:
                            sign = ws[i + 1]["text"]
                            i += 1
                        if col:
                            values[col] = (amt, sign)
                        i += 1
                    elif w["text"] in ("Dr", "Cr") and not seen_amount:
                        # stray sign with no preceding amount on this line -- ignore
                        i += 1
                    else:
                        if not seen_amount:
                            label_tokens.append(w["text"])
                        i += 1

                if not seen_amount:
                    # Pure text with no amount token at all (page header noise
                    # like "Jammu" / "E-Mail :") -- not a trial balance row.
                    continue
                account = " ".join(label_tokens).strip()
                # A pure label line with zero amounts is a group header
                # (e.g. "Loans (Liability)" immediately followed by its own
                # single child "Unsecured Loans" line) -- keep it, it's
                # informative, but it won't match a leaf ledger account.
                rows.append({
                    "account": account,
                    "page": page_num,
                    "opening": values["opening"][0], "opening_sign": values["opening"][1],
                    "debit": values["debit"][0],
                    "credit": values["credit"][0],
                    "closing": values["closing"][0], "closing_sign": values["closing"][1],
                })

    return rows


def main():
    rows = parse_trial_balance()
    print(f"Parsed {len(rows)} rows from {PDF_PATH}")
    named = [r for r in rows if r["account"]]
    print(f"  {len(named)} with a (non-redacted) account name")
    print(f"  {len(rows) - len(named)} with no name (redacted partner accounts / continuation rows)")
    print()
    for r in rows[:15]:
        print(f"  p{r['page']:2} {r['account']:30} "
              f"open={r['opening']:>14,.2f}{r['opening_sign'] or '':3} "
              f"debit={r['debit']:>14,.2f} credit={r['credit']:>14,.2f} "
              f"close={r['closing']:>14,.2f}{r['closing_sign'] or '':3}")


if __name__ == "__main__":
    main()
