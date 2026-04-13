#!/usr/bin/env bash
# One-shot dev launcher for ibscanner.
#
#   1. Boot the IBKR Client Portal Gateway (Java bundle under ibPortal/).
#   2. Wait for its TLS port to accept connections, then open the login
#      page in the default browser.
#   3. Boot Vite and, once it's serving, open the app in a second tab.
#   4. Tail both processes in the foreground. Ctrl+C takes everything
#      down — the EXIT trap kills the gateway even if Vite crashes first.
#
# Assumes:
#   - macOS `open` exists (falls back to xdg-open on Linux).
#   - Gateway config listens on 5001 (see ibPortal/root/conf.yaml).
#   - Vite's default port 5173 is free.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATEWAY_DIR="${REPO_ROOT}/ibPortal"
GATEWAY_CONF="./root/conf.yaml"
GATEWAY_URL="https://localhost:5001"
APP_URL="http://localhost:5173"
GATEWAY_LOG="${REPO_ROOT}/.gateway.log"

# Pick whichever browser-launcher the OS provides.
open_url() {
  if command -v open >/dev/null 2>&1; then
    open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1"
  else
    echo "  (no 'open' / 'xdg-open' — visit $1 manually)"
  fi
}

# Poll a TCP port until it accepts a connection or we time out. Used for
# both gateway (5001) and Vite (5173) readiness — neither advertises a
# "ready" signal over stdout that's reliable to grep for.
wait_for_port() {
  local port="$1"
  local name="$2"
  local tries=60
  while (( tries-- > 0 )); do
    if (echo > "/dev/tcp/127.0.0.1/${port}") >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "  $name never came up on :${port}" >&2
  return 1
}

cleanup() {
  if [[ -n "${GATEWAY_PID:-}" ]] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
    echo ""
    echo "→ stopping gateway (pid $GATEWAY_PID)"
    kill "$GATEWAY_PID" 2>/dev/null || true
    # Give it a moment, then SIGKILL if it's still around.
    sleep 1
    kill -9 "$GATEWAY_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ ! -d "$GATEWAY_DIR" ]]; then
  echo "✗ gateway not found at $GATEWAY_DIR" >&2
  echo "  install the IBKR Client Portal Gateway there first" >&2
  exit 1
fi

echo "→ starting Client Portal Gateway ($GATEWAY_URL)"
( cd "$GATEWAY_DIR" && ./bin/run.sh "$GATEWAY_CONF" ) \
  >"$GATEWAY_LOG" 2>&1 &
GATEWAY_PID=$!
echo "  pid=$GATEWAY_PID  log=$GATEWAY_LOG"

wait_for_port 5001 "gateway"
echo "→ opening $GATEWAY_URL to log in"
open_url "$GATEWAY_URL"

echo "→ starting Vite"
( cd "${REPO_ROOT}/client" && npm run dev ) &
VITE_PID=$!

wait_for_port 5173 "vite"
echo "→ opening $APP_URL"
open_url "$APP_URL"

echo ""
echo "  ibscanner is up."
echo "  gateway log: tail -f $GATEWAY_LOG"
echo "  Ctrl+C to stop both."
echo ""

# Wait on Vite in the foreground; if it exits, the EXIT trap stops the
# gateway. Using `wait "$VITE_PID"` so Ctrl+C is delivered to Vite and
# propagates back here cleanly.
wait "$VITE_PID"
