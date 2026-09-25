#!/usr/bin/env bash
#
# Start mitmproxy for the Android emulator: check tools, install the CA once,
# then run mitmdump until this script exits.
#
#   ./android/start-proxy.sh                    # stop with Ctrl-C (or kill this script)
#   AVD=Pixel_10a PORT=8081 ./android/start-proxy.sh
#
# The debug build must trust user-installed CAs (see README). This script puts
# the CA in the user store and leaves /system alone. A booted emulator stays up.

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
PORT="${PORT:-8080}"
AVD="${AVD:-}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-240}"
SERIAL="${SERIAL:-${ANDROID_SERIAL:-}}"
UDID="${UDID:-}"
TRUST_ONLY=0
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
USER_CA_DIR="/data/misc/user/0/cacerts-added"
EMULATOR_LOG=""
PROXY_PID=""
STATE_ACQUIRED=0
STATE_DIR=""

configure_python_path
parse_launcher_args "$@"
validate_common_files
DEVICE_PROXY="10.0.2.2:${PORT}"
[ -z "$UDID" ] || fail 'arguments' '--udid is only valid for iOS'
[ "$TRUST_ONLY" -eq 0 ] || fail 'arguments' '--trust-only is only valid for iOS'
require_config_file
if [ "${#ADDON_SCRIPTS[@]}" -gt 0 ]; then
  for script in "${ADDON_SCRIPTS[@]}"; do
    [ -f "$script" ] || fail 'addon' "missing: $script"
  done
fi

select_mitmproxy

preflight_tools() {
  local missing=() hints=() c warm_pid t
  for c in adb openssl lsof; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    for c in "${missing[@]}"; do
      # shellcheck disable=SC2016 # hint is copy-paste text — $PATH must stay literal
      case "$c" in
        adb) hints+=('adb: install Android Studio, then export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools" — https://developer.android.com/studio') ;;
        *) hints+=("$c: not found — check your PATH") ;;
      esac
    done
    fail 'tools' "missing: ${missing[*]}" "${hints[@]}"
  fi
  [ -f "$ROUTER" ] || fail 'tools' 'local_router.py missing (complete checkout required)'
  if [ "${#ADDON_SCRIPTS[@]}" -gt 0 ]; then
    for script in "${ADDON_SCRIPTS[@]}"; do
      [ -f "$script" ] || fail 'tools' "addon missing: $script"
    done
  fi

  # Warm the selected executable before boot; this also absorbs the first uv
  # download. Announce it only when startup is actually slow.
  "${MITMDUMP[@]}" --version >/dev/null 2>&1 &
  warm_pid=$!
  t=0
  while kill -0 "$warm_pid" 2>/dev/null && [ "$t" -lt 150 ]; do
    sleep 0.1
    t=$((t + 1))
  done
  if kill -0 "$warm_pid" 2>/dev/null; then
    if [ "$MITMPROXY_SOURCE" != "host mitmdump" ]; then
      info '…' 'mitmproxy' "resolving $MITMPROXY_SOURCE"
    else
      info '…' 'mitmproxy' 'host mitmdump — starting'
    fi
  fi
  wait "$warm_pid" ||
    fail 'mitmproxy' "could not run ${MITMDUMP[*]} --version" \
      "check: ${MITMDUMP[*]} --version" \
      'https://docs.mitmproxy.org/stable/'
  check_mitmproxy_version
  info '✓' 'tools' 'adb, openssl, lsof'
}

find_emulator() {
  if [ -n "$SERIAL" ]; then
    adb devices | awk -v selected="$SERIAL" '$1 == selected && $2 == "device" { found=1 } END { exit(found ? 0 : 1) }' ||
      fail 'emulator' "serial is not running: $SERIAL" \
        'start the emulator first or omit --serial to boot an AVD'
    printf '%s' "$SERIAL"
    return
  fi
  adb devices | awk '$2 == "device" && $1 ~ /^emulator-/ { print $1; exit }'
}

