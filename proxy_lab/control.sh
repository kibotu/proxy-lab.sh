#!/usr/bin/env bash
# Lifecycle, diagnostics, and recovery commands for proxy-lab.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/../local_router.py" ] && [ -f "$SCRIPT_DIR/../domains.yaml" ]; then
  PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  PROJECT_DIR="$SCRIPT_DIR"
fi
CONFIG="${PROXY_LAB_CONFIG:-$PROJECT_DIR/domains.yaml}"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

OPERATION="${1:-}"
[ "$#" -gt 0 ] && shift
PLATFORM="all"
SERIAL=""
UDID=""
PORT="${PORT:-}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    android|ios)
      [ "$PLATFORM" = "all" ] || fail 'arguments' "platform specified twice"
      PLATFORM="$1"
      shift
      ;;
    --serial)
      require_value "$1" "${2-}"
      SERIAL="$2"
      shift 2
      ;;
    --udid)
      require_value "$1" "${2-}"
      UDID="$2"
      shift 2
      ;;
    --port)
      require_value "$1" "${2-}"
      PORT="$2"
      shift 2
      ;;
    *)
      fail 'arguments' "unknown option: $1"
      ;;
  esac
done

case "$OPERATION" in
  status|stop|reset|doctor) ;;
  *) fail 'arguments' "unknown operation: ${OPERATION:-<missing>}" ;;
esac

selected_platform() {
  [ "$PLATFORM" = "all" ] || [ "$PLATFORM" = "$1" ]
}

state_directories() {
  local requested="$1" directory
  local root
  root="$(state_root)"
  if [ "$requested" = "android" ]; then
    for directory in "$root"/android-*; do
      [ -d "$directory" ] || continue
      if [ -n "$PORT" ] && [ "$(state_read_from "$directory" port)" != "$PORT" ]; then
        continue
      fi
      printf '%s\n' "$directory"
    done
  else
    directory="$root/$requested"
    [ -d "$directory" ] && printf '%s\n' "$directory"
  fi
}

status_one() {
  local directory="$1" owner platform port state
  owner="$(state_read_from "$directory" owner)"
  platform="$(state_read_from "$directory" platform)"
  port="$(state_read_from "$directory" port)"
  if state_pid_alive "$owner" && state_owner_matches "$directory"; then
    state="running"
  else
    state="stale"
  fi
  printf '%s\n' "session: $directory"
  printf '%s\n' "  state: $state"
  printf '%s\n' "  platform: ${platform:-unknown}"
  printf '%s\n' "  port: ${port:-n/a}"
  printf '%s\n' "  owner: ${owner:-unknown}"
  printf '%s\n' "  serial: $(state_read_from "$directory" serial)"
  printf '%s\n' "  config: $(state_read_from "$directory" config)"
  if [ "$platform" = "android" ]; then
    printf '%s\n' "  previous proxy: $(state_read_from "$directory" previous_proxy)"
    printf '%s\n' "  CA hash: $(state_read_from "$directory" ca_hash)"
  else
    printf '%s\n' "  UDID: $(state_read_from "$directory" udid)"
  fi
}

stop_one() {
  local directory="$1" owner waited=0
  owner="$(state_read_from "$directory" owner)"
  if state_pid_alive "$owner"; then
    if ! state_owner_matches "$directory"; then
      printf '  ✗ %-11s PID %s is not the recorded proxy-lab owner; state left untouched\n' \
        'stop' "$owner" >&2
      return 1
    fi
    kill -TERM "$owner" 2>/dev/null || true
    while state_pid_alive "$owner" && [ "$waited" -lt 100 ]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    if state_pid_alive "$owner"; then
      if ! state_owner_matches "$directory"; then
        printf '  ✗ %-11s PID %s changed identity; state left untouched\n' 'stop' "$owner" >&2
        return 1
      fi
      kill -KILL "$owner" 2>/dev/null || true
      sleep 0.1
    fi
    if state_pid_alive "$owner"; then
      printf '  ✗ %-11s PID %s did not stop\n' 'stop' "$owner" >&2
      return 1
    fi
  fi

  if [ -f "$directory/platform" ] && [ "$(state_read_from "$directory" platform)" = "android" ]; then
    state_restore_android_proxy "$directory" || {
      printf '  ✗ %-11s could not restore the Android proxy; run reset explicitly\n' 'stop' >&2
      return 1
    }
  fi
  rm -rf "$directory"
  printf '  ✓ %-11s stopped %s\n' 'stop' "$directory"
}

