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
  if [ "$STATE_ACQUIRED" -eq 1 ] && ! state_restore_ios_proxy "$STATE_DIR"; then
    printf '  ! %-11s %s\n' 'cleanup' "couldn't restore your Mac proxy — run: proxy-lab reset ios" >&2
    return 0
  fi
  state_release
}

if [ "$TRUST_ONLY" -eq 1 ]; then
  ensure_host_ca
  trust_status=0
  trust_ios_certificate || trust_status=$?
  if [ "$trust_status" -ne 0 ]; then
    case "$trust_status" in
      2) fail 'simulator' "xcrun isn't available" 'install Xcode and its command-line tools' ;;
      3) fail 'simulator' 'no Simulator is booted' 'boot one in Xcode, or pass --udid' ;;
      4) fail 'simulator' "no Xcode with a usable simulator toolchain ($(xcode-select -p 2>/dev/null || echo 'unknown selection'))" \
        'select a full Xcode, then: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer' \
        'or point proxy-lab at one for this run: DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer proxy-lab start ios' ;;
      *) fail 'simulator' "couldn't add the CA to the Simulator" 'open mitm.it in the Simulator and install the profile' ;;
    esac
  fi
  exit 0
fi

trap_cleanup
state_acquire ios 0
ensure_host_ca

trust_status=0
trust_ios_certificate || trust_status=$?
if [ "$trust_status" -eq 2 ]; then
  fail 'simulator' "xcrun isn't available" \
    'install Xcode and its command-line tools'
elif [ "$trust_status" -eq 4 ]; then
  fail 'simulator' "no Xcode with a usable simulator toolchain ($(xcode-select -p 2>/dev/null || echo 'unknown selection'))" \
    'select a full Xcode, then: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer' \
    'or point proxy-lab at one for this run: DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer proxy-lab start ios'
elif [ "$trust_status" -ne 0 ]; then
  # The capture itself still works; only the automatic trust step is lost.
  info '!' 'simulator' "couldn't trust the CA automatically — open mitm.it in the Simulator to finish"
fi

# An installed-but-unapproved extension captures nothing while mitmdump still
# reports itself healthy. Fail here rather than hand back a proxy that logs
# nothing and looks fine. `absent` is the first run, which still needs to start
# mitmdump so macOS can install the extension and ask.
case "$(ios_capture_mode)$(mitmproxy_extension_state)" in
  localpending)
    fail 'extension' "mitmproxy's network extension needs your approval" \
      'allow it in System Settings > General > Login Items & Extensions > Network Extensions' \
      "or jump there: open \"$EXTENSION_SETTINGS_PANE\"" \
      'then run: proxy-lab start ios' \
      'or skip the extension: PROXY_LAB_IOS_MODE=proxy proxy-lab start ios'
    ;;
esac

CAPTURE_MODE="$(ios_capture_mode)"
PROXY_PORT="${PROXY_LAB_IOS_PORT:-8080}"
if [ "$CAPTURE_MODE" = "proxy" ]; then
  # Local capture cannot see loopback, and your dev hosts live there. The
  # system proxy can: mitmproxy resolves names through /etc/hosts like any app.
  if lsof -t -nP -iTCP:"$PROXY_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $PROXY_PORT" 'already in use' 'free it, or pick another: PROXY_LAB_IOS_PORT=<port> proxy-lab start ios'
  fi
  info '✓' 'mode' "system proxy — $(loopback_hosts | wc -l | tr -d ' ') dev hosts in /etc/hosts point at localhost, beyond local capture's reach"
  MODE_ARGS=(--listen-port "$PROXY_PORT")
else
  MODE_ARGS=(--mode local:Simulator)
fi

"${MITMDUMP[@]}" \
  "${MODE_ARGS[@]}" \
  --showhost \
  --set ssl_insecure=true \
  "${MITMDUMP_SCRIPT_ARGS[@]}" &
PROXY_PID=$!
state_write proxy_pid "$PROXY_PID"
# A non-zero exit is a failed start, a clean exit is a finished run, and a
# live process is up. In proxy mode "up" also means the port is listening.
up=''
for attempt in {1..20}; do
  kill -0 "$PROXY_PID" 2>/dev/null || break
  if [ "$CAPTURE_MODE" = "proxy" ]; then
    lsof -t -nP -iTCP:"$PROXY_PORT" -sTCP:LISTEN >/dev/null 2>&1 && { up=1; break; }
  elif [ "$attempt" -ge 8 ]; then
    up=1; break
  fi
  sleep 0.25
done
if [ -z "$up" ] || ! kill -0 "$PROXY_PID" 2>/dev/null; then
  proxy_status=0
  wait "$PROXY_PID" || proxy_status=$?
  PROXY_PID=""
  [ "$proxy_status" -eq 0 ] && exit 0
  fail 'mitmdump' "didn't start (status $proxy_status)" \
    'the output above says why' \
    'first run? approve the mitmproxy network extension when macOS asks'
fi

if [ "$CAPTURE_MODE" = "proxy" ]; then
  service="$(ios_network_service || true)"
  [ -n "$service" ] || fail 'proxy' "couldn't find your active network service" \
    'connect to a network, or: PROXY_LAB_IOS_MODE=local proxy-lab start ios'
  # Record first, change second: a crash in between still restores cleanly.
  state_write previous_ios_web "$(ios_proxy_read "$service" web)"
  state_write previous_ios_secureweb "$(ios_proxy_read "$service" secureweb)"
  state_write previous_ios_service "$service"
  networksetup -setwebproxy "$service" 127.0.0.1 "$PROXY_PORT" >/dev/null 2>&1 &&
    networksetup -setsecurewebproxy "$service" 127.0.0.1 "$PROXY_PORT" >/dev/null 2>&1 ||
    fail 'proxy' "couldn't set the proxy on $service" \
      'macOS may want admin rights — try once more from an admin account' \
      'or use local capture: PROXY_LAB_IOS_MODE=local proxy-lab start ios'
  state_write port "$PROXY_PORT"
  info '✓' 'proxy' "$service → 127.0.0.1:$PROXY_PORT (restored on stop)"
fi
mark_session_ready
# First run installs the extension and macOS asks. Say so now, rather than
# letting an empty log be the first hint. Proxy mode does not need it.
if [ "$CAPTURE_MODE" = "local" ] && [ "$(mitmproxy_extension_state)" = 'pending' ]; then
  info '!' 'extension' "approve it to start capturing: open \"$EXTENSION_SETTINGS_PANE\""
fi
if [ "$CAPTURE_MODE" = "proxy" ]; then
  target="127.0.0.1:$PROXY_PORT"
else
  target='local:Simulator'
fi
if [ -n "${DURATION:-}" ]; then
  info '✓' 'mitmdump' "$target — stopping in ${DURATION}s"
  start_duration_watchdog
else
  info '✓' 'mitmdump' "$target — Ctrl-C to stop"
fi
printf '\n'
wait "$PROXY_PID"
