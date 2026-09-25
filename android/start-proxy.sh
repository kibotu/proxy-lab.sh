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
ROUTER="$(cd "$SCRIPT_DIR/.." && pwd)/local_router.py"
CONFIG="${PROXY_LAB_CONFIG:-$(cd "$SCRIPT_DIR/.." && pwd)/domains.yaml}"
PORT="${PORT:-8080}"
DEVICE_PROXY="10.0.2.2:${PORT}" # 10.0.2.2 = the host, from the emulator's view
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
USER_CA_DIR="/data/misc/user/0/cacerts-added"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-240}"
LOCK="${TMPDIR:-/tmp}/start-proxy-${PORT}.lock"
EMULATOR_LOG=""

# A later run overwrites this. cleanup() clears the device proxy only when no
# live launcher remains recorded in the lock file, avoiding a handoff race.
printf '%s\n' "$$" >"$LOCK"

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

  info '✓' 'mitmproxy' "$current via $MITMPROXY_SOURCE"
  latest="$(latest_mitmproxy_version || true)"
  if [ -n "$latest" ] && version_is_older "$current" "$latest"; then
    info 'i' 'mitmproxy' "newer version available: $latest — https://pypi.org/project/mitmproxy/"
  fi
}

find_emulator() {
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

preflight_tools() {
  local missing=() hints=() c warm_pid t
  for c in adb openssl lsof; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    for c in "${missing[@]}"; do
      # shellcheck disable=SC2016 # hint is copy-paste text — $PATH must stay literal
      case "$c" in
        adb) hints+=('adb: install Android Studio, then export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools" — https://developer.android.com/studio') ;;
        *) hints+=("$c: not found — check your PATH") ;;
      esac
    done
    fail 'tools' "missing: ${missing[*]}" "${hints[@]}"
  fi
  [ -f "$ROUTER" ] ||
    fail 'tools' 'local_router.py missing (repo root)' 'use a complete checkout of this repo'
  [ -f "$CONFIG" ] ||
    fail 'tools' "config missing: $CONFIG" 'use a complete checkout of this repo'
  # Warm the selected executable before boot; this also absorbs the first
  # uv download. Announce it only when startup is actually slow.
  "${MITMDUMP[@]}" --version >/dev/null 2>&1 &
  warm_pid=$!
  t=0
  while kill -0 "$warm_pid" 2>/dev/null && [ "$t" -lt 15 ]; do sleep 0.1; t=$((t + 1)); done
  if kill -0 "$warm_pid" 2>/dev/null; then
    if [ "$MITMPROXY_SOURCE" = "uv latest" ]; then
      info '…' 'mitmproxy' 'resolving latest via uv'
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

ensure_host_ca() {
  if [ ! -f "$CERT" ]; then
    info '…' 'host CA' 'generating ~/.mitmproxy (first run)'
    # A mitmproxy command creates the CA on startup; port 0 avoids conflicts.
    "${MITMDUMP[@]}" --listen-port 0 >/dev/null 2>&1 &
    local gen=$!
    for _ in {1..25}; do [ -f "$CERT" ] && break; sleep 0.2; done
    kill "$gen" 2>/dev/null || true
    wait "$gen" 2>/dev/null || true
    [ -f "$CERT" ] || fail 'host CA' 'could not generate the mitmproxy CA' \
      "Run once: ${MITMDUMP[*]}" \
      'https://docs.mitmproxy.org/stable/concepts/certificates/'
  fi
  # shellcheck disable=SC2088 # display path — the literal ~ is what we mean
  info '✓' 'host CA' '~/.mitmproxy/mitmproxy-ca-cert.pem'
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
      { set -m; } 2>/dev/null # own process group, so Ctrl-C doesn't kill the emulator
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
    return 0
  fi
  info '…' 'device CA' "installing ${hash}.0 — one reboot, once per AVD"
  adb shell "mkdir -p $USER_CA_DIR && chmod 755 $USER_CA_DIR" ||
    fail 'device CA' "cannot create $USER_CA_DIR" 'check: adb root'
  out="$(adb push "$CERT" "$remote" 2>&1)" ||
    fail 'device CA' "could not push ${hash}.0" "${out##*$'\n'}"
  out="$(adb shell "chmod 644 $remote && restorecon $remote $USER_CA_DIR" 2>&1)" || {
    adb shell "rm -f $remote" >/dev/null 2>&1 || true # never leave an unlabeled cert behind
    fail 'device CA' 'permissions/SELinux label failed (rolled back)' "${out##*$'\n'}"
  }
  adb reboot
  wait_boot
  # adbd drops back to shell after reboot; wait before reading the user store.
  adb root >/dev/null 2>&1 || true
  adb wait-for-device
  adb shell "test -f $remote" 2>/dev/null ||
    fail 'device CA' 'cert did not survive reboot' "check: adb root && adb shell ls $USER_CA_DIR"
  info '✓' 'device CA' "${hash}.0 trusted"
}

set_device_proxy() {
  local current
  current="$(adb shell settings get global http_proxy 2>/dev/null | tr -d '\r')" || current=""
  if [ "$current" = "$DEVICE_PROXY" ]; then
    info '✓' 'proxy' "$DEVICE_PROXY"
  else
    adb shell settings put global http_proxy "$DEVICE_PROXY" ||
      fail 'proxy' "could not set $DEVICE_PROXY" 'try: adb shell settings get global http_proxy'
    info '✓' 'proxy' "$DEVICE_PROXY set"
  fi
}

free_port() {
  local pid args pids
  local stale=() foreign=()
  pids="$(lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    # This is a command-line heuristic, not proof that this script owns the port.
    case "$args" in
      *mitmdump*) stale+=("$pid") ;;
      *) foreign+=("$pid ${args:-unknown}") ;;
    esac
  done <<<"$pids"
  if [ ${#foreign[@]} -gt 0 ]; then
    fail "port $PORT" "used by: ${foreign[*]}" \
      'Stop that process, or run: PORT=<other> ./android/start-proxy.sh'
  fi
  if [ ${#stale[@]} -gt 0 ]; then
    for pid in "${stale[@]}"; do kill "$pid" 2>/dev/null || true; done
    for _ in {1..15}; do # give them a moment before forcing
      lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
      sleep 0.2
    done
    for pid in "${stale[@]}"; do kill -9 "$pid" 2>/dev/null || true; done
    lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 &&
      fail "port $PORT" 'still busy after stopping stale proxies' "try: lsof -nP -iTCP:$PORT"
    info '✓' "port $PORT" "stopped ${#stale[@]} mitmdump process(es)"
  else
    info '✓' "port $PORT" 'free'
  fi
}

cleanup() {
  trap - EXIT INT TERM
  kill "$PROXY_PID" 2>/dev/null || true
  wait "$PROXY_PID" 2>/dev/null || true
  local owner=''
  owner="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -z "$owner" ] || [ "$owner" = "$$" ] || ! kill -0 "$owner" 2>/dev/null; then
    adb shell settings delete global http_proxy >/dev/null 2>&1 || true
    info '✓' 'mitmdump' 'stopped — device proxy cleared'
  else
    info '✓' 'mitmdump' "stopped — instance $owner is still running; device proxy left set"
  fi
}

start_proxy() {
  # Local debugging only: this disables upstream certificate verification.
  "${MITMDUMP[@]}" --listen-host 0.0.0.0 --listen-port "$PORT" --set ssl_insecure=true \
    -s "$ROUTER" &
  PROXY_PID=$!
  trap cleanup EXIT
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
  local up=''
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
ensure_host_ca
boot_emulator
ensure_device_ca
free_port
set_device_proxy
start_proxy
