#!/usr/bin/env bash
# Run the API locally against whatever MODEL_SOURCE is configured in .env.
set -euo pipefail

PORT="${PORT:-8320}"
export FLASK_APP="${FLASK_APP:-app:app}"

echo "Starting on http://127.0.0.1:${PORT} (MODEL_SOURCE=${MODEL_SOURCE:-s3})"
exec python -m flask run --host 127.0.0.1 --port "${PORT}"
