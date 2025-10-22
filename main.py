from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import tempfile, os, io, csv, json
from app.parser_enhanced import parse_statement, process_zip  # ✅ include process_zip

app = FastAPI(title="Credit Card Statement Parser")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

LAST_RESULTS = []


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Landing page (upload form)"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)):
    """Upload and parse a single PDF"""
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix != ".pdf":
        return templates.TemplateResponse(
            "result.html",
            {"request": request, "error": "Please upload a valid PDF file."}
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = parse_statement(tmp_path)
    except Exception as e:
        return templates.TemplateResponse(
            "result.html",
            {"request": request, "error": f"Failed to parse file: {str(e)}"}
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    LAST_RESULTS.clear()
    LAST_RESULTS.append(result)

    return templates.TemplateResponse(
        "result.html",
        {"request": request, "result": result, "filename": file.filename, "multiple": False}
    )


@app.post("/parse-zip", response_class=HTMLResponse)
async def parse_zip(request: Request, file: UploadFile = File(...)):
    """Upload a ZIP containing multiple PDFs"""
    if not file.filename.lower().endswith(".zip"):
        return templates.TemplateResponse(
            "result.html",
            {"request": request, "error": "Please upload a .zip file containing PDFs"}
        )

    # ✅ Save uploaded ZIP to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # ✅ Reuse existing robust ZIP parser
        results = process_zip(tmp_path)
    except Exception as e:
        return templates.TemplateResponse(
            "result.html",
            {"request": request, "error": f"Failed to process ZIP: {str(e)}"}
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    LAST_RESULTS.clear()
    LAST_RESULTS.extend(results)

    # ✅ Render all results using same template (multiple mode)
    return templates.TemplateResponse(
        "result.html",
        {"request": request, "results": results, "multiple": True}
    )


@app.get("/download/json")
def download_json():
    """Download last parsed results as JSON"""
    if not LAST_RESULTS:
        return JSONResponse({"error": "No results available"}, status_code=404)
    return JSONResponse(LAST_RESULTS)


@app.get("/download/csv")
def download_csv():
    """Download last parsed results as CSV"""
    if not LAST_RESULTS:
        return JSONResponse({"error": "No results available"}, status_code=404)

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "file", "issuer", "customer_name", "card_last4", "card_type",
        "billing_cycle_start", "billing_cycle_end",
        "payment_due_date", "total_amount_due"
    ]
    writer.writerow(header)

    for r in LAST_RESULTS:
        bc_start = r.get("billing_cycle", {}).get("start") if isinstance(r.get("billing_cycle"), dict) else ""
        bc_end = r.get("billing_cycle", {}).get("end") if isinstance(r.get("billing_cycle"), dict) else ""
        writer.writerow([
            r.get("file"),
            r.get("issuer"),
            r.get("customer_name"),
            r.get("card_last4"),
            r.get("card_type"),
            bc_start,
            bc_end,
            r.get("payment_due_date"),
            r.get("total_amount_due")
        ])

    output.seek(0)
    return FileResponse(
        path_or_file=io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        filename="parsed_results.csv"
    )


@app.get("/health")
def health():
    """Simple health check"""
    return {"status": "ok"}


@app.get("/docs")
def open_docs():
    """Redirect FastAPI docs → homepage (friendly UX)"""
    return RedirectResponse(url="/")
