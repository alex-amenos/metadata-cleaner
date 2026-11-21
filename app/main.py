"""Local-only metadata cleaner. Binds to 127.0.0.1; files never leave the machine."""
import asyncio
import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# GUI-launched processes on macOS often lack Homebrew in PATH (Apple Silicon and Intel).
os.environ["PATH"] = os.pathsep.join(
    ["/opt/homebrew/bin", "/usr/local/bin", "/Applications/LibreOffice.app/Contents/MacOS", os.environ.get("PATH", "")]
)

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
MAX_FILES = 20
MAX_FILE_BYTES = 300 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
RESULT_TTL = 600  # seconds a cleaned file may wait to be downloaded
TMP_PREFIX = "mdclean-"

STATIC = Path(__file__).parent / "static"

IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".heic", ".bmp"}
VIDEO = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus"}
OFFICE = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
# Forcing the input demuxer stops ffmpeg from content-sniffing playlist/concat formats
# that can read other local files or URLs.
FFMPEG_FORMAT = {
    ".mp4": "mov", ".mov": "mov", ".m4v": "mov", ".m4a": "mov", ".mkv": "matroska", ".webm": "matroska",
    ".avi": "avi", ".mp3": "mp3", ".flac": "flac", ".wav": "wav", ".ogg": "ogg", ".opus": "ogg", ".aac": "aac",
}

# Cleaned files wait on disk until downloaded or expired.
RESULTS: dict[str, dict] = {}


class UserError(Exception):
    """Message is safe to show: it never contains paths, file names or file content."""


def _cleanup() -> None:
    for r in RESULTS.values():
        shutil.rmtree(r["dir"], ignore_errors=True)
    RESULTS.clear()


def sweep(max_age: float = RESULT_TTL) -> None:
    now = time.time()
    for token, r in list(RESULTS.items()):
        if now - r["created"] > max_age:
            RESULTS.pop(token, None)
            shutil.rmtree(r["dir"], ignore_errors=True)


def purge_stale_dirs() -> None:
    """Remove leftovers from runs that were killed before they could clean up."""
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob(f"{TMP_PREFIX}*"):
        try:
            if now - d.stat().st_mtime > RESULT_TTL:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    purge_stale_dirs()

    async def sweeper():
        while True:
            await asyncio.sleep(60)
            sweep()

    task = asyncio.create_task(sweeper())
    try:
        # Closing the Terminal window sends SIGHUP: turn it into a graceful shutdown.
        signal.signal(signal.SIGHUP, lambda *_: os.kill(os.getpid(), signal.SIGTERM))
    except (ValueError, AttributeError):
        pass
    yield
    task.cancel()
    _cleanup()


