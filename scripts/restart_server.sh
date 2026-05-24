#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"

cd "$ROOT_DIR"

if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "Missing .venv/bin/uvicorn. Run: .venv/bin/python -m pip install ." >&2
  exit 1
fi

PID="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN || true)"
if [ -n "$PID" ]; then
  echo "Stopping server on port $PORT: $PID"
  kill $PID
  sleep 1
fi

echo "Starting server at http://127.0.0.1:$PORT"
exec .venv/bin/uvicorn spend_tracker.main:app --host 127.0.0.1 --port "$PORT"
