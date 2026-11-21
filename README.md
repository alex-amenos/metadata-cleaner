# Metadata Cleaner

A local web app that removes metadata from documents, PDFs, images, video and audio. It runs on your Mac: the server only listens on `127.0.0.1`, files never leave your machine, and temporary files are deleted after use.

## How it works

1. Drag files onto the page or click **Choose files**. Their current metadata is listed.
2. Click **Delete metadata**. A before/after diff shows what was removed (red), changed (yellow) and what remains (grey).
3. Click **Download clean copy**.

Your originals are never modified. The result is a new file named `<name>_clean.<ext>` (legacy `.doc` becomes `<name>_clean.docx`), and each result card labels which file is the original and which is the metadata-free copy. Several files, or any failure, download as a zip; `ERRORS.txt` lists files that failed.

The interface is available in English, Castellano and Català (selector at the top right). It follows the browser language and remembers your choice. The page is responsive and uses a dark theme.

## Run on macOS

Requires [Homebrew](https://brew.sh) and Python 3 (`xcode-select --install` provides one).

**Double-click** `Metadata Cleaner.app` (or `Metadata Cleaner.command`).

- The app opens Terminal and runs `Metadata Cleaner.command`, so it behaves exactly like running `./run.sh` yourself.
- On first run it installs any missing tools (this can take a few minutes), starts the server and opens your browser.
- Opening it again while the server runs just opens the browser.
- To stop the server, press Ctrl+C or close the Terminal window.
- Keep the app inside this folder; it uses the files next to it.
- If macOS blocks it (Gatekeeper, for example after downloading a zip), right-click → Open once, or run `xattr -dr com.apple.quarantine .` in this folder.

**From Terminal:**

```sh
./run.sh
```

`run.sh` installs missing tools, creates `.venv`, starts the server and opens the browser. Use `PORT=9000 ./run.sh` for another port.

### Install the tools manually

```sh
brew install exiftool ffmpeg qpdf mat2
brew install --cask libreoffice   # optional, only for legacy .doc
```

The **i** button next to *Tools* in the page shows these steps. The *Tools* line shows ✓ or ✗ for each tool.

## What cleans what

| Type | Extensions | Tool |
|---|---|---|
| Images | jpg, jpeg, png, gif, tif, tiff, webp, heic, bmp | `exiftool -all=` |
| Video | mp4, mov, m4v, mkv, avi, webm | `ffmpeg -map_metadata -1 -c copy` (no re-encode) |
| Audio | mp3, m4a, flac, wav, ogg, aac, opus | `ffmpeg -map_metadata -1 -c copy` |
| PDF | pdf | `exiftool`, then `qpdf` rewrite to drop old revisions |
| Office | docx, xlsx, pptx, odt, ods, odp | `mat2` |
| Legacy Word | doc | LibreOffice converts to docx, then `mat2` |

The real file type is detected from the file content for JPEG, PNG, GIF, TIFF, WebP and PDF, so a JPEG named `.png` still works (the copy gets the correct extension). Other types rely on the filename extension.

Metadata is read and compared with `exiftool -json -a -G1`.

## Project layout

```
app/main.py            FastAPI app: file-type routing, cleaners, endpoints
app/static/index.html  Single-page UI (vanilla JS, i18n, diff view)
run.sh                 Launcher: installs tools, venv, server, opens browser
Metadata Cleaner.app   Double-click launcher (opens the .command in Terminal)
Metadata Cleaner.command  Runs run.sh in Terminal
requirements.txt       fastapi, uvicorn, python-multipart
```

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | The web page |
| `GET /tools` | Which external tools are installed |
| `POST /inspect` | Upload files, get their current metadata as JSON |
| `POST /clean` | Upload files, get a before/after report and a download token |
| `GET /download/{token}` | One-time download of the cleaned file or zip |

Cleaned files are kept in a temp folder until downloaded (or until the server exits).

## Limitations

- Embedded content (hidden text, tracked changes, watermarks, the image pixels themselves) is not metadata and is not removed.
- Cleaning needs the matching tool installed; otherwise that file reports an error naming the missing tool.
- Error messages from the server and metadata tag names are not translated.
- Verify any result yourself with `exiftool -a -G1 file`.
- There is no automated test suite yet.
