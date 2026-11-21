# Metadata Cleaner

A local-only web UI (FastAPI) that strips metadata from documents, PDFs, images, video and audio on macOS. The server binds to `127.0.0.1`; files never leave your machine and temp files are deleted after processing.

## Setup (macOS)

```sh
brew install exiftool ffmpeg qpdf mat2
# optional, for legacy .doc (converted to .docx):
brew install --cask libreoffice
./run.sh
```

Open http://127.0.0.1:8000 and drop files. Multiple files (or any failures) return a zip, with `ERRORS.txt` listing failed files.

## Tools per type

| Type | Tool |
|---|---|
| Images | `exiftool -all=` |
| Video / audio | `ffmpeg -map_metadata -1 -c copy` (no re-encode) |
| PDF | `exiftool` + `qpdf` rewrite (drops old revisions) |
| docx/xlsx/pptx/odf | `mat2` |
| doc | LibreOffice → docx, then `mat2` |

## Caveats

- Embedded content (hidden text, tracked changes, watermarks, pixels) is not metadata and is not removed.
- Verify results with `exiftool -a -G1 file`.
- `/tools` shows which required tools are installed.
