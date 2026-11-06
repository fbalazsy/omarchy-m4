#!/usr/bin/env bash
# Termux launcher — no root. Uses Termux python (pkg) only.
set -eu
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 missing. In Termux run:" >&2
  echo "  pkg install python" >&2
  exit 1
fi
HERE="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
export LAN_PORT="${LAN_PORT:-8780}"
export MCP_PORT="${MCP_PORT:-8781}"
exec python3 "$HERE/phone_relay.py"
