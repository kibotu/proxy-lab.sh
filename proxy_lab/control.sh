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
use_android_sdk

OPERATION="${1:-}"
[ "$#" -gt 0 ] && shift
PLATFORM="all"
SERIAL=""
UDID=""
PORT="${PORT:-}"
JSON_OUTPUT="${JSON_OUTPUT:-0}"
FOLLOW=0
LINES=""

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
    --json)
      JSON_OUTPUT=1
      shift
      ;;
    --follow|-f)
      FOLLOW=1
      shift
      ;;
    --lines)
      require_value "$1" "${2-}"
      LINES="$2"
      shift 2
      ;;
    *)
      fail 'arguments' "unknown option: $1"
      ;;
  esac
done

case "$OPERATION" in
  status|stop|reset|doctor|logs) ;;
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

  if [ "$JSON_OUTPUT" -eq 1 ]; then
    printf '{'
    json_field 'session' "$directory"; printf ','
    json_field 'state' "$state"; printf ','
    json_field 'platform' "${platform:-unknown}"; printf ','
    json_field 'port' "${port:-0}"; printf ','
    json_field 'owner' "${owner:-0}"; printf ','
    json_field 'serial' "$(state_read_from "$directory" serial)"; printf ','
    json_field 'udid' "$(state_read_from "$directory" udid)"; printf ','
    json_field 'config' "$(state_read_from "$directory" config)"; printf ','
    json_field 'started_at' "$(state_read_from "$directory" started_at)"; printf ','
    json_field 'mitmproxy' "$(state_read_from "$directory" mitmproxy)"; printf ','
    json_field 'mitmproxy_source' "$(state_read_from "$directory" mitmproxy_source)"; printf ','
    json_field 'log_format' "$(state_read_from "$directory" log_format)"; printf ','
    json_field 'duration' "$(state_read_from "$directory" duration)"; printf ','
    json_field 'log' "$(state_read_from "$directory" log)"; printf ','
    json_field 'proxy_pid' "$(state_read_from "$directory" proxy_pid)"
    if [ "$platform" = "android" ]; then
      printf ','; json_field 'previous_proxy' "$(state_read_from "$directory" previous_proxy)"
      printf ','; json_field 'ca_hash' "$(state_read_from "$directory" ca_hash)"
    fi
    printf '}\n'
    return 0
  fi

  printf '%s\n' "session: $directory"
  printf '%s\n' "  state: $state"
  printf '%s\n' "  platform: ${platform:-unknown}"
  printf '%s\n' "  port: ${port:-n/a}"
  printf '%s\n' "  owner: ${owner:-unknown}"
  printf '%s\n' "  serial: $(state_read_from "$directory" serial)"
  printf '%s\n' "  config: $(state_read_from "$directory" config)"
  printf '%s\n' "  mitmproxy: $(state_read_from "$directory" mitmproxy) ($(state_read_from "$directory" mitmproxy_source))"
  if [ -n "$(state_read_from "$directory" log)" ]; then
    printf '%s\n' "  log: $(state_read_from "$directory" log)"
  fi
  if [ "$platform" = "android" ]; then
    printf '%s\n' "  previous proxy: $(state_read_from "$directory" previous_proxy)"
    printf '%s\n' "  CA hash: $(state_read_from "$directory" ca_hash)"
  else
    printf '%s\n' "  UDID: $(state_read_from "$directory" udid)"
  fi
}

