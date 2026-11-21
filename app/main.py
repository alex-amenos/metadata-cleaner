"""Local-only metadata cleaner. Binds to 127.0.0.1; files never leave the machine."""
import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Metadata Cleaner")
STATIC = Path(__file__).parent / "static"

IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".heic", ".bmp"}
VIDEO = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus"}
OFFICE = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}


def run(cmd: list[str]) -> None:
    if shutil.which(cmd[0]) is None:
        raise RuntimeError(f"'{cmd[0]}' is not installed (brew install {cmd[0]})")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:500])


def clean_exiftool(src: Path) -> Path:
    run(["exiftool", "-all=", "-overwrite_original", str(src)])
    return src


def clean_ffmpeg(src: Path) -> Path:
    out = src.with_name(f"clean{src.suffix}")
    run(["ffmpeg", "-y", "-i", str(src), "-map", "0", "-map_metadata", "-1",
         "-map_chapters", "-1", "-c", "copy", str(out)])
    return out


def clean_pdf(src: Path) -> Path:
    run(["exiftool", "-all=", "-overwrite_original", str(src)])
    out = src.with_name("clean.pdf")
    # Rewriting drops the old revisions exiftool leaves behind.
    run(["qpdf", "--empty", "--pages", str(src), "--", "--linearize", str(out)])
    run(["exiftool", "-all=", "-overwrite_original", str(out)])
    return out


def clean_mat2(src: Path) -> Path:
    run(["mat2", str(src)])
    out = src.with_name(f"{src.stem}.cleaned{src.suffix}")
    if not out.exists():
        raise RuntimeError("mat2 produced no output")
    return out


def clean_doc(src: Path) -> Path:
    run(["soffice", "--headless", "--convert-to", "docx", "--outdir", str(src.parent), str(src)])
    return clean_mat2(src.with_suffix(".docx"))


def pick_cleaner(ext: str):
    if ext in IMAGE:
        return clean_exiftool
    if ext in VIDEO | AUDIO:
        return clean_ffmpeg
    if ext == ".pdf":
        return clean_pdf
    if ext in OFFICE:
        return clean_mat2
    if ext == ".doc":
        return clean_doc
    return None


def clean_one(name: str, data: bytes) -> tuple[str, bytes]:
    name = Path(name).name
    ext = Path(name).suffix.lower()
    cleaner = pick_cleaner(ext)
    if cleaner is None:
        raise RuntimeError(f"unsupported file type '{ext}'")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{ext}"
        src.write_bytes(data)
        out = cleaner(src)
        out_name = f"{Path(name).stem}.docx" if ext == ".doc" else name
        return out_name, out.read_bytes()


@app.post("/clean")
async def clean(files: list[UploadFile] = File(...)):
    results, errors = [], {}
    for f in files:
        try:
            results.append(clean_one(f.filename or "file", await f.read()))
        except Exception as e:  # report per-file failures
            errors[f.filename] = str(e)
    if not results:
        return JSONResponse({"errors": errors}, status_code=422)
    if len(results) == 1 and not errors:
        name, data = results[0]
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in results:
            z.writestr(name, data)
        if errors:
            z.writestr("ERRORS.txt", "\n".join(f"{k}: {v}" for k, v in errors.items()))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="cleaned.zip"'})


@app.get("/tools")
def tools():
    return {t: shutil.which(t) is not None for t in ["exiftool", "ffmpeg", "qpdf", "mat2", "soffice"]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