app = FastAPI(title="Metadata Cleaner", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _hostname(netloc: str):
    try:
        return urlsplit("//" + netloc).hostname
    except ValueError:
        return None


@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.headers.get("host", "")
    # Blocks DNS rebinding: a rebound page still sends its own hostname in Host.
    if _hostname(host) not in ALLOWED_HOSTS:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin", "")
        # Blocks cross-site form posts; the custom header also forces a CORS preflight.
        if urlsplit(origin).netloc != host or request.headers.get("x-mdc") != "1":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse({"error": "bad request"}, status_code=400)
        if length > MAX_TOTAL_BYTES + (1 << 20):
            return JSONResponse({"error": "too large"}, status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return response


def safe_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", Path(name).name).strip()
    return name or "file"


def attachment(name: str) -> str:
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\;]', "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def run(cmd: list[str]) -> None:
    tool = cmd[0]
    if shutil.which(tool) is None:
        raise UserError(f"'{tool}' is not installed (brew install {tool})")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise UserError(f"'{tool}' timed out") from None
    if r.returncode != 0:
        # Tool output can contain temp paths or file content, so it is not forwarded.
        raise UserError(f"'{tool}' could not process this file")


def clean_exiftool(src: Path) -> Path:
    run(["exiftool", "-all=", "-overwrite_original", str(src)])
    return src


def clean_ffmpeg(src: Path) -> Path:
    out = src.with_name(f"clean{src.suffix}")
    run(["ffmpeg", "-nostdin", "-y", "-protocol_whitelist", "file", "-f", FFMPEG_FORMAT[src.suffix],
         "-i", str(src), "-map", "0", "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", str(out)])
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
        raise UserError("'mat2' produced no output")
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


MAGIC = [
    (b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"GIF8", ".gif"),
    (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"), (b"%PDF", ".pdf"),
]


def real_ext(name: str, data: bytes) -> str:
    """Trust file content over the filename (e.g. a JPEG saved as .png makes exiftool fail)."""
    ext = Path(name).suffix.lower()
    for sig, kind in MAGIC:
        if data.startswith(sig):
            return ext if ext in {".jpg", ".jpeg", ".tif", ".tiff"} and ext[:3] == kind[:3] else kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ext


def read_tags(path: Path) -> dict:
    if shutil.which("exiftool") is None:
        raise UserError("'exiftool' is not installed (brew install exiftool)")
    try:
        r = subprocess.run(["exiftool", "-json", "-a", "-G1", str(path)],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise UserError("'exiftool' timed out") from None
    if r.returncode != 0:
        raise UserError("'exiftool' could not read this file")
    tags = json.loads(r.stdout)[0]
    # Drop tags about the temp file itself, not the user's metadata.
    return {k: v for k, v in tags.items()
            if k != "SourceFile" and not k.startswith(("System:", "ExifTool:"))}


def clean_one(name: str, data: bytes) -> tuple[str, bytes, dict, dict]:
    name = safe_name(name)
    ext = real_ext(name, data)
    cleaner = pick_cleaner(ext)
    if cleaner is None:
        raise UserError(f"unsupported file type '{ext[:12]}'")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{ext}"
        src.write_bytes(data)
        before = read_tags(src)  # must run first: some cleaners edit src in place
        out = cleaner(src)
        after = read_tags(out)
        # The original is never touched; the result is a copy with a "_clean" suffix.
        out_name = f"{Path(name).stem[:100]}_clean{'.docx' if ext == '.doc' else ext}"
        return out_name, out.read_bytes(), before, after


def describe(e: Exception) -> str:
    if isinstance(e, UserError):
        return str(e)
    return "unexpected error processing this file"


async def read_upload(f: UploadFile, budget: list[int]) -> bytes:
    chunks, size = [], 0
    while chunk := await f.read(1 << 20):
        size += len(chunk)
        if size > MAX_FILE_BYTES or size > budget[0]:
            raise UserError("file too large")
        chunks.append(chunk)
    budget[0] -= size
    return b"".join(chunks)


def too_many(files: list[UploadFile]) -> dict:
    return {(f.filename or "file"): {"error": f"too many files (max {MAX_FILES})"} for f in files}


@app.post("/clean")
async def clean(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        return JSONResponse({"files": too_many(files)}, status_code=413)
    sweep()
    token = uuid.uuid4().hex
    outdir = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
    budget = [MAX_TOTAL_BYTES]
    report, errors, saved = {}, {}, []
    for f in files:
        name = f.filename or "file"
        try:
            out_name, data, before, after = clean_one(name, await read_upload(f, budget))
            n = 2
            base = Path(out_name)
            while (outdir / out_name).exists():  # same filename uploaded twice
                out_name = f"{base.stem}_{n}{base.suffix}"
                n += 1
            (outdir / out_name).write_bytes(data)
            saved.append(out_name)
            report[name] = {"before": before, "after": after, "output": out_name}
        except Exception as e:  # report per-file failures without leaking details
            msg = describe(e)
            errors[safe_name(name)] = msg
            report[name] = {"error": msg}
    if not saved:
        shutil.rmtree(outdir, ignore_errors=True)
        return JSONResponse({"files": report}, status_code=422)
    RESULTS[token] = {"dir": outdir, "files": saved, "errors": errors, "created": time.time()}
    return {"token": token, "files": report}


@app.get("/download/{token}")
def download(token: str):
    sweep()
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
    return Response(data, media_type=media, headers={"Content-Disposition": attachment(fname)})


def inspect_one(name: str, data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{real_ext(name, data)}"
        src.write_bytes(data)
        return read_tags(src)


@app.post("/inspect")
async def inspect(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        return JSONResponse(too_many(files), status_code=413)
    budget = [MAX_TOTAL_BYTES]
    out = {}
    for f in files:
        name = f.filename or "file"
        try:
            out[name] = {"tags": inspect_one(name, await read_upload(f, budget))}
        except Exception as e:
            out[name] = {"error": describe(e)}
    return out


@app.get("/tools")
def tools():
    return {t: shutil.which(t) is not None for t in ["exiftool", "ffmpeg", "qpdf", "mat2", "soffice"]}


@app.get("/health")
def health():
    return {"app": "metadata-cleaner"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
