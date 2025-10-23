

import os, re, json, zipfile, tempfile
from datetime import timedelta
from dateutil import parser as dateparse


try:
    import fitz  
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except Exception:
    HAS_PDFPLUMBER = False


ISSUERS = {
    "HDFC": {"keywords": ["hdfc"], "cycle_days": 30},
    "ICICI": {"keywords": ["icici"], "cycle_days": 35},
    "SBI": {"keywords": ["sbi", "state bank of india"], "cycle_days": 40},
    "AXIS": {"keywords": ["axis"], "cycle_days": 45},
    "KOTAK": {"keywords": ["kotak"], "cycle_days": 60},
}

SUPPORTED_ISSUERS = set(ISSUERS.keys())


RE_AMOUNT = re.compile(r"(?:₹|Rs\.?|INR|USD|EUR|[$€£])?\s*\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?", re.IGNORECASE)
RE_DATE = re.compile(
    r"\b(?:\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{0,4}|[A-Za-z]{3,9}\s+\d{4})\b"
)
RE_LAST4 = re.compile(
    r"(?:card\s*(?:no\.?|number|ending|ending\s*in|xx+)\s*[:\-]?\s*(?:x{2,}\s*){0,3}(\d{4})|\b(\d{4})\b)", re.IGNORECASE
)
RE_NAME_PATTERNS = [
    r"Customer\s*Name\s*[:\-]?\s*([\w\s\.\']{2,60})(?=\s*(?:Card|A/c|Account|Statement|Period|No|Number|$))",
    r"Statement\s*for\s*[:\-]?\s*([\w\s\.\']{2,60})",
    r"Cardholder\s*[:\-]?\s*([\w\s\.\']{2,60})",
    r"Name\s*[:\-]?\s*([\w\s\.\']{2,60})",
]


def extract_text(path):
    """
    Extracts text using multiple PDF libraries (fitz → pdfplumber → fallback decode)
    """
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
    for pat in RE_NAME_PATTERNS:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            name = re.sub(
                r"\b(Card|No|Number|Account|Statement|Period|Details)\b.*", "", m.group(1), flags=re.IGNORECASE
            ).strip()
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


def clean_and_format_amount_candidate(raw_text):
    if not raw_text:
        return None, None
    s = re.sub(r"[^\d\.\-]", "", raw_text)
    if not s:
        return None, None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None, None
    try:
        val = float(m.group(0))
    except Exception:
        return None, None
    formatted = f"₹{val:,.2f}"
    return val, formatted


def find_label_value(text, labels):
    if not text:
        return None

    lower = text.lower()
    extended_labels = set([l.lower() for l in labels] + [
        "total amount due", "amount due", "total due", "new balance", "amount payable"
    ])

    label_positions = []
    for lbl in extended_labels:
        start = 0
        while True:
            idx = lower.find(lbl, start)
            if idx == -1:
                break
            label_positions.append((lbl, idx))
            start = idx + 1

    if not label_positions:
        all_matches = RE_AMOUNT.finditer(text)
        candidates = []
        for m in all_matches:
            raw = m.group(0)
            val, fmt = clean_and_format_amount_candidate(raw)
            if val is not None:
                candidates.append((val, fmt))
        if candidates:
            best = max(candidates, key=lambda x: x[0])
            return best[1]
        return None

    candidates = []
    for lbl, idx in label_positions:
        window = text[max(0, idx - 100): min(len(text), idx + len(lbl) + 250)]
        for m in RE_AMOUNT.finditer(window):
            raw = m.group(0)
            abs_pos = m.start() + idx
            val, fmt = clean_and_format_amount_candidate(raw)
            if val:
                candidates.append((val, fmt))
    if not candidates:
        return None
    best = max(candidates, key=lambda x: x[0])
    return best[1]


def find_due_date_near_label(text):
    for lbl in ["payment due date", "due date", "pay by", "payment date"]:
        idx = text.lower().find(lbl)
        if idx != -1:
            window = text[idx: idx + 200]
            for d in RE_DATE.findall(window):
                try:
                    dt = dateparse.parse(d, fuzzy=True, dayfirst=True)
                    if 2020 <= dt.year <= 2100:
                        return dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
    for d in RE_DATE.findall(text):
        try:
            dt = dateparse.parse(d, fuzzy=True, dayfirst=True)
            if 2020 <= dt.year <= 2100:
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def parse_statement(path):
    txt = clean_text(extract_text(path))
    issuer = detect_issuer(txt)

    # ✅ Bank authentication check
    if issuer not in SUPPORTED_ISSUERS:
        return {
            "file": os.path.basename(path),
            "error": "Unsupported or unrecognized bank statement",
            "issuer_detected": issuer,
        }

    
    return {
        "file": os.path.basename(path),
        "issuer": issuer,
        "customer_name": find_customer_name(txt) or "N/A",
        "card_last4": find_last4(txt) or "N/A",
        "payment_due_date": find_due_date_near_label(txt) or "N/A",
        "total_amount_due": find_label_value(
            txt, ["total amount due", "amount due", "total due", "new balance", "amount payable"]
        ) or "N/A",
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


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Credit Card PDF Parser (5 Key Data Points + Auth)")
    p.add_argument("--input", required=True, help="Path to PDF or ZIP file")
    args = p.parse_args()

    inp = args.input
    if inp.lower().endswith(".zip"):
        res = process_zip(inp)
    else:
        res = [parse_statement(inp)]

    print(json.dumps(res, indent=2))
