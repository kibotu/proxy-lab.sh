#!/usr/bin/env bash
#
# Start mitmdump in macOS local-capture mode for the iOS Simulator.
# Start a simulator in Xcode or Device Hub first; this script does not boot one.
# The process filter is intended to cover both launch paths. No proxy settings
# or fixed port are needed.
# Stop with Ctrl-C (or kill this script).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$SCRIPT_DIR/../common.sh" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/../common.sh"
else
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/../proxy_lab/common.sh"
fi

ROUTER="$PROJECT_DIR/local_router.py"
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
CONFIG="${PROXY_LAB_CONFIG:-$PROJECT_DIR/domains.yaml}"
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
PORT=0
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
VALIDATE_PORT=0
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
BOOT_TIMEOUT=1
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
AVD=""
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
SERIAL=""
UDID="${UDID:-}"
TRUST_ONLY=0
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
MITMPROXY_MIN_LOCAL_VERSION="10.1.5"
PROXY_PID=""
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
STATE_ACQUIRED=0
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
STATE_DIR=""

configure_python_path
parse_launcher_args "$@"
validate_common_files
[ "$PORT" -eq 0 ] || fail 'arguments' '--port is only valid for Android'
[ -z "$AVD" ] || fail 'arguments' '--avd is only valid for Android'
[ "$BOOT_TIMEOUT" -eq 1 ] || fail 'arguments' '--boot-timeout is only valid for Android'
[ -z "$SERIAL" ] || fail 'arguments' '--serial is only valid for Android'
require_config_file
if [ "${#ADDON_SCRIPTS[@]}" -gt 0 ]; then
  for script in "${ADDON_SCRIPTS[@]}"; do
    [ -f "$script" ] || fail 'addon' "missing: $script"
  done
fi

select_mitmproxy
check_mitmproxy_version "$MITMPROXY_MIN_LOCAL_VERSION"

cleanup() {
  trap - EXIT INT TERM HUP
  if [ -n "$PROXY_PID" ]; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
  state_release
}

if [ "$TRUST_ONLY" -eq 1 ]; then
  ensure_host_ca
  trust_status=0
  trust_ios_certificate || trust_status=$?
  if [ "$trust_status" -ne 0 ]; then
    case "$trust_status" in
      2) fail 'simulator' 'xcrun is not available' 'install Xcode and its command-line tools' ;;
      3) fail 'simulator' 'no booted iOS Simulator found' 'boot one in Xcode or pass --udid' ;;
      *) fail 'simulator' 'could not install the CA into the Simulator keychain' 'use mitm.it and trust the downloaded profile manually' ;;
    esac
  fi
  exit 0
fi

trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP
state_acquire ios 0
ensure_host_ca

trust_status=0
trust_ios_certificate || trust_status=$?
if [ "$trust_status" -ne 0 ]; then
  info '!' 'simulator' 'automatic CA trust unavailable; use mitm.it in the booted Simulator'
fi

mitmproxy_addon_args
if [ "${#MITMPROXY_ADDON_ARGS[@]}" -gt 0 ]; then
  "${MITMDUMP[@]}" \
    --mode local:Simulator \
    --showhost \
    --set ssl_insecure=true \
    -s "$ROUTER" "${MITMPROXY_ADDON_ARGS[@]}" &
else
  "${MITMDUMP[@]}" \
    --mode local:Simulator \
    --showhost \
    --set ssl_insecure=true \
    -s "$ROUTER" &
fi
PROXY_PID=$!
state_write proxy_pid "$PROXY_PID"
info '✓' 'mitmdump' 'local:Simulator — Ctrl-C to stop'
printf '\n'
wait "$PROXY_PID"
