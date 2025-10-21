#!/usr/bin/env python3
"""
Demo runner that applies parser_enhanced.py to all PDFs in sample_pdfs/
Outputs results.json, results.csv, and results.db (SQLite)
"""
import os, json, csv, sqlite3
from parser_enhanced import parse_statement

IN_DIR = "sample_pdfs"
OUT_JSON = "results.json"
OUT_CSV = "results.csv"
OUT_DB = "results.db"

os.makedirs(IN_DIR, exist_ok=True)
results = []

for fn in sorted(os.listdir(IN_DIR)):
    if fn.lower().endswith(".pdf"):
        path = os.path.join(IN_DIR, fn)
        try:
            res = parse_statement(path)
            results.append(res)
        except Exception as e:
            results.append({"file": fn, "error": str(e)})

# JSON
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

# CSV
keys = ["file","issuer","card_last4","card_type","payment_due_date","total_amount_due"]
with open(OUT_CSV, "w", newline='', encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(keys)
    for r in results:
        writer.writerow([r.get(k) for k in keys])

# SQLite
conn = sqlite3.connect(OUT_DB)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS statements (
    file TEXT, issuer TEXT, card_last4 TEXT, card_type TEXT,
    payment_due_date TEXT, total_amount_due TEXT, raw_json TEXT
)""")
for r in results:
    c.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?)", (
        r.get("file"), r.get("issuer"), r.get("card_last4"),
        r.get("card_type"), r.get("payment_due_date"),
        r.get("total_amount_due"), json.dumps(r)
    ))
conn.commit()
conn.close()

print(f"Wrote {len(results)} results -> {OUT_JSON}, {OUT_CSV}, {OUT_DB}")
