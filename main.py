from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import tempfile, os, io, csv, json
from app.parser_enhanced import parse_statement, process_zip

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

        # ✅ Throw error for unsupported banks
        if result.get("issuer") not in ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK"]:
            raise ValueError(
                "Only HDFC, ICICI, SBI, AXIS, and KOTAK bank statements are supported."
            )

    except Exception as e:
        # ✅ Pass error to template (SweetAlert will show it)
        return templates.TemplateResponse(
            "result.html",
            {"request": request, "error": str(e), "filename": file.filename}
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

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        results = process_zip(tmp_path)

        # ✅ Check if any unsupported banks are inside
        for r in results:
            if r.get("issuer") not in ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK"]:
                raise ValueError(
                    f"Unsupported bank found in ZIP ({r.get('issuer')}). Only HDFC, ICICI, SBI, AXIS, and KOTAK are supported."
                )

    except Exception as e:
        return templates.TemplateResponse(
            "result.html",
            {"request": request, "error": str(e)}
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    LAST_RESULTS.clear()
    LAST_RESULTS.extend(results)

    return templates.TemplateResponse(
        "result.html",
        {"request": request, "results": results, "multiple": True}
    )


@app.get("/download/json")
def download_json():
    if not LAST_RESULTS:
        return JSONResponse({"error": "No results available"}, status_code=404)
    return JSONResponse(LAST_RESULTS)


@app.get("/download/csv")
def download_csv():
    if not LAST_RESULTS:
        return JSONResponse({"error": "No results available"}, status_code=404)

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "file", "issuer", "customer_name", "card_last4",
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
    return {"status": "ok"}


@app.get("/docs")
def open_docs():
    return RedirectResponse(url="/")