wait_boot() {
  local deadline=$((SECONDS + BOOT_TIMEOUT))
  until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
    [ "$SECONDS" -lt "$deadline" ] || fail 'emulator' "not finished booting after ${BOOT_TIMEOUT}s" \
      'Watch the emulator window, check: adb devices' \
      "Log: ${EMULATOR_LOG:-n/a}"
    sleep 2
  done
}

boot_emulator() {
  local serial pending avd start root_out deadline
  serial="$(find_emulator)"
  if [ -n "$serial" ]; then
    export ANDROID_SERIAL="$serial"
    info '✓' 'emulator' "$serial running"
  else
    pending="$(adb devices | awk '$1 ~ /^emulator-/ && $2 != "device" { print $1; exit }')"
    if [ -n "$pending" ]; then
      export ANDROID_SERIAL="$pending"
      info '…' 'emulator' "waiting for $pending"
      wait_boot
      info '✓' 'emulator' "$pending ready"
      serial="$pending"
    else
      # shellcheck disable=SC2016 # hint is copy-paste text — $PATH must stay literal
      command -v emulator >/dev/null 2>&1 || fail 'emulator' 'not on PATH' \
        'Install Android Studio, then: export PATH="$PATH:$HOME/Library/Android/sdk/emulator" — https://developer.android.com/studio'
      if [ -n "${AVD:-}" ]; then
        avd="$AVD"
        emulator -list-avds 2>/dev/null | grep -Fxq "$avd" ||
          fail 'emulator' "AVD '$avd' does not exist" "Available: $(emulator -list-avds 2>/dev/null | paste -sd', ' -)"
      else
        avd="$(emulator -list-avds 2>/dev/null | sed '/^$/d' | head -1)"
        [ -n "$avd" ] || fail 'emulator' 'no AVDs found' \
          'Create one: Android Studio → Tools → Device Manager (Google APIs image)' \
          'https://developer.android.com/studio/run/managing-avds'
      fi
      EMULATOR_LOG="${TMPDIR:-/tmp}/emulator-${avd}.log"
      start=$SECONDS
      info '…' 'emulator' "booting $avd"
      { set -m; } 2>/dev/null
      emulator -avd "$avd" >"$EMULATOR_LOG" 2>&1 &
      { set +m; } 2>/dev/null
      serial=''
      deadline=$((SECONDS + 60))
      while [ -z "$serial" ]; do
        serial="$(find_emulator)"
        [ -n "$serial" ] && break
        [ "$SECONDS" -lt "$deadline" ] || fail 'emulator' 'never showed up in adb devices' \
          "Log: $EMULATOR_LOG"
        sleep 1
      done
      export ANDROID_SERIAL="$serial"
      wait_boot
      info '✓' 'emulator' "$avd booted in $((SECONDS - start))s"
    fi
  fi
  SERIAL="$serial"
  state_write serial "$serial"

  # Root is only needed to install the CA into the user trust store.
  root_out="$(adb root 2>&1 || true)"
  case "$root_out" in
    *'cannot run as root'*)
      fail 'root' 'this AVD image refuses adb root' \
        'Use a "Google APIs" image, not "Google APIs Play Store" — see README.md, Requirements' ;;
  esac
  adb wait-for-device
}

ensure_device_ca() {
  local hash remote out
  hash="$(openssl x509 -inform PEM -subject_hash_old -in "$CERT" | head -1)" ||
    fail 'device CA' "could not hash $CERT"
  remote="$USER_CA_DIR/${hash}.0"
  if adb shell "test -f $remote" 2>/dev/null; then
    info '✓' 'device CA' "${hash}.0 in user store"
    state_write ca_hash "$hash"
    return 0
  fi
  info '…' 'device CA' "installing ${hash}.0 — one reboot, once per AVD"
  adb shell "mkdir -p $USER_CA_DIR && chmod 755 $USER_CA_DIR" ||
    fail 'device CA' "cannot create $USER_CA_DIR" 'check: adb root'
  out="$(adb push "$CERT" "$remote" 2>&1)" ||
    fail 'device CA' "could not push ${hash}.0" "${out##*$'\n'}"
  out="$(adb shell "chmod 644 $remote && restorecon $remote $USER_CA_DIR" 2>&1)" || {
    adb shell "rm -f $remote" >/dev/null 2>&1 || true
    fail 'device CA' 'permissions/SELinux label failed (rolled back)' "${out##*$'\n'}"
  }
  adb reboot
  wait_boot
  adb root >/dev/null 2>&1 || true
  adb wait-for-device
  adb shell "test -f $remote" 2>/dev/null ||
    fail 'device CA' 'cert did not survive reboot' "check: adb root && adb shell ls $USER_CA_DIR"
  info '✓' 'device CA' "${hash}.0 trusted"
  state_write ca_hash "$hash"
}

