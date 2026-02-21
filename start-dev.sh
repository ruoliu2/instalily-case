#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/instalily-case-backend"
FRONTEND_DIR="${ROOT_DIR}/instalily-case-front"

BACKEND_PORT="${APP_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

PIDS=()

cleanup() {
  trap - EXIT INT TERM
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    kill "${PIDS[@]}" >/dev/null 2>&1 || true
    wait "${PIDS[@]}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting backend on http://localhost:${BACKEND_PORT}"
(
  cd "${BACKEND_DIR}"
  APP_PORT="${BACKEND_PORT}" uv run start 2>&1 | sed 's/^/[backend] /'
) &
PIDS+=("$!")

echo "Starting frontend on http://localhost:${FRONTEND_PORT}"
(
  cd "${FRONTEND_DIR}"
  PORT="${FRONTEND_PORT}" bun run dev 2>&1 | sed 's/^/[frontend] /'
) &
PIDS+=("$!")

echo "Both services started. Press Ctrl+C to stop."
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      exit 1
    fi
  done
  sleep 1
done
