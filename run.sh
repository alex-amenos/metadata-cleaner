#!/usr/bin/env bash
# macOS launcher: installs missing Homebrew tools, starts the server on localhost, opens the browser.
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PORT="${PORT:-8000}"

# Already running (e.g. app opened twice): just open the browser.
if curl -fs --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q metadata-cleaner; then
  [ -n "$NO_OPEN" ] || open "http://127.0.0.1:$PORT"
  exit 0
fi

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
# Install only when requirements change (fewer network fetches of third-party code).
if ! cmp -s requirements.txt .venv/.requirements.installed; then
  .venv/bin/pip install -q -r requirements.txt
  cp requirements.txt .venv/.requirements.installed
fi

[ -n "$NO_OPEN" ] || { (sleep 2 && open "http://127.0.0.1:$PORT") & }
# Bound to localhost only.
# No access log: request lines and download tokens are not written anywhere.
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --no-access-log --log-level warning
