# Metadata Cleaner

A local-only web UI (FastAPI) that strips metadata from documents, PDFs, images, video and audio on macOS. The server binds to `127.0.0.1`; files never leave your machine and temp files are deleted after processing.

## Run on macOS

Requires [Homebrew](https://brew.sh) and Python 3 (`xcode-select --install` provides one).

```sh
./run.sh        # or double-click "Metadata Cleaner.command" in Finder
```

The launcher installs any missing tools (`exiftool ffmpeg qpdf mat2`), creates a virtualenv, starts the server on `127.0.0.1:8000` (override with `PORT=9000 ./run.sh`) and opens your browser. On first double-click, macOS may require right-click → Open (Gatekeeper), or `chmod +x` if the executable bit was lost.

For legacy `.doc` support: `brew install --cask libreoffice`.

Selecting or dropping files lists their current metadata. **Borrar metadatos** cleans them and shows a before/after diff (red = removed, yellow = changed, grey = remaining); then **Descargar** saves the result. Multiple files (or any failures) download as a zip, with `ERRORS.txt` listing failed files.

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
