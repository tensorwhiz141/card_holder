from fastapi import FastAPI, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import tempfile, os, io, csv, zipfile, shutil, json
from app.parser_enhanced import parse_statement

app = FastAPI(title="Credit Card Statement Parser")

# Static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory results store (simple). For production, use a database.
LAST_RESULTS = []

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)):
    # Save uploaded PDF to temp file and parse
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix != ".pdf":
        return templates.TemplateResponse("result.html", {"request": request, "error": "Please upload a PDF file."})
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = parse_statement(tmp_path)
    finally:
        try: os.remove(tmp_path)
        except: pass

    # store last result
    LAST_RESULTS.clear()
    LAST_RESULTS.append(result)

    return templates.TemplateResponse("result.html", {"request": request, "data": result, "filename": file.filename})

@app.post("/parse-zip")
async def parse_zip(file: UploadFile = File(...)):
    """
    Upload a zip with multiple PDFs. Returns JSON array of parsed results.
    """
    if not file.filename.lower().endswith(".zip"):
        return JSONResponse({"error": "Please upload a .zip file containing PDFs"}, status_code=400)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "uploaded.zip")
        with open(zip_path, "wb") as f:
            f.write(await file.read())
        # extract
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmpdir)
        results = []
        for root, _, files in os.walk(tmpdir):
            for fname in files:
                if fname.lower().endswith(".pdf"):
                    p = os.path.join(root, fname)
                    try:
                        r = parse_statement(p)
                        results.append(r)
                    except Exception as e:
                        results.append({"file": fname, "error": str(e)})
        # store results
        LAST_RESULTS.clear()
        LAST_RESULTS.extend(results)
        return JSONResponse(results)

@app.get("/download/json")
def download_json():
    if not LAST_RESULTS:
        return JSONResponse({"error": "No results available"}, status_code=404)
    data = json.dumps(LAST_RESULTS, indent=2)
    return JSONResponse(json.loads(data))

@app.get("/download/csv")
def download_csv():
    if not LAST_RESULTS:
        return JSONResponse({"error": "No results available"}, status_code=404)

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    # header
    header = ["file","issuer","card_last4","card_type","billing_cycle_start","billing_cycle_end","payment_due_date","total_amount_due"]
    writer.writerow(header)
    for r in LAST_RESULTS:
        bc_start = r.get("billing_cycle", {}).get("start") if isinstance(r.get("billing_cycle"), dict) else ""
        bc_end = r.get("billing_cycle", {}).get("end") if isinstance(r.get("billing_cycle"), dict) else ""
        writer.writerow([
            r.get("file"),
            r.get("issuer"),
            r.get("card_last4"),
            r.get("card_type"),
            bc_start,
            bc_end,
            r.get("payment_due_date"),
            r.get("total_amount_due")
        ])
    output.seek(0)
    return FileResponse(path_or_file=io.BytesIO(output.getvalue().encode("utf-8")),
                        media_type="text/csv",
                        filename="parsed_results.csv")

@app.get("/health")
def health():
    return {"status": "ok"}

# Friendly redirect
@app.get("/docs")
def open_docs():
    return RedirectResponse(url="/")