reset_one() {
  local directory="$1" owner state_serial
  if [ ! -d "$directory" ]; then
    return 0
  fi
  if [ -z "$SERIAL" ]; then
    state_serial="$(state_read_from "$directory" serial)"
    [ -n "$state_serial" ] && SERIAL="$state_serial"
  fi
  owner="$(state_read_from "$directory" owner)"
  if state_pid_alive "$owner" && ! state_owner_matches "$directory"; then
    printf '  ! %-11s PID %s is not the recorded owner; removing state without signalling it\n' \
      'reset' "$owner" >&2
    rm -rf "$directory"
    return 0
  fi
  stop_one "$directory"
}

clear_android_proxy() {
  local serial="$SERIAL"
  command -v adb >/dev/null 2>&1 || fail 'reset' 'adb not found' \
    'install Android Studio or use the recorded session state'
  if [ -z "$serial" ]; then
    serial="$(adb devices | awk '$2 == "device" && $1 ~ /^emulator-/ { print $1; exit }')"
  fi
  [ -n "$serial" ] || fail 'reset' 'no running emulator found' \
    'boot an emulator or pass --serial'
  adb -s "$serial" shell settings delete global http_proxy >/dev/null 2>&1 || true
  adb -s "$serial" shell settings delete global_http_proxy_host >/dev/null 2>&1 || true
  adb -s "$serial" shell settings delete global_http_proxy_port >/dev/null 2>&1 || true
  printf '  ✓ %-11s cleared Android proxy on %s\n' 'reset' "$serial"
}

