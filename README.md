# Metadata Cleaner

A local-only web UI (FastAPI) that strips metadata from documents, PDFs, images, video and audio on macOS. The server binds to `127.0.0.1`; files never leave your machine and temp files are deleted after processing.

## Run on macOS

Requires [Homebrew](https://brew.sh) and Python 3 (`xcode-select --install` provides one).

**No Terminal:** double-click `Metadata Cleaner.app`. It installs any missing tools on first run (can take a few minutes), starts the server in the background and opens your browser. Open it again while running to get **Open** or **Stop server**. Logs: `~/Library/Logs/MetadataCleaner.log`. Keep the app inside this folder.

If macOS blocks it (Gatekeeper, e.g. after downloading a zip), right-click → Open once, or run `xattr -dr com.apple.quarantine .` in the folder.

**Terminal alternative:**

```sh
./run.sh        # or double-click "Metadata Cleaner.command"
```

Both start the server on `127.0.0.1:8000` (override with `PORT=9000 ./run.sh`).

For legacy `.doc` support: `brew install --cask libreoffice`.

Selecting or dropping files lists their current metadata. **Borrar metadatos** cleans them and shows a before/after diff (red = removed, yellow = changed, grey = remaining); then **Descargar** saves a copy named `<name>_clean.<ext>` (legacy `.doc` becomes `<name>_clean.docx`). The original is never modified, and the UI labels which file is the original and which is the new metadata-free copy. Multiple files (or any failures) download as a zip, with `ERRORS.txt` listing failed files.

The UI language (English, Castellano, Català) can be switched from the selector at the top right; it defaults to the browser language and is remembered.

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