stop_one() {
  local directory="$1" owner waited=0 restore_status=0 serial
  owner="$(state_read_from "$directory" owner)"
  if state_pid_alive "$owner"; then
    if ! state_owner_matches "$directory"; then
      printf '  ✗ %-11s PID %s is not the recorded proxy-lab owner; state left untouched\n' \
        'stop' "$owner" >&2
      STOP_DETAIL="PID $owner is not the recorded owner; state left untouched"
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
        STOP_DETAIL="PID $owner changed identity; state left untouched"
        return 1
      fi
      kill -KILL "$owner" 2>/dev/null || true
      sleep 0.1
    fi
    if state_pid_alive "$owner"; then
      printf '  ✗ %-11s PID %s did not stop\n' 'stop' "$owner" >&2
      STOP_DETAIL="PID $owner did not stop"
      return 1
    fi
  fi

  if [ -f "$directory/platform" ] && [ "$(state_read_from "$directory" platform)" = "android" ]; then
    state_restore_android_proxy "$directory" || restore_status=$?
    case "$restore_status" in
      0) ;;
      2)
        # The recorded device is gone; holding the state back would only block
        # the next start behind a proxy nobody can restore.
        serial="$(state_read_from "$directory" serial)"
        info '!' 'stop' "${serial:-the recorded device} is not reachable; state released"
        ;;
      *)
        printf '  ✗ %-11s could not restore the Android proxy; run reset explicitly\n' 'stop' >&2
        STOP_DETAIL='could not restore the Android proxy; run reset explicitly'
        return 1
        ;;
    esac
  fi
  rm -rf "$directory"
  STOP_DETAIL='stopped'
  [ "$JSON_OUTPUT" -eq 1 ] || printf '  ✓ %-11s stopped %s\n' 'stop' "$directory"
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
  if ! android_device_ready "$serial"; then
    info '!' 'reset' "$serial is not reachable; nothing to clear on the device"
    return 0
  fi
  adb -s "$serial" shell settings delete global http_proxy >/dev/null 2>&1 || true
  adb -s "$serial" shell settings delete global_http_proxy_host >/dev/null 2>&1 || true
  adb -s "$serial" shell settings delete global_http_proxy_port >/dev/null 2>&1 || true
  printf '  ✓ %-11s cleared Android proxy on %s\n' 'reset' "$serial"
}

# Doctor accumulates every check into parallel arrays so the same pass can be
# rendered as human text or as JSON. DOCTOR_STATUS holds ok/warn/fail.
DOCTOR_STATUS=()
DOCTOR_NAME=()
DOCTOR_DETAIL=()
DOCTOR_ISSUES=0

doctor_check() {
  local status="$1" name="$2" detail="$3"
  DOCTOR_STATUS+=("$status")
  DOCTOR_NAME+=("$name")
  DOCTOR_DETAIL+=("$detail")
  [ "$status" = "fail" ] && DOCTOR_ISSUES=$((DOCTOR_ISSUES + 1))
  [ "$JSON_OUTPUT" -eq 1 ] && return 0
  case "$status" in
    ok) info '✓' "$name" "$detail" ;;
    warn) info '!' "$name" "$detail" ;;
    *) info '✗' "$name" "$detail" ;;
  esac
}

doctor_next() {
  case "$PLATFORM" in
    android) printf 'proxy-lab start android' ;;
    ios) printf 'proxy-lab start ios' ;;
    *) printf 'proxy-lab start android   # or: proxy-lab start ios' ;;
  esac
}

doctor_json() {
  local index first=1
  printf '{'
  printf '"platform": "%s", ' "$(json_escape "$PLATFORM")"
  printf '"ok": %s, ' "$([ "$DOCTOR_ISSUES" -eq 0 ] && printf 'true' || printf 'false')"
  printf '"issues": %d, ' "$DOCTOR_ISSUES"
  json_field 'state_directory' "$(state_root)"; printf ', '
  json_field 'version' "${PROXY_LAB_VERSION:-unknown}"; printf ', '
  json_field 'config' "$CONFIG"; printf ', '
  json_field 'next' "$(doctor_next)"; printf ', '
  printf '"checks": ['
  for index in "${!DOCTOR_NAME[@]}"; do
    [ "$first" -eq 1 ] || printf ', '
    first=0
    printf '{"status": "%s", "name": "%s", "detail": "%s"}' \
      "${DOCTOR_STATUS[$index]}" \
      "$(json_escape "${DOCTOR_NAME[$index]}")" \
      "$(json_escape "${DOCTOR_DETAIL[$index]}")"
  done
  printf ']}\n'
}

