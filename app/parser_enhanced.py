#!/usr/bin/env python3
"""
FINAL FULLY ROBUST CREDIT CARD STATEMENT PARSER
------------------------------------------------------------
✓ Extracts all major details (Issuer, Customer, Card Last 4, Card Type, Dates, Amounts)
✓ Works even if data spans multiple lines or has extra spaces
✓ Handles 'Statement for:', 'Card No', etc.

OUTPUT KEYS:
  issuer, customer_name, card_last4, card_type,
  billing_cycle, payment_due_date, total_amount_due, transactions_preview
"""

import os, re, json
from dateutil import parser as dateparse

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
    "HDFC": {"keywords": ["hdfc"], "due_labels": ["payment due date", "due date"], "total_labels": ["total amount due", "amount due", "amount payable"]},
    "ICICI": {"keywords": ["icici"], "due_labels": ["payment due date", "due date"], "total_labels": ["total amount due", "total balance", "amount due"]},
    "SBI": {"keywords": ["sbi", "state bank of india"], "due_labels": ["payment due date", "due date"], "total_labels": ["total amount due", "amount due"]},
    "AXIS": {"keywords": ["axis"], "due_labels": ["payment due date", "due date"], "total_labels": ["total amount due", "amount payable", "amount due"]},
    "KOTAK": {"keywords": ["kotak"], "due_labels": ["payment due date", "due date"], "total_labels": ["total amount due", "amount due", "current due"]},
}

# Regex for matching all types of amounts and numbers that may split across lines
RE_AMOUNT = re.compile(r"(₹|Rs\.?|INR|[$€£])?\s*\d[\d,\s\n]*(?:\.\d{2})?", re.IGNORECASE)

# Dates
RE_DATE = re.compile(r"\b(?:\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{0,4}|[A-Za-z]{3,9}\s+\d{4})\b")

# Card number patterns (extremely flexible)
RE_LAST4 = re.compile(
    r"(?:card\s*(?:no\.?|number|ending|ending\s*in|xx+)\s*[:\-]?\s*(?:x{2,}\s*){0,3}(\d{4})|\b(\d{4})\b)",
    re.IGNORECASE | re.MULTILINE
)

# Name patterns
RE_NAME_PATTERNS = [
    r"Statement\s*for\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.\']+)",
    r"Customer\s*Name\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.\']+)",
    r"Cardholder\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.\']+)",
    r"Name\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.\']+)"
]


# --------------------------- UTILITIES -----------------------------
def extract_text(path):
    """Extracts clean text from PDF (PyMuPDF → pdfplumber → fallback)"""
    txt = ""
    if HAS_FITZ:
        try:
            with fitz.open(path) as doc:
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


def clean_text(txt: str) -> str:
    """Unify whitespace and fix numeric breaks like 12\n543.89"""
    txt = re.sub(r"\r", "\n", txt)
    txt = re.sub(r"(\d+)\s*\n\s*(\d{3}\.\d{2})", r"\1,\2", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt


def detect_issuer(text):
    lower = text.lower()
    for name, conf in ISSUERS.items():
        if any(k in lower for k in conf["keywords"]):
            return name
    return "UNKNOWN"


def find_customer_name(text):
    for pat in RE_NAME_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            name = re.split(r"statement|period|account|number|no\.?", name, flags=re.IGNORECASE)[0].strip()
            return name
    return None


def find_last4(text):
    """Find last 4 digits flexibly"""
    matches = RE_LAST4.findall(text)
    for m in matches:
        digits = m[0] or m[1]
        if digits and digits.isdigit() and not (1900 <= int(digits) <= 2100):
            return digits[-4:]
    return None


def find_label_value(text, labels):
    """Get numeric value near given label (handles Rs. and line breaks)"""
    lower = text.lower()
    for lbl in labels:
        idx = lower.find(lbl)
        if idx != -1:
            window = text[idx: idx + 250]
            m = RE_AMOUNT.search(window)
            if m:
                amt = m.group(0)
                amt = re.sub(r"[\s\n]", "", amt)
                amt = amt.replace("Rs.", "").replace("INR", "").replace("₹", "").replace(",", "")
                try:
                    val = float(re.findall(r"\d+\.\d{2}|\d+", amt)[0])
                    return f"{val:,.2f}"
                except Exception:
                    return amt
    return None


def find_due_date_near_label(text, labels):
    """Finds a realistic date near labels"""
    lower = text.lower()
    for lbl in labels:
        idx = lower.find(lbl)
        if idx != -1:
            window = text[idx: idx + 200]
            for d in RE_DATE.findall(window):
                try:
                    parsed = dateparse.parse(d, fuzzy=True, dayfirst=True)
                    if parsed.year >= 2020:
                        return parsed.strftime("%d-%b-%Y")
                except Exception:
                    pass
    return None


def find_billing_cycle(text):
    """Finds two dates near 'Statement Period' or similar"""
    for label in ["statement period", "billing period", "statement date", "statement cycle"]:
        idx = text.lower().find(label)
        if idx != -1:
            window = text[idx: idx + 250]
            dates = RE_DATE.findall(window)
            if len(dates) >= 2:
                try:
                    s = dateparse.parse(dates[0], fuzzy=True, dayfirst=True)
                    e = dateparse.parse(dates[1], fuzzy=True, dayfirst=True)
                    return {"start": s.strftime("%d-%b-%Y"), "end": e.strftime("%d-%b-%Y")}
                except Exception:
                    pass
    return None


def extract_transactions_simple(text, max_lines=200):
    """Simple heuristic to list some transactions"""
    lines = []
    for ln in text.split("\n"):
        if RE_DATE.search(ln) and RE_AMOUNT.search(ln):
            lines.append(ln.strip())
            if len(lines) >= max_lines:
                break
    return lines


# --------------------------- MAIN -----------------------------
def parse_statement(path):
    txt = extract_text(path)
    txt = clean_text(txt)

    issuer = detect_issuer(txt)
    customer_name = find_customer_name(txt)
    last4 = find_last4(txt)
    billing = find_billing_cycle(txt)

    conf = ISSUERS.get(issuer, {})
    due_date = find_due_date_near_label(txt, conf.get("due_labels", []))
    total_due = find_label_value(txt, conf.get("total_labels", []))

    if not due_date:
        due_date = find_due_date_near_label(txt, ["payment due date", "due date", "pay by"])
    if not total_due:
        total_due = find_label_value(txt, ["total amount due", "amount due", "total due", "new balance", "amount payable"])

    card_type = None
    for t in ["Platinum", "Gold", "Classic", "Signature", "World", "Visa", "Mastercard", "Titanium", "Infinite"]:
        if t.lower() in txt.lower():
            card_type = t
            break

    transactions = extract_transactions_simple(txt)

    return {
        "file": os.path.basename(path),
        "issuer": issuer,
        "customer_name": customer_name,
        "card_last4": last4,
        "card_type": card_type,
        "billing_cycle": billing,
        "payment_due_date": due_date,
        "total_amount_due": total_due,
        "transactions_preview": transactions[:20],
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="PDF file path")
    args = p.parse_args()
    print(json.dumps(parse_statement(args.input), indent=2))
