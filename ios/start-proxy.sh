#!/usr/bin/env bash
#
# Start mitmdump for the iOS simulator with the shared local_router addon.
# The simulator shares the host's network — no device changes, no root.
# Stop with Ctrl-C (or kill this script); uv takes the proxy down with it.
#
#   ./ios/start-proxy.sh                    # PORT=8081 ./ios/start-proxy.sh
#
# Point your debug build at localhost:8080. If HTTPS reports a trust error,
# install the CA once in the simulator: http://mitm.it

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROUTER="$(cd "$SCRIPT_DIR/.." && pwd)/local_router.py"
CONFIG="${PROXY_LAB_CONFIG:-$(cd "$SCRIPT_DIR/.." && pwd)/domains.yaml}"
PORT="${PORT:-8080}"
MITMPROXY_VERSION="12.2.3"
MITMDUMP=(uv tool run --from "mitmproxy==$MITMPROXY_VERSION" mitmdump)

fail() {
  printf '  ✗ %s\n' "$1" >&2
  exit 1
}

command -v uv >/dev/null ||
  fail 'uv not found — brew install uv — https://docs.astral.sh/uv/getting-started/installation/'
[ -f "$ROUTER" ] ||
  fail "router missing: $ROUTER — use a complete checkout of this repo"
[ -f "$CONFIG" ] ||
  fail "config missing: $CONFIG — use a complete checkout of this repo, or pass an existing domains file"

# exec: this script becomes mitmdump (via uv), so killing it kills the proxy.
exec "${MITMDUMP[@]}" \
  --listen-host 0.0.0.0 \
  --listen-port "$PORT" \
  --set ssl_insecure=true \
  -s "$ROUTER"
