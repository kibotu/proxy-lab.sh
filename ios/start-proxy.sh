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

# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
ROUTER="$PROJECT_DIR/local_router.py"
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
CONFIG="${PROXY_LAB_CONFIG:-$PROJECT_DIR/domains.yaml}"
# Local capture has no listen port. State uses 0 as the slot name.
PORT=0
UDID="${UDID:-}"
TRUST_ONLY=0
AVD="${AVD:-}"
SERIAL="${SERIAL:-}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-}"
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
MITMPROXY_MIN_LOCAL_VERSION="10.1.5"
PROXY_PID=""
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
STATE_ACQUIRED=0
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
STATE_DIR=""
DURATION_PID=""
# shellcheck disable=SC2034 # set by proxy_lab/common.sh for the detach handoff
DETACH_PID=""
# shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
PLATFORM_NAME=ios

configure_python_path
parse_launcher_args "$@"
validate_common_files
[ "$PORT" -eq 0 ] || fail 'arguments' '--port is only valid for Android'
[ -z "$AVD" ] || fail 'arguments' '--avd is only valid for Android'
[ -z "$BOOT_TIMEOUT" ] || fail 'arguments' '--boot-timeout is only valid for Android'
[ -z "$SERIAL" ] || fail 'arguments' '--serial is only valid for Android'
run_detach_handoff "$SCRIPT_DIR/start-proxy.sh" "$@"
require_config_file
select_mitmproxy
check_mitmproxy_version "$MITMPROXY_MIN_LOCAL_VERSION"

cleanup() {
  trap - EXIT INT TERM HUP USR1
  if [ -n "${DURATION_PID:-}" ]; then
    kill "$DURATION_PID" 2>/dev/null || true
  fi
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

trap_cleanup
state_acquire ios 0
ensure_host_ca

trust_status=0
trust_ios_certificate || trust_status=$?
if [ "$trust_status" -ne 0 ]; then
  info '!' 'simulator' 'automatic CA trust unavailable; use mitm.it in the booted Simulator'
fi

"${MITMDUMP[@]}" \
  --mode local:Simulator \
  --showhost \
  --set ssl_insecure=true \
  "${MITMDUMP_SCRIPT_ARGS[@]}" &
PROXY_PID=$!
state_write proxy_pid "$PROXY_PID"
# Local capture has no port to poll. Give mitmdump a moment: a non-zero exit
# is a failed start, a clean exit is a finished run, and a live process is up.
for _ in 1 2 3 4 5 6 7 8; do
  kill -0 "$PROXY_PID" 2>/dev/null || break
  sleep 0.25
done
if ! kill -0 "$PROXY_PID" 2>/dev/null; then
  proxy_status=0
  wait "$PROXY_PID" || proxy_status=$?
  PROXY_PID=""
  [ "$proxy_status" -eq 0 ] && exit 0
  fail 'mitmdump' "exited during startup (status $proxy_status)" \
    'the output above says why' \
    'first run? approve the mitmproxy network extension when macOS asks'
fi
mark_session_ready
if [ -n "${DURATION:-}" ]; then
  info '✓' 'mitmdump' "local:Simulator — stopping in ${DURATION}s"
  start_duration_watchdog
else
  info '✓' 'mitmdump' 'local:Simulator — Ctrl-C to stop'
fi
printf '\n'
wait "$PROXY_PID"
