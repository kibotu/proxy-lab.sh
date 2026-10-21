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
# Optional. Without a domains file, the hosts in /etc/hosts are what gets captured.
CONFIG="${PROXY_LAB_CONFIG:-}"
# Local capture has no listen port. State uses 0 as the slot name.
PORT=0
UDID="${UDID:-}"
TRUST_ONLY=0
# Opt-in: reach hosts that /etc/hosts points at localhost. See --system-proxy.
SYSTEM_PROXY="${PROXY_LAB_SYSTEM_PROXY:-0}"
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
# The one mode that may write to the Mac's network settings. Everything else
# reads them and leaves them alone.
if [ "$SYSTEM_PROXY" -eq 1 ]; then
  # shellcheck disable=SC2034 # consumed by proxy_lab/common.sh
  SYSTEM_CHANGES_ALLOWED=1
fi
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
  # A second Ctrl-C must not interrupt the teardown it is waiting on.
  trap '' INT TERM HUP
  trap - EXIT USR1
  local waited=0 restore_failed=0
  if [ -n "${DURATION_PID:-}" ]; then
    kill "$DURATION_PID" 2>/dev/null || true
  fi
  # The Mac's proxy setting goes back first. mitmdump can linger on open
  # connections, and the Mac must not stay pointed at it meanwhile. Only a run
  # that installed an auto-config has one to put back.
  if [ "$SYSTEM_PROXY" -eq 1 ] && [ "$STATE_ACQUIRED" -eq 1 ] &&
    ! state_restore_ios_proxy "$STATE_DIR"; then
    restore_failed=1
  fi
  if [ -n "$PROXY_PID" ]; then
    kill "$PROXY_PID" 2>/dev/null || true
    while kill -0 "$PROXY_PID" 2>/dev/null && [ "$waited" -lt 20 ]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    kill -KILL "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
  if [ "$restore_failed" -eq 1 ]; then
    printf '  ! %-11s %s\n' 'cleanup' "couldn't put your Mac's proxy setting back" >&2
    printf '      ↳ %s\n' 'run: proxy-lab reset ios' >&2
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
if [ "$SYSTEM_PROXY" -eq 1 ]; then
  # This mode owns the Mac's settings, so it also clears the ones an earlier
  # run left behind before it installs one of its own.
  ios_clear_leftover_pacs
else
  # Report, never fix: a plain start must not change the Mac's settings.
  orphan="$(ios_orphan_pacs | head -n 1)"
  if [ -n "$orphan" ]; then
    info '!' 'proxy' "an auto-config from an earlier run is still set on ${orphan%%|*} — run: proxy-lab reset ios"
  fi
fi
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
# mitmdump so macOS can install the extension and ask. The system-proxy route
# does not use the extension.
if [ "$SYSTEM_PROXY" -eq 0 ]; then
  case "$(mitmproxy_extension_state)" in
    pending)
      fail 'extension' "mitmproxy's network extension needs your approval" \
        'allow it in System Settings > General > Login Items & Extensions > Network Extensions' \
        "or jump there: open \"$EXTENSION_SETTINGS_PANE\"" \
        'then run: proxy-lab start ios'
      ;;
  esac
fi

if [ "$SYSTEM_PROXY" -eq 1 ]; then
  PROXY_PORT="$(free_local_port)" ||
    fail 'proxy' "couldn't find a free port" 'python3 is required for --system-proxy'
  MODE_ARGS=(--listen-host 127.0.0.1 --listen-port "$PROXY_PORT")
else
  MODE_ARGS=(--mode local:Simulator)
  loopback_names="$(loopback_hosts | paste -sd' ' -)"
  if [ -n "$loopback_names" ]; then
    info '!' 'localhost' "$(printf '%s' "$loopback_names" | wc -w | tr -d ' ') /etc/hosts names point at localhost, which local capture can't see"
    info ' ' 'localhost' 'to capture them: proxy-lab start ios --system-proxy'
  fi
fi

"${MITMDUMP[@]}" \
  "${MODE_ARGS[@]}" \
  --showhost \
  --set ssl_insecure=true \
  "${MITMDUMP_SCRIPT_ARGS[@]}" &
PROXY_PID=$!
state_write proxy_pid "$PROXY_PID"
# A non-zero exit is a failed start, a clean exit is a finished run, and a
# live process is up. With a listen port, up also means the port answers.
up=''
for attempt in {1..20}; do
  kill -0 "$PROXY_PID" 2>/dev/null || break
  if [ "$SYSTEM_PROXY" -eq 1 ]; then
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

if [ "$SYSTEM_PROXY" -eq 1 ]; then
  # Only now, with the proxy listening: the Mac is never pointed at nothing.
  service="$(ios_network_service || true)"
  [ -n "$service" ] || fail 'proxy' "couldn't find your active network service" \
    'connect to a network and try again'
  pac_url="$(ios_pac_serve "127.0.0.1:$PROXY_PORT")" ||
    fail 'proxy' "couldn't serve the auto-config" 'python3 is required for --system-proxy'
  # Record first, change second: a crash in between still restores cleanly.
  ios_auto_config_write "$service" "$(ios_autoproxy_read "$service")"
  networksetup -setautoproxyurl "$service" "$pac_url" >/dev/null 2>&1 ||
    fail 'proxy' "couldn't set the proxy on $service" \
      'macOS may want admin rights — try again from an admin account' \
      'or run without --system-proxy'
  state_write port "$PROXY_PORT"
  info '✓' 'proxy' "$service routes only your hosts here; the rest stays direct"
  info ' ' 'proxy' 'put back on stop; if anything goes wrong: proxy-lab reset ios'
fi
mark_session_ready
# First run installs the extension and macOS asks. Say so now, rather than
# letting an empty log be the first hint.
if [ "$SYSTEM_PROXY" -eq 0 ] && [ "$(mitmproxy_extension_state)" = 'pending' ]; then
  info '!' 'extension' "approve it to start capturing: open \"$EXTENSION_SETTINGS_PANE\""
fi
if [ "$SYSTEM_PROXY" -eq 1 ]; then
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