run_doctor() {
  local config_status mitmproxy_status version latest tool doctor_port listener
  local booted avd_home play_avds avd_name devices emulator_bin

  use_android_sdk

  if [ "$JSON_OUTPUT" -ne 1 ]; then
    printf '%s\n' 'proxy-lab doctor'
    printf '%s\n' "  state directory: $(state_root)"
    printf '%s\n' "  wrapper version: ${PROXY_LAB_VERSION:-unknown}"
    printf '%s\n' "  config: $CONFIG"
    printf '%s\n' "  CA: $HOME/.mitmproxy/mitmproxy-ca-cert.pem"
  fi

  doctor_check ok 'version' "${PROXY_LAB_VERSION:-unknown}"

  if [ -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" ]; then
    if command -v openssl >/dev/null 2>&1; then
      doctor_check ok 'CA fingerprint' "$(openssl x509 -in "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" -noout -fingerprint -sha256 2>/dev/null || true)"
    else
      doctor_check warn 'CA' 'present; openssl is unavailable for fingerprint display'
    fi
  else
    doctor_check warn 'CA' 'not generated yet; start will generate it'
  fi

  config_status=0
  validate_config_file || config_status=$?
  if [ "$config_status" -eq 0 ]; then
    doctor_check ok 'config' "$CONFIG"
  else
    case "$config_status" in
      2) doctor_check warn 'config' 'Python unavailable; file existence was not validated' ;;
      *) doctor_check fail 'config' "invalid: $CONFIG" ;;
    esac
  fi

  if command -v uv >/dev/null 2>&1; then
    doctor_check ok 'uv' "$(command -v uv)"
  else
    doctor_check warn 'uv' 'not found — brew install uv  (required unless mitmdump is already on PATH)'
  fi

  mitmproxy_status=0
  try_select_mitmproxy || mitmproxy_status=$?
  if [ "$mitmproxy_status" -eq 0 ]; then
    version="$(mitmproxy_version || true)"
    if [ -n "$version" ]; then
      doctor_check ok 'mitmproxy' "$version via $MITMPROXY_SOURCE"
      if selected_platform ios && version_is_older "$version" "10.1.5"; then
        doctor_check fail 'mitmproxy' 'too old for iOS local capture (need 10.1.5+)'
      fi
      latest="$(latest_mitmproxy_version || true)"
      if [ -n "$latest" ] && version_is_older "$version" "$latest"; then
        doctor_check warn 'mitmproxy' "newer on PyPI: $latest — MITMPROXY_SPEC=mitmproxy==$latest"
      fi
    else
      doctor_check fail 'mitmproxy' 'could not run --version'
    fi
  else
    doctor_check fail 'mitmproxy' 'not found — brew install uv, or brew install --cask mitmproxy'
  fi

  if selected_platform android; then
    for tool in adb openssl lsof; do
      if command -v "$tool" >/dev/null 2>&1; then
        doctor_check ok "$tool" "$(command -v "$tool")"
      else
        case "$tool" in
          adb) doctor_check fail 'adb' 'not found — install Android Studio, then re-run doctor (SDK paths are probed automatically)' ;;
          *) doctor_check fail "$tool" 'not found' ;;
        esac
      fi
    done
    if command -v adb >/dev/null 2>&1; then
      devices="$(adb devices 2>/dev/null | awk '$2 == "device" && $1 ~ /^emulator-/ { print $1 }' | paste -sd', ' -)"
      if [ -n "$devices" ]; then
        doctor_check ok 'emulator' "running: $devices"
      else
        doctor_check warn 'emulator' 'none running; start will boot the first AVD'
      fi
    fi
    emulator_bin="$(command -v emulator || true)"
    if [ -n "$emulator_bin" ]; then
      if [ "$JSON_OUTPUT" -ne 1 ]; then
        printf '%s\n' '  AVDs:'
        emulator -list-avds 2>&1 | sed 's/^/    /' || true
      fi
      avd_home="${ANDROID_AVD_HOME:-$HOME/.android/avd}"
      play_avds=""
      for avd_name in "$avd_home"/*.avd; do
        [ -f "$avd_name/config.ini" ] || continue
        if grep -Eiq 'playstore|google_apis_playstore' "$avd_name/config.ini"; then
          play_avds="${play_avds:+$play_avds, }$(basename "$avd_name" .avd)"
        fi
      done
      if [ -n "$play_avds" ]; then
        doctor_check warn 'avd' "Play Store image(s): $play_avds — CA install needs a Google APIs image, not Play Store"
      fi
    else
      doctor_check warn 'emulator' 'not found; only required when booting an AVD'
    fi
    if [ "$JSON_OUTPUT" -ne 1 ]; then
      printf '%s\n' "  ANDROID_HOME: ${ANDROID_HOME:-unset}"
      printf '%s\n' "  ANDROID_SDK_ROOT: ${ANDROID_SDK_ROOT:-unset}"
      printf '%s\n' "  selected serial: ${SERIAL:-auto}"
    fi
    doctor_port="${PORT:-8080}"
    if command -v lsof >/dev/null 2>&1; then
      listener="$(lsof -nP -iTCP:"$doctor_port" -sTCP:LISTEN 2>/dev/null || true)"
      if [ -n "$listener" ]; then
        doctor_check warn "port $doctor_port" 'in use — stop the other listener, or: PORT=<other> proxy-lab start android'
      else
        doctor_check ok "port $doctor_port" 'free'
      fi
    fi
  fi

  if selected_platform ios; then
    if [ "$JSON_OUTPUT" -ne 1 ]; then
      printf '%s\n' "  selected UDID: ${UDID:-auto}"
    fi
    if command -v xcrun >/dev/null 2>&1; then
      doctor_check ok 'xcrun' "$(command -v xcrun)"
      booted="$(xcrun simctl list devices booted 2>/dev/null || true)"
      if [ "$JSON_OUTPUT" -ne 1 ]; then
        printf '%s\n' '  booted simulators:'
        printf '%s\n' "$booted" | sed 's/^/    /'
      fi
      if printf '%s' "$booted" | grep -q '(Booted)'; then
        doctor_check ok 'simulator' 'a simulator is booted'
      else
        doctor_check warn 'simulator' 'none booted; start one in Xcode or Device Hub, then: proxy-lab start ios'
      fi
      # Local capture needs a signed network extension approved by the user.
      # There is no non-interactive way to read that approval, so say so
      # instead of letting `start` fail with an unexplained hang.
      doctor_check warn 'capture' \
        'first run: approve the mitmproxy network extension when macOS asks — there is no non-interactive check'
    else
      doctor_check fail 'xcrun' 'not found — install Xcode and its command-line tools'
    fi
    if [ "$JSON_OUTPUT" -ne 1 ] && command -v xcode-select >/dev/null 2>&1; then
      printf '%s\n' "  Xcode: $(xcode-select -p 2>/dev/null || true)"
    fi
  fi

  if [ "$JSON_OUTPUT" -eq 1 ]; then
    doctor_json
  elif [ "$DOCTOR_ISSUES" -eq 0 ]; then
    printf '\nNext: %s\n' "$(doctor_next)"
  else
    printf '\nFix the ✗ checks, then: proxy-lab doctor%s\n' \
      "$([ "$PLATFORM" = all ] && printf '' || printf ' %s' "$PLATFORM")"
  fi

  if [ "$DOCTOR_ISSUES" -ne 0 ]; then
    return 1
  fi
  return 0
}

run_logs() {
  local directory log platform candidate found=0
  local json_logs="" json_first=1

  if [ "$JSON_OUTPUT" -eq 1 ] && [ "$FOLLOW" -eq 1 ]; then
    fail 'arguments' '--json cannot be combined with --follow'
  fi

  # A recorded session names its own log; a finished session's state is gone,
  # so fall back to the conventional path under the state root.
  for platform in android ios; do
    selected_platform "$platform" || continue
    log=""
    while IFS= read -r directory; do
      [ -n "$directory" ] || continue
      candidate="$(state_read_from "$directory" log)"
      if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        log="$candidate"
        break
      fi
    done < <(state_directories "$platform")

    if [ -z "$log" ]; then
      if [ "$platform" = "android" ]; then
        candidate="$(log_path_for android "${PORT:-8080}")"
      else
        candidate="$(log_path_for ios 0)"
      fi
      [ -f "$candidate" ] && log="$candidate"
    fi

    [ -n "$log" ] || continue
    found=1
    if [ "$JSON_OUTPUT" -eq 1 ]; then
      [ "$json_first" -eq 1 ] || json_logs+=', '
      json_first=0
      json_logs+="$(printf '{"platform": "%s", "log": "%s"}' \
        "$(json_escape "$platform")" "$(json_escape "$log")")"
      continue
    fi
    if [ "$FOLLOW" -eq 1 ]; then
      tail -n "${LINES:-0}" -f "$log"
    elif [ -n "$LINES" ]; then
      tail -n "$LINES" "$log"
    else
      cat "$log"
    fi
  done

  if [ "$found" -eq 0 ]; then
    if [ "$JSON_OUTPUT" -eq 1 ]; then
      printf '{"sessions": []}\n'
    else
      printf '%s\n' 'No proxy-lab log found. Start one with: proxy-lab start <platform> --detach'
    fi
    return 1
  fi
  if [ "$JSON_OUTPUT" -eq 1 ]; then
    printf '{"logs": [%s]}\n' "$json_logs"
  fi
  return 0
}

case "$OPERATION" in
  status)
    found=0
    if [ "$JSON_OUTPUT" -eq 1 ]; then
      printf '['
      first_json=1
    fi
    for platform in android ios; do
      selected_platform "$platform" || continue
      while IFS= read -r directory; do
        [ -n "$directory" ] || continue
        if [ "$JSON_OUTPUT" -eq 1 ]; then
          [ "$first_json" -eq 1 ] || printf ','
          first_json=0
        fi
        status_one "$directory"
        found=1
      done < <(state_directories "$platform")
    done
    if [ "$JSON_OUTPUT" -eq 1 ]; then
      printf ']\n'
    elif [ "$found" -eq 0 ]; then
      printf '%s\n' 'No proxy-lab sessions recorded.'
    fi
    ;;
  stop)
    found=0
    result=0
    [ "$JSON_OUTPUT" -eq 1 ] && printf '['
    first_json=1
    for platform in android ios; do
      selected_platform "$platform" || continue
      while IFS= read -r directory; do
        [ -n "$directory" ] || continue
        found=1
        STOP_DETAIL=""
        stop_status=ok
        stop_one "$directory" || { result=1; stop_status=fail; }
        if [ "$JSON_OUTPUT" -eq 1 ]; then
          [ "$first_json" -eq 1 ] || printf ','
          first_json=0
          printf '{"session": "%s", "status": "%s", "detail": "%s"}' \
            "$(json_escape "$directory")" "$stop_status" "$(json_escape "$STOP_DETAIL")"
        fi
      done < <(state_directories "$platform")
    done
    if [ "$JSON_OUTPUT" -eq 1 ]; then
      printf ']\n'
    elif [ "$found" -eq 0 ]; then
      printf '%s\n' 'No proxy-lab sessions recorded.'
    fi
    exit "$result"
    ;;
  reset)
    found=0
    result=0
    for platform in android ios; do
      selected_platform "$platform" || continue
      while IFS= read -r directory; do
        [ -n "$directory" ] || continue
        found=1
        reset_one "$directory" || result=1
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
    exit "$result"
    ;;
  doctor)
    run_doctor
    ;;
  logs)
    run_logs
    ;;
esac
