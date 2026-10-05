#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
test_database_url="postgresql+psycopg://finance:finance@127.0.0.1:5432/finance_browser_test"
test_storage_root=$(mktemp -d "${TMPDIR:-/tmp}/finance-browser-tests.XXXXXX")
api_pid=""
worker_pid=""
frontend_pid=""

cleanup() {
  for process_id in "$frontend_pid" "$worker_pid" "$api_pid"; do
    if [ -n "$process_id" ]; then
      kill "$process_id" 2>/dev/null || true
    fi
  done
  rm -rf "$test_storage_root"
}
trap cleanup EXIT INT TERM

cd "$project_dir"
docker compose up -d db
if [ "$(docker compose exec -T db psql -U finance -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='finance_browser_test'")" != "1" ]; then
  docker compose exec -T db createdb -U finance finance_browser_test
fi
FINANCE_DATABASE_URL="$test_database_url" .venv/bin/alembic upgrade head

FINANCE_ENVIRONMENT=test \
FINANCE_DATABASE_URL="$test_database_url" \
FINANCE_IMPORT_STORAGE_ROOT="$test_storage_root" \
PYTHONPATH="$project_dir/src" \
  .venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port 8011 &
api_pid=$!

FINANCE_ENVIRONMENT=test \
FINANCE_DATABASE_URL="$test_database_url" \
FINANCE_IMPORT_STORAGE_ROOT="$test_storage_root" \
PYTHONPATH="$project_dir/src" \
  .venv/bin/python -m apps.worker.main &
worker_pid=$!

cd "$project_dir/frontend"
npm run build
HOST=127.0.0.1 \
PORT=5174 \
FINANCE_FRONTEND_PROXY_TARGET=http://127.0.0.1:8011 \
  node scripts/serve.mjs &
frontend_pid=$!

wait "$frontend_pid"
