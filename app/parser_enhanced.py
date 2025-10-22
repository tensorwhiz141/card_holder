#!/usr/bin/env python3
"""
Ultra Robust Credit Card Statement Parser (PDF + ZIP)
-----------------------------------------------------
- Handles single PDFs or ZIP archives with PDFs
- Extracts: Issuer, Customer Name, Card Last 4 Digits, Card Type
- Billing Cycle (unique per bank), Payment Due Date, Total Amount Due
- Transactions preview (top 20)
"""

import os, re, json, random, zipfile, tempfile
from datetime import timedelta
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

# ------------------- CONFIG -------------------
ISSUERS = {
    "HDFC": {"keywords": ["hdfc"], "cycle_days": 30},
    "ICICI": {"keywords": ["icici"], "cycle_days": 35},
    "SBI": {"keywords": ["sbi", "state bank of india"], "cycle_days": 40},
    "AXIS": {"keywords": ["axis"], "cycle_days": 45},
    "KOTAK": {"keywords": ["kotak"], "cycle_days": 60},
}

RE_AMOUNT = re.compile(r"(₹|Rs\.?|INR|[$€£])?\s*\d[\d,\s\n]*(?:\.\d{2})?", re.IGNORECASE)
RE_DATE = re.compile(r"\b(?:\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{0,4}|[A-Za-z]{3,9}\s+\d{4})\b")
RE_LAST4 = re.compile(r"(?:card\s*(?:no\.?|number|ending|ending\s*in|xx+)\s*[:\-]?\s*(?:x{2,}\s*){0,3}(\d{4})|\b(\d{4})\b)", re.IGNORECASE)
RE_NAME_PATTERNS = [
    r"Customer\s*Name\s*[:\-]?\s*([\w\s\.\']{2,60})(?=\s*(?:Card|A/c|Account|Statement|Period|No|Number|$))",
    r"Statement\s*for\s*[:\-]?\s*([\w\s\.\']{2,60})",
    r"Cardholder\s*[:\-]?\s*([\w\s\.\']{2,60})",
    r"Name\s*[:\-]?\s*([\w\s\.\']{2,60})",
]

# ------------------- UTILITIES -------------------
def extract_text(path):
    txt = ""
    if HAS_FITZ:
        try:
            with fitz.open(path) as doc:
                txt = "\n".join(p.get_text("text") for p in doc)
                if txt.strip():
                    return txt
        except Exception:
            pass
    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(path) as pdf:
                txt = "\n".join(p.extract_text() or "" for p in pdf.pages)
                if txt.strip():
                    return txt
        except Exception:
            pass
    try:
        return open(path, "rb").read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

def clean_text(txt):
    txt = re.sub(r"\r", "\n", txt)
    txt = re.sub(r"(\d+)\s*\n\s*(\d{3}\.\d{2})", r"\1,\2", txt)
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"₹\s*₹", "₹", txt)
    return txt

def detect_issuer(text):
    lower = text.lower()
    for name, conf in ISSUERS.items():
        if any(k in lower for k in conf["keywords"]):
            return name
    return "UNKNOWN"

def find_customer_name(text):
    cleaned = re.sub(r"[\r\n]+", " ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"Customer\s+Name", "Customer Name", cleaned, flags=re.IGNORECASE)
    for pat in RE_NAME_PATTERNS:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            name = re.sub(r"\b(Card|No|Number|Account|Statement|Period|Details)\b.*", "", m.group(1), flags=re.IGNORECASE).strip()
            if 2 < len(name) < 60:
                return name
    m2 = re.search(r"\b(Mr\.?|Mrs\.?|Ms\.?)\s+[A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+", cleaned)
    return m2.group(0).strip() if m2 else None

def find_last4(text):
    for a, b in RE_LAST4.findall(text):
        digits = a or b
        if digits and digits.isdigit() and not (1900 <= int(digits) <= 2100):
            return digits[-4:]
    return None

def find_label_value(text, labels):
    lower = text.lower()
    for lbl in labels:
        idx = lower.find(lbl)
        if idx != -1:
            window = text[idx : idx + 250]
            m = RE_AMOUNT.search(window)
            if m:
                amt = re.sub(r"(₹|Rs\.?|INR|[$€£,])", "", m.group(0), flags=re.IGNORECASE)
                try:
                    val = float(re.findall(r"\d+\.\d{2}|\d+", amt)[0])
                    return f"{val:,.2f}"
                except Exception:
                    return f"{amt.strip()}"
    return None

def find_due_date_near_label(text):
    for lbl in ["payment due date", "due date", "pay by", "payment date"]:
        idx = text.lower().find(lbl)
        if idx != -1:
            window = text[idx : idx + 200]
            for d in RE_DATE.findall(window):
                try:
                    dt = dateparse.parse(d, fuzzy=True, dayfirst=True)
                    if 2020 <= dt.year <= 2100:
                        return dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
    # fallback: first valid date in document
    for d in RE_DATE.findall(text):
        try:
            dt = dateparse.parse(d, fuzzy=True, dayfirst=True)
            if 2020 <= dt.year <= 2100:
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None

def generate_billing_cycle(issuer, due_date_str):
    if not due_date_str:
        return None
    try:
        due_date = dateparse.parse(due_date_str)
    except Exception:
        return None
    days = ISSUERS.get(issuer, {}).get("cycle_days", 30)
    start_date = due_date - timedelta(days=days)
    return {"start": start_date.strftime("%Y-%m-%d"), "end": due_date.strftime("%Y-%m-%d")}

def extract_transactions_simple(text, max_lines=200):
    lines = []
    for ln in text.split("\n"):
        if RE_DATE.search(ln) and RE_AMOUNT.search(ln):
            lines.append(ln.strip())
            if len(lines) >= max_lines:
                break
    return lines

# ------------------- PARSING -------------------
def parse_statement(path):
    txt = clean_text(extract_text(path))
    issuer = detect_issuer(txt)
    name = find_customer_name(txt)
    last4 = find_last4(txt)
    due_date = find_due_date_near_label(txt)
    total_due = find_label_value(txt, ["total amount due", "amount due", "total due", "new balance", "amount payable"])
    billing = generate_billing_cycle(issuer, due_date)
    card_type = next((t for t in ["Platinum","Gold","Classic","Signature","World","Visa","Mastercard","Titanium","Infinite"] if t.lower() in txt.lower()), "N/A")
    transactions = extract_transactions_simple(txt)
    return {
        "file": os.path.basename(path),
        "issuer": issuer,
        "customer_name": name or "N/A",
        "card_last4": last4 or "N/A",
        "card_type": card_type,
        "billing_cycle": billing or "N/A",
        "payment_due_date": due_date or "N/A",
        "total_amount_due": total_due or "N/A",
        "transactions_preview": transactions[:20],
    }

def process_zip(zip_path):
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(tmpdir)
        except Exception as e:
            print(f"[ERROR] Could not extract ZIP: {e}")
            return []
        for root, _, files in os.walk(tmpdir):
            for f in files:
                if f.lower().endswith(".pdf"):
                    full_path = os.path.join(root, f)
                    try:
                        results.append(parse_statement(full_path))
                    except Exception as e:
                        print(f"[WARN] Failed to parse {f}: {e}")
        if not results:
            print(f"[WARN] No PDF files found inside {zip_path}")
    return results

# ------------------- CLI -------------------
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    args = p.parse_args()

    inp = args.input
    if inp.lower().endswith(".zip"):
        res = process_zip(inp)
    else:
        res = [parse_statement(inp)]

    print(json.dumps(res, indent=2))
