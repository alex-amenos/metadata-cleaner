#!/usr/bin/env bash
# macOS launcher: installs missing Homebrew tools, starts the server on localhost, opens the browser.
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v brew >/dev/null; then
  echo "Homebrew is required: https://brew.sh" >&2
  exit 1
fi

for tool in exiftool ffmpeg qpdf mat2; do
  command -v "$tool" >/dev/null || brew install "$tool"
done
command -v soffice >/dev/null || echo "Note: LibreOffice not found; legacy .doc files will be unsupported (brew install --cask libreoffice)."

PY=$(command -v python3) || { echo "python3 not found (xcode-select --install or brew install python)" >&2; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt

PORT="${PORT:-8000}"
(sleep 2 && open "http://127.0.0.1:$PORT") &
# Bound to localhost only.
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
