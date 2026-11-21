"""Local-only metadata cleaner. Binds to 127.0.0.1; files never leave the machine."""
import atexit
import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# GUI-launched processes on macOS often lack Homebrew in PATH (Apple Silicon and Intel).
os.environ["PATH"] = os.pathsep.join(
    ["/opt/homebrew/bin", "/usr/local/bin", "/Applications/LibreOffice.app/Contents/MacOS", os.environ.get("PATH", "")]
)

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


def read_tags(path: Path) -> dict:
    if shutil.which("exiftool") is None:
        raise RuntimeError("'exiftool' is not installed (brew install exiftool)")
    r = subprocess.run(["exiftool", "-json", "-a", "-G1", str(path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:500])
    tags = json.loads(r.stdout)[0]
    # Drop tags about the temp file itself, not the user's metadata.
    return {k: v for k, v in tags.items()
            if k != "SourceFile" and not k.startswith(("System:", "ExifTool:"))}


def clean_one(name: str, data: bytes) -> tuple[str, bytes, dict, dict]:
    name = Path(name).name
    ext = Path(name).suffix.lower()
    cleaner = pick_cleaner(ext)
    if cleaner is None:
        raise RuntimeError(f"unsupported file type '{ext}'")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{ext}"
        src.write_bytes(data)
        before = read_tags(src)  # must run first: some cleaners edit src in place
        out = cleaner(src)
        after = read_tags(out)
        # The original is never touched; the result is a copy with a "_clean" suffix.
        out_name = f"{Path(name).stem}_clean{'.docx' if ext == '.doc' else Path(name).suffix}"
        return out_name, out.read_bytes(), before, after


# Cleaned files wait on disk until downloaded.
RESULTS: dict[str, dict] = {}


@atexit.register
def _cleanup() -> None:
    for r in RESULTS.values():
        shutil.rmtree(r["dir"], ignore_errors=True)


@app.post("/clean")
async def clean(files: list[UploadFile] = File(...)):
    token = uuid.uuid4().hex
    outdir = Path(tempfile.mkdtemp(prefix="mdclean-"))
    report, errors, saved = {}, {}, []
    for f in files:
        name = f.filename or "file"
        try:
            out_name, data, before, after = clean_one(name, await f.read())
            n = 2
            base = Path(out_name)
            while (outdir / out_name).exists():  # same filename uploaded twice
                out_name = f"{base.stem}_{n}{base.suffix}"
                n += 1
            (outdir / out_name).write_bytes(data)
            saved.append(out_name)
            report[name] = {"before": before, "after": after, "output": out_name}
        except Exception as e:  # report per-file failures
            errors[name] = str(e)
            report[name] = {"error": str(e)}
    if not saved:
        shutil.rmtree(outdir, ignore_errors=True)
        return JSONResponse({"errors": errors, "files": report}, status_code=422)
    RESULTS[token] = {"dir": outdir, "files": saved, "errors": errors}
    return {"token": token, "files": report}


@app.get("/download/{token}")
def download(token: str):
    res = RESULTS.pop(token, None)
    if res is None:
        return JSONResponse({"error": "unknown or already downloaded"}, status_code=404)
    try:
        if len(res["files"]) == 1 and not res["errors"]:
            name = res["files"][0]
            data, media, fname = (res["dir"] / name).read_bytes(), "application/octet-stream", name
        else:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name in res["files"]:
                    z.write(res["dir"] / name, name)
                if res["errors"]:
                    z.writestr("ERRORS.txt", "\n".join(f"{k}: {v}" for k, v in res["errors"].items()))
            data, media, fname = buf.getvalue(), "application/zip", "cleaned.zip"
    finally:
        shutil.rmtree(res["dir"], ignore_errors=True)
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


def inspect_one(name: str, data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{Path(name).suffix.lower()}"
        src.write_bytes(data)
        return read_tags(src)


@app.post("/inspect")
async def inspect(files: list[UploadFile] = File(...)):
    out = {}
    for f in files:
        name = f.filename or "file"
        try:
            out[name] = {"tags": inspect_one(name, await f.read())}
        except Exception as e:
            out[name] = {"error": str(e)}
    return out


@app.get("/tools")
def tools():
    return {t: shutil.which(t) is not None for t in ["exiftool", "ffmpeg", "qpdf", "mat2", "soffice"]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
