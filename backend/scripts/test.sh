#!/usr/bin/env sh
# Run the backend tests with the project's virtual environment, never the global Python.
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/Scripts/python.exe ]; then python=.venv/Scripts/python.exe; else python=.venv/bin/python; fi
if [ ! -x "$python" ]; then
  echo "No .venv found. Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
exec "$python" -m pytest "$@"
