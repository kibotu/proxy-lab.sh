#!/usr/bin/env bash
#
# Start mitmdump in macOS local-capture mode for the iOS Simulator.
# Start a simulator in Xcode or Device Hub first; this script does not boot one.
# The process filter is intended to cover both launch paths. No proxy settings
# or fixed port are needed.
# Stop with Ctrl-C (or kill this script).
#
#   ./ios/start-proxy.sh
#
# The first run may ask you to allow mitmproxy's network extension.
# HTTPS interception still requires the simulator to trust the CA:
# http://mitm.it

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROUTER="$(cd "$SCRIPT_DIR/.." && pwd)/local_router.py"
CONFIG="${PROXY_LAB_CONFIG:-$(cd "$SCRIPT_DIR/.." && pwd)/domains.yaml}"
# Local mode is available in mitmproxy 10.1.5+.
MITMPROXY_MIN_LOCAL_VERSION="10.1.5"

info() { printf '  %s %-11s %s\n' "$1" "$2" "$3"; }

fail() {
  local label="$1" msg="$2" hint
  shift 2
  printf '  ✗ %-11s %s\n' "$label" "$msg" >&2
  for hint in "$@"; do printf '      ↳ %s\n' "$hint" >&2; done
  exit 1
}

# Prefer a host binary. The @latest spec asks uv to refresh its cached tool.
if command -v mitmdump >/dev/null 2>&1; then
  MITMDUMP=(mitmdump)
  MITMPROXY_SOURCE="host mitmdump"
elif command -v uv >/dev/null 2>&1; then
  MITMDUMP=(uv tool run --from 'mitmproxy@latest' mitmdump)
  MITMPROXY_SOURCE="uv latest"
else
  fail 'mitmproxy' 'mitmdump not found and uv is not installed — brew install --cask mitmproxy or brew install uv'
fi

mitmproxy_version() {
  "${MITMDUMP[@]}" --version 2>/dev/null |
    sed -nE 's/^Mitmproxy( version)?:[[:space:]]*([^[:space:]]+).*/\2/p' |
    head -n 1
}

latest_mitmproxy_version() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -fsSL --max-time 5 https://pypi.org/pypi/mitmproxy/json 2>/dev/null |
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' |
    head -n 1
}

version_is_older() {
  [ "$1" != "$2" ] &&
    [ "$1" = "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1)" ]
}

check_mitmproxy_version() {
  local current latest
  current="$(mitmproxy_version || true)"
  [ -n "$current" ] ||
    fail 'mitmproxy' "could not run ${MITMDUMP[*]} --version" \
      "check: ${MITMDUMP[*]} --version" \
      'https://docs.mitmproxy.org/stable/'
  if version_is_older "$current" "$MITMPROXY_MIN_LOCAL_VERSION"; then
    fail 'iOS mode' "mitmproxy $current is too old for local capture (need $MITMPROXY_MIN_LOCAL_VERSION+)" \
      'upgrade mitmproxy, or remove it and install uv to use the latest fallback'
  fi

  info '✓' 'mitmproxy' "$current via $MITMPROXY_SOURCE"
  latest="$(latest_mitmproxy_version || true)"
  if [ -n "$latest" ] && version_is_older "$current" "$latest"; then
    info 'i' 'mitmproxy' "newer version available: $latest — https://pypi.org/project/mitmproxy/"
  fi
}

[ -f "$ROUTER" ] ||
  fail 'router' "missing: $ROUTER — use a complete checkout of this repo"
[ -f "$CONFIG" ] ||
  fail 'config' "missing: $CONFIG — use a complete checkout of this repo, or pass an existing domains file"

check_mitmproxy_version

# Local debugging only: this disables upstream certificate verification.
# Replace this shell with the selected mitmdump command.
exec "${MITMDUMP[@]}" \
  --mode local:Simulator \
  --showhost \
  --set ssl_insecure=true \
  -s "$ROUTER"