run_doctor() {
  local issues=0
  printf '%s\n' 'proxy-lab doctor'
  printf '%s\n' "  state directory: $(state_root)"
  printf '%s\n' "  wrapper version: ${PROXY_LAB_VERSION:-unknown}"
  printf '%s\n' "  config: $CONFIG"
  printf '%s\n' "  CA: $HOME/.mitmproxy/mitmproxy-ca-cert.pem"
  if [ -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" ]; then
    if command -v openssl >/dev/null 2>&1; then
      info '✓' 'CA fingerprint' "$(openssl x509 -in "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" -noout -fingerprint -sha256 2>/dev/null || true)"
    else
      info '!' 'CA' 'present; openssl is unavailable for fingerprint display'
    fi
  else
    info '!' 'CA' 'not generated yet; start will generate it'
  fi

  config_status=0
  validate_config_file || config_status=$?
  if [ "$config_status" -eq 0 ]; then
    info '✓' 'config' "$CONFIG"
  else
    case "$config_status" in
      2) info '!' 'config' 'Python unavailable; file existence was not validated' ;;
      *) info '✗' 'config' "invalid: $CONFIG"; issues=$((issues + 1)) ;;
    esac
  fi

  mitmproxy_status=0
  try_select_mitmproxy || mitmproxy_status=$?
  if [ "$mitmproxy_status" -eq 0 ]; then
    local version
    version="$(mitmproxy_version || true)"
    if [ -n "$version" ]; then
      info '✓' 'mitmproxy' "$version via $MITMPROXY_SOURCE"
      if selected_platform ios && version_is_older "$version" "10.1.5"; then
        info '✗' 'mitmproxy' 'too old for iOS local capture (need 10.1.5+)'
        issues=$((issues + 1))
      fi
    else
      info '✗' 'mitmproxy' 'could not run --version'
      issues=$((issues + 1))
    fi
  else
    info '✗' 'mitmproxy' 'not found'
    issues=$((issues + 1))
  fi

  if selected_platform android; then
    local tool
    for tool in adb openssl lsof; do
      if command -v "$tool" >/dev/null 2>&1; then
        info '✓' "$tool" "$(command -v "$tool")"
      else
        info '✗' "$tool" 'not found'
        issues=$((issues + 1))
      fi
    done
    if command -v adb >/dev/null 2>&1; then
      printf '%s\n' '  adb devices:'
      adb devices -l 2>&1 | sed 's/^/    /' || issues=$((issues + 1))
    fi
    if command -v emulator >/dev/null 2>&1; then
      printf '%s\n' '  AVDs:'
      emulator -list-avds 2>&1 | sed 's/^/    /' || issues=$((issues + 1))
    else
      info '!' 'emulator' 'not found; only required when booting an AVD'
    fi
    printf '%s\n' "  ANDROID_HOME: ${ANDROID_HOME:-unset}"
    printf '%s\n' "  ANDROID_SDK_ROOT: ${ANDROID_SDK_ROOT:-unset}"
    printf '%s\n' "  selected serial: ${SERIAL:-auto}"
    local doctor_port="${PORT:-8080}"
    if command -v lsof >/dev/null 2>&1; then
      local listener
      listener="$(lsof -nP -iTCP:"$doctor_port" -sTCP:LISTEN 2>/dev/null || true)"
      if [ -n "$listener" ]; then
        printf '%s\n' '  port listener:'
        printf '%s\n' "$listener" | sed 's/^/    /'
      else
        info '✓' "port $doctor_port" 'free'
      fi
    fi
  fi

  if selected_platform ios; then
    printf '%s\n' "  selected UDID: ${UDID:-auto}"
    if command -v xcrun >/dev/null 2>&1; then
      info '✓' 'xcrun' "$(command -v xcrun)"
      printf '%s\n' '  booted simulators:'
      xcrun simctl list devices booted 2>&1 | sed 's/^/    /' || issues=$((issues + 1))
    else
      info '✗' 'xcrun' 'not found'
      issues=$((issues + 1))
    fi
    if command -v xcode-select >/dev/null 2>&1; then
      printf '%s\n' "  Xcode: $(xcode-select -p 2>/dev/null || true)"
    fi
  fi

  if [ "$issues" -ne 0 ]; then
    return 1
  fi
  return 0
}

case "$OPERATION" in
  status)
    found=0
    for platform in android ios; do
      selected_platform "$platform" || continue
      while IFS= read -r directory; do
        [ -n "$directory" ] || continue
        status_one "$directory"
        found=1
      done < <(state_directories "$platform")
    done
    [ "$found" -eq 1 ] || printf '%s\n' 'No proxy-lab sessions recorded.'
    ;;
  stop)
    found=0
    result=0
    for platform in android ios; do
      selected_platform "$platform" || continue
      while IFS= read -r directory; do
        [ -n "$directory" ] || continue
        found=1
        stop_one "$directory" || result=1
      done < <(state_directories "$platform")
    done
    [ "$found" -eq 1 ] || printf '%s\n' 'No proxy-lab sessions recorded.'
    exit "$result"
    ;;
  reset)
    found=0
    for platform in android ios; do
      selected_platform "$platform" || continue
      while IFS= read -r directory; do
        [ -n "$directory" ] || continue
        found=1
        reset_one "$directory"
      done < <(state_directories "$platform")
    done
    if [ "$PLATFORM" = "android" ]; then
      clear_android_proxy
      found=1
    elif [ "$PLATFORM" = "all" ] && command -v adb >/dev/null 2>&1; then
      clear_android_proxy
      found=1
    fi
    [ "$found" -eq 1 ] || printf '%s\n' 'No proxy-lab sessions recorded.'
    ;;
  doctor)
    run_doctor
    ;;
esac