capture_device_proxy() {
  local current
  if ! current="$(adb shell settings get global http_proxy 2>/dev/null | tr -d '\r')"; then
    fail 'proxy' 'could not read the existing Android proxy setting' \
      'check: adb shell settings get global http_proxy'
  fi
  if [ -z "$current" ] || [ "$current" = "null" ]; then
    state_write previous_proxy '__PROXY_LAB_NULL__'
  else
    state_write previous_proxy "$current"
  fi
}

set_device_proxy() {
  local current
  if ! current="$(adb shell settings get global http_proxy 2>/dev/null | tr -d '\r')"; then
    fail 'proxy' 'could not read the Android proxy setting before changing it'
  fi

  if [ "$current" = "$DEVICE_PROXY" ]; then
    info '✓' 'proxy' "$DEVICE_PROXY"
  else
    adb shell settings put global http_proxy "$DEVICE_PROXY" ||
      fail 'proxy' "could not set $DEVICE_PROXY" 'try: adb shell settings get global http_proxy'
    info '✓' 'proxy' "$DEVICE_PROXY set"
  fi
}

free_port() {
  local pids pid args
  pids="$(lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    for pid in $pids; do
      args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
      fail "port $PORT" "already in use by PID $pid (${args:-unknown})" \
        'Stop the recorded proxy-lab session with: proxy-lab stop android' \
        'or choose another PORT'
    done
  fi
  info '✓' "port $PORT" 'free'
}

cleanup() {
  trap - EXIT INT TERM HUP
  local restored=1
  if [ -n "$PROXY_PID" ]; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
  if [ "$STATE_ACQUIRED" -eq 1 ]; then
    if ! state_restore_android_proxy "$STATE_DIR"; then
      restored=0
      printf '  ! %-11s %s\n' 'cleanup' 'could not restore the Android proxy; run: proxy-lab reset android' >&2
    fi
    if [ "$restored" -eq 1 ]; then
      state_release
    fi
  fi
  if [ "$restored" -eq 1 ] && [ -n "$PROXY_PID" ]; then
    info '✓' 'mitmdump' 'stopped — device proxy restored'
  fi
}

start_proxy() {
  local up=''
  mitmproxy_addon_args
  if [ "${#MITMPROXY_ADDON_ARGS[@]}" -gt 0 ]; then
    "${MITMDUMP[@]}" --listen-host 0.0.0.0 --listen-port "$PORT" --set ssl_insecure=true \
      -s "$ROUTER" "${MITMPROXY_ADDON_ARGS[@]}" &
  else
    "${MITMDUMP[@]}" --listen-host 0.0.0.0 --listen-port "$PORT" --set ssl_insecure=true \
      -s "$ROUTER" &
  fi
  PROXY_PID=$!
  state_write proxy_pid "$PROXY_PID"
  trap cleanup EXIT
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
  trap 'cleanup; exit 129' HUP
  for _ in {1..20}; do
    lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && { up=1; break; }
    kill -0 "$PROXY_PID" 2>/dev/null || break
    sleep 0.25
  done
  if [ -z "$up" ]; then
    wait "$PROXY_PID" 2>/dev/null || true
    fail 'mitmdump' "exited before listening on :$PORT" 'the output above says why'
  fi
  info '✓' 'mitmdump' "0.0.0.0:$PORT — Ctrl-C to stop"
  printf '\n'
  wait "$PROXY_PID"
}

preflight_tools
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP
state_acquire android "$PORT"
ensure_host_ca
boot_emulator
capture_device_proxy
ensure_device_ca
free_port
set_device_proxy
start_proxy
