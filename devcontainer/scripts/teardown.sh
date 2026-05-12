#!/usr/bin/env bash
# Stop all neobanker dev services + containers (volumes preserved unless -v)
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[+] Stopping application processes…"
pkill -f "spring-boot:run"            2>/dev/null || true
pkill -f "uvicorn main:app"           2>/dev/null || true
pkill -f "next dev"                   2>/dev/null || true
pkill -f "node .*next dev"            2>/dev/null || true

echo "[+] Stopping infra containers…"
DC="docker compose"; docker compose version >/dev/null 2>&1 || DC="docker-compose"
$DC -f "$SCRIPT_DIR/docker-compose.dev.yml" --profile full down "${@}"

if [ "${1:-}" = "-v" ] || [ "${1:-}" = "--volumes" ]; then
  echo "[!] Volumes were also removed. Re-run bootstrap.sh to re-import data."
fi
echo "[+] Done."
