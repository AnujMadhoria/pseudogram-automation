#!/bin/sh
set -eu

# Free Render instances permit one web service only. Run the durable worker
# alongside Uvicorn in that service; both share the same Supabase database.
python -m app.worker &
worker_pid=$!

cleanup() {
  kill -TERM "$worker_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"

