#!/usr/bin/env python3
"""
FINAL ENHANCED CREDIT CARD STATEMENT PARSER
-------------------------------------------
✓ Fixes issue where only '543.89' was extracted (handles '12\n543.89' cases)
✓ Works on multiline amounts and different layouts
✓ Supports HDFC, ICICI, SBI, AXIS, KOTAK
✓ Extracts issuer, last4, card type, billing cycle, due date, total due, and transactions
"""

import os, re, json
from dateutil import parser as dateparse

# --------------------------- LIBRARIES ---------------------------
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except Exception:
    HAS_PDFPLUMBER = False


# --------------------------- CONFIG -----------------------------
ISSUERS = {
    "HDFC": {
        "keywords": ["hdfc", "hdfc bank"],
        "due_labels": ["payment due date", "payment due"],
        "total_labels": ["total amount due", "amount due", "total due", "amount payable"],
    },
    "ICICI": {
        "keywords": ["icici", "icici bank"],
        "due_labels": ["payment due date", "due date"],
        "total_labels": ["total amount due", "total balance", "amount due"],
    },
    "SBI": {
        "keywords": ["state bank of india", "sbi", "sbi card"],
        "due_labels": ["payment due date", "due date"],
        "total_labels": ["total amount due", "amount due"],
    },
    "AXIS": {
        "keywords": ["axis", "axis bank"],
        "due_labels": ["payment due date", "due date", "payment due"],
        "total_labels": ["total amount due", "amount payable", "amount due", "total due"],
    },
    "KOTAK": {
        "keywords": ["kotak", "kotak mahindra"],
        "due_labels": ["payment due date", "due date"],
        "total_labels": ["total amount due", "amount due", "current due", "total due"],
    },
}


RE_LAST4 = re.compile(r"(?:ending in|xxxx\s*\d{4}|x{2,}\s*\d{4}|card\s*no[:\s]*\*+(\d{4})|(\d{4})\b)", re.IGNORECASE)
RE_DATE = re.compile(r"\b(?:\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{0,4}|[A-Za-z]{3,9}\s+\d{4})\b")

# Tolerate line breaks between digits (e.g. "12\n543.89")
RE_AMOUNT = re.compile(r"(₹|Rs\.?|INR|[$€£])?\s*\d[\d,\s\n]*(?:\.\d{2})?")


# --------------------------- UTILITIES -----------------------------
def extract_text(path):
    """Try PyMuPDF → pdfplumber → fallback"""
    if HAS_FITZ:
        try:
            doc = fitz.open(path)
            txt = "\n".join([p.get_text("text") for p in doc])
            if txt.strip():
                return txt
        except Exception:
            pass
    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(path) as pdf:
                txt = "\n".join([p.extract_text() or "" for p in pdf.pages])
                if txt.strip():
                    return txt
        except Exception:
            pass
    try:
        return open(path, "rb").read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def clean_text_multiline_numbers(txt: str) -> str:
    """Join broken numeric lines like '12\\n543.89' → '12,543.89'"""
    return re.sub(r"(\d+)\s*\n\s*(\d{3}\.\d{2})", r"\1,\2", txt)


def detect_issuer(text):
    lower = text.lower()
    for name, conf in ISSUERS.items():
        for k in conf["keywords"]:
            if k in lower:
                return name
    return "UNKNOWN"


def normalize_amount(s):
    if not s:
        return None
    s = s.replace(",", "").replace("₹", "").replace("$", "").replace("€", "").replace("£", "").replace("Rs.", "").replace("INR", "").strip()
    try:
        val = float(re.findall(r"[-\d\.]+", s)[0])
        return f"{val:,.2f}"
    except Exception:
        return s


def find_last4(text):
    for m in RE_LAST4.finditer(text):
        g = m.group(1) or m.group(2)
        if g and not (1900 <= int(g) <= 2100):
            return g
    return None


def find_billing_cycle(text):
    for label in ["statement period", "billing period", "statement date", "statement cycle"]:
        idx = text.lower().find(label)
        if idx != -1:
            window = text[idx : idx + 200]
            dates = RE_DATE.findall(window)
            if len(dates) >= 2:
                try:
                    s = dateparse.parse(dates[0], fuzzy=True).strftime("%Y-%m-%d")
                    e = dateparse.parse(dates[1], fuzzy=True).strftime("%Y-%m-%d")
                    return {"start": s, "end": e}
                except Exception:
                    return {"start": dates[0], "end": dates[1]}
    return None


def extract_transactions_simple(text, max_lines=200):
    lines = []
    for ln in text.splitlines():
        if RE_DATE.search(ln) and RE_AMOUNT.search(ln):
            lines.append(ln.strip())
            if len(lines) >= max_lines:
                break
    return lines


# --------------------------- FIXED LABEL LOGIC -----------------------------
def find_label_value(text, labels):
    """
    - Looks for a label
    - Handles numbers split across lines (e.g., '12\\n543.89')
    - Returns the full amount including commas and decimals
    """
    lower = text.lower()
    for lbl in labels:
        idx = lower.find(lbl)
        if idx != -1:
            window = text[idx : idx + 300]
            # Match multiline currency patterns
            m = re.search(r"(₹|Rs\.?|INR|[$€£])?\s*\d[\d,\s\n]*(?:\.\d{2})?", window)
            if m:
                raw = m.group(0).replace("\n", "").replace(" ", "")
                # ensure proper comma placement (e.g., 12,543.89)
                cleaned = re.sub(r"(\d)(\d{3}\.\d{2})$", r"\1,\2", raw)
                return cleaned.strip()
    return None


def find_due_date_near_label(text, labels):
    lower = text.lower()
    for lbl in labels:
        idx = lower.find(lbl)
        if idx != -1:
            window = text[idx : idx + 200]
            dates = RE_DATE.findall(window)
            for d in dates:
                try:
                    parsed = dateparse.parse(d, fuzzy=True, dayfirst=True)
                    return parsed.strftime("%d-%b-%Y")
                except Exception:
                    pass
    return None


# --------------------------- MAIN PARSER -----------------------------
def parse_statement(path):
    txt = extract_text(path)
    txt = clean_text_multiline_numbers(txt)

    issuer = detect_issuer(txt)
    last4 = find_last4(txt)
    billing = find_billing_cycle(txt)

    conf = ISSUERS.get(issuer, {})
    due_date = find_due_date_near_label(txt, conf.get("due_labels", []))
    total_due = find_label_value(txt, conf.get("total_labels", []))

    if not due_date:
        due_date = find_due_date_near_label(txt, ["payment due date", "due date"])
    if not total_due:
        total_due = find_label_value(txt, ["total amount due", "amount due", "total due", "current due", "new balance"])
    if total_due:
        total_due = normalize_amount(total_due)

    card_type = None
    for t in ["Platinum","Gold","Classic","Signature","World","Visa","Mastercard","Titanium","Infinite"]:
        if t.lower() in txt.lower():
            card_type = t
            break

    transactions = extract_transactions_simple(txt)

    return {
        "file": os.path.basename(path),
        "issuer": issuer,
        "card_last4": last4,
        "card_type": card_type,
        "billing_cycle": billing,
        "payment_due_date": due_date,
        "total_amount_due": total_due,
        "transactions_preview": transactions[:20],
    }


# --------------------------- CLI -----------------------------
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="PDF file path")
    args = p.parse_args()
    res = parse_statement(args.input)
    print(json.dumps(res, indent=2))
