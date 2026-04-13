#!/usr/bin/env bash
# Tear down anything the start launcher left behind.
#
# Use when:
#   - Ctrl+C didn't propagate (start script ran detached, terminal closed,
#     etc.) and you have orphan processes holding :5001 or :5173.
#   - You want a clean slate before re-running ./scripts/start.sh.
#
# Strategy: kill everything bound to the two known ports (gateway 5001,
# vite 5173) plus anything matching the start orchestrator. Idempotent —
# safe to run even if nothing's up.

set -u

killed_any=0

# Port-based kill: catches vite (node) and the Java gateway regardless of
# how they were spawned. lsof -t prints just PIDs.
PORT_PIDS=$(lsof -t -i :5001 -i :5173 2>/dev/null || true)
if [[ -n "$PORT_PIDS" ]]; then
  echo "→ killing port holders on :5001 / :5173: $PORT_PIDS"
  # shellcheck disable=SC2086
  kill $PORT_PIDS 2>/dev/null || true
  sleep 1
  # Escalate survivors.
  # shellcheck disable=SC2086
  kill -9 $PORT_PIDS 2>/dev/null || true
  killed_any=1
fi

# Process-name kill: catches the start.sh orchestrator itself and any
# npm/gateway parents that weren't holding a port at the moment we looked.
for pattern in 'scripts/start.sh' 'ibPortal/bin/run.sh' 'clientportal.gw'; do
  PIDS=$(pgrep -f "$pattern" 2>/dev/null || true)
  if [[ -n "$PIDS" ]]; then
    echo "→ killing processes matching '$pattern': $PIDS"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    killed_any=1
  fi
done

if [[ "$killed_any" -eq 0 ]]; then
  echo "  nothing to stop — ports :5001 and :5173 are already free."
fi
