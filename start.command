#!/bin/bash
# pdf2xlsx web app, one click: double-click this file in Finder (macOS), or run ./start.command (Linux).
# The first run installs what is missing (Tesseract via Homebrew, a Python environment with the packages);
# later runs only start the app and open it in the browser. Options are passed on: ./start.command --port 8080
cd "$(dirname "$0")" || exit 1
VENV="${PDF2XLSX_VENV:-$HOME/pdf2xlsx-venv}"     # in the home folder: Python cannot create one in a path with ":"

fail() { echo; echo "$1"; echo; read -r -p "Press Enter to close this window. " _; exit 1; }

if ! command -v tesseract >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "Installing Tesseract (OCR) with Homebrew..."
        brew install tesseract || fail "Installing Tesseract failed."
    else
        fail "Tesseract (the OCR program) is not installed.
  macOS: install Homebrew from https://brew.sh, then run:  brew install tesseract
  Ubuntu/Debian:  sudo apt install tesseract-ocr"
    fi
fi

PY=""
for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$p"; break
    fi
done
[ -x "$VENV/bin/python" ] || [ -n "$PY" ] || fail "Python 3.10 or newer is needed.
  macOS:  brew install python      Ubuntu/Debian:  sudo apt install python3 python3-venv"

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating the Python environment in $VENV (first run only)..."
    "$PY" -m venv "$VENV" || fail "Could not create the Python environment."
fi
if ! cmp -s requirements.txt "$VENV/.requirements.txt"; then        # first run, or the requirements changed
    echo "Installing the Python packages..."
    "$VENV/bin/python" -m pip install -q --upgrade pip
    "$VENV/bin/python" -m pip install -r requirements.txt || fail "Installing the Python packages failed."
    cp requirements.txt "$VENV/.requirements.txt"
fi

exec "$VENV/bin/python" app.py "$@"
