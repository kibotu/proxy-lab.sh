#!/usr/bin/env bash
# Shared launcher, configuration, mitmproxy, and session-state helpers.
# This file is sourced by the platform launchers and control command.

if [ -n "${PROXY_LAB_COMMON_LOADED:-}" ]; then
  return 0
fi
PROXY_LAB_COMMON_LOADED=1

info() {
  printf '  %s %-11s %s\n' "$1" "$2" "$3"
}

fail() {
  local label="$1" message="$2" hint
  shift 2
  printf '  ✗ %-11s %s\n' "$label" "$message" >&2
  for hint in "$@"; do
    printf '      ↳ %s\n' "$hint" >&2
  done
  exit 1
}

require_value() {
  [ "$#" -ge 2 ] || fail 'arguments' "$1 requires a value"
}

resolve_file() {
  local path="$1" label="${2:-file}"
  [ -f "$path" ] || fail "$label" "missing: $path"
  printf '%s/%s\n' "$(cd "$(dirname "$path")" && pwd)" "$(basename "$path")"
}

parse_launcher_args() {
  local index resolved
  ADDON_SCRIPTS=()

  if [ -n "${PROXY_LAB_SCRIPTS:-}" ]; then
    while IFS= read -r script; do
      [ -n "$script" ] && ADDON_SCRIPTS+=("$script")
    done < <(printf '%s\n' "$PROXY_LAB_SCRIPTS")
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      -s|--script)
        require_value "$1" "${2-}"
        ADDON_SCRIPTS+=("$2")
        shift 2
        ;;
      --config)
        require_value "$1" "${2-}"
        CONFIG="$2"
        shift 2
        ;;
      --port)
        require_value "$1" "${2-}"
        PORT="$2"
        shift 2
        ;;
      --avd)
        require_value "$1" "${2-}"
        # shellcheck disable=SC2034 # consumed by the platform launcher
        AVD="$2"
        shift 2
        ;;
      --boot-timeout)
        require_value "$1" "${2-}"
        BOOT_TIMEOUT="$2"
        shift 2
        ;;
      --udid)
        require_value "$1" "${2-}"
        UDID="$2"
        shift 2
        ;;
      --serial)
        require_value "$1" "${2-}"
        # shellcheck disable=SC2034 # consumed by the Android launcher
        SERIAL="$2"
        shift 2
        ;;
      --trust-only)
        # shellcheck disable=SC2034 # consumed by the iOS launcher
        TRUST_ONLY=1
        shift
        ;;
      --)
        shift
        [ "$#" -eq 0 ] || fail 'arguments' "unexpected arguments: $*"
        ;;
      *)
        fail 'arguments' "unknown option: $1"
        ;;
    esac
  done

  ROUTER="$(resolve_file "$ROUTER" 'router')"
  CONFIG="$(resolve_file "$CONFIG" 'config')"
  if [ "${#ADDON_SCRIPTS[@]}" -gt 0 ]; then
    index=0
    for script in "${ADDON_SCRIPTS[@]}"; do
      resolved="$(resolve_file "$script" 'addon')"
      ADDON_SCRIPTS[index]="$resolved"
      index=$((index + 1))
    done
  fi
}

validate_common_files() {
  if [ "${VALIDATE_PORT:-1}" -eq 1 ]; then
    case "${PORT:-}" in
      ''|*[!0-9]*) fail 'arguments' "PORT must be a positive integer: ${PORT:-}" ;;
    esac
    [ "$PORT" -gt 0 ] 2>/dev/null || fail 'arguments' "PORT must be greater than zero: $PORT"
  fi

  case "${BOOT_TIMEOUT:-}" in
    ''|*[!0-9]*) fail 'arguments' "BOOT_TIMEOUT must be a positive integer: ${BOOT_TIMEOUT:-}" ;;
  esac
  [ "$BOOT_TIMEOUT" -gt 0 ] 2>/dev/null || fail 'arguments' "BOOT_TIMEOUT must be greater than zero: $BOOT_TIMEOUT"
}

try_select_mitmproxy() {
  if [ -n "${PROXY_LAB_MITMDUMP:-}" ]; then
    [ -x "$PROXY_LAB_MITMDUMP" ] || return 1
    MITMDUMP=("$PROXY_LAB_MITMDUMP")
    MITMPROXY_SOURCE="explicit mitmdump"
  elif command -v mitmdump >/dev/null 2>&1; then
    MITMDUMP=(mitmdump)
    MITMPROXY_SOURCE="host mitmdump"
  elif command -v uv >/dev/null 2>&1; then
    MITMDUMP=(uv tool run --from "${MITMPROXY_SPEC:-mitmproxy@latest}" mitmdump)
    MITMPROXY_SOURCE="uv ${MITMPROXY_SPEC:-mitmproxy@latest}"
  else
    return 1
  fi
}

select_mitmproxy() {
  try_select_mitmproxy || fail 'mitmproxy' 'mitmdump not found and uv is not installed' \
    'brew install --cask mitmproxy or brew install uv'
}

mitmproxy_version() {
  "${MITMDUMP[@]}" --version 2>/dev/null |
    sed -nE 's/^Mitmproxy( version)?:[[:space:]]*([^[:space:]]+).*/\2/p' |
    head -n 1
}

latest_mitmproxy_version() {
  [ "${PROXY_LAB_SKIP_UPDATE_CHECK:-0}" = "1" ] && return 1
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
  local minimum="${1:-}" current latest
  current="$(mitmproxy_version || true)"
  [ -n "$current" ] ||
    fail 'mitmproxy' "could not run ${MITMDUMP[*]} --version" \
      "check: ${MITMDUMP[*]} --version" \
      'https://docs.mitmproxy.org/stable/'

  if [ -n "$minimum" ] && version_is_older "$current" "$minimum"; then
    fail 'mitmproxy' "$current is too old (need $minimum+)" \
      'upgrade mitmproxy or choose a newer MITMPROXY_SPEC'
  fi

  info '✓' 'mitmproxy' "$current via $MITMPROXY_SOURCE"
  latest="$(latest_mitmproxy_version || true)"
  if [ -n "$latest" ] && version_is_older "$current" "$latest"; then
    info 'i' 'mitmproxy' "newer version available: $latest — https://pypi.org/project/mitmproxy/"
  fi
}

ensure_host_ca() {
  if [ ! -f "$CERT" ]; then
    info '…' 'host CA' 'generating ~/.mitmproxy (first run)'
    "${MITMDUMP[@]}" --listen-port 0 >/dev/null 2>&1 &
    local generator=$!
    for _ in {1..50}; do
      [ -f "$CERT" ] && break
      sleep 0.2
    done
    kill "$generator" 2>/dev/null || true
    wait "$generator" 2>/dev/null || true
    [ -f "$CERT" ] || fail 'host CA' 'could not generate the mitmproxy CA' \
      "Run once: ${MITMDUMP[*]}" \
      'https://docs.mitmproxy.org/stable/concepts/certificates/'
  fi
  info '✓' 'host CA' "$CERT"
}

python_path() {
  if [ -n "${PROXY_LAB_PYTHONPATH:-}" ]; then
    printf '%s' "$PROXY_LAB_PYTHONPATH"
  elif [ -f "${PROJECT_DIR:-}/proxy_lab/__init__.py" ]; then
    printf '%s' "$PROJECT_DIR"
  else
    (cd "$PROJECT_DIR/.." && pwd)
  fi
}

configure_python_path() {
  local path
  path="$(python_path)"
  case ":${PYTHONPATH:-}:" in
    *":$path:"*) ;;
    *) export PYTHONPATH="$path${PYTHONPATH:+:$PYTHONPATH}" ;;
  esac
}

validate_config_file() {
  local python_bin="${PROXY_LAB_PYTHON:-}"
  if [ -z "$python_bin" ]; then
    python_bin="$(command -v python3 || true)"
  fi
  if [ -z "$python_bin" ]; then
    return 2
  fi
  PYTHONPATH="$(python_path)${PYTHONPATH:+:$PYTHONPATH}" \
    "$python_bin" - "$CONFIG" <<'PY'
import sys

try:
    from proxy_lab.config import ConfigError, load_domains
except ModuleNotFoundError:
    raise SystemExit(2)

try:
    load_domains(sys.argv[1])
except ConfigError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
}

require_config_file() {
  local status=0
  validate_config_file || status=$?
  if [ "$status" -ne 0 ]; then
    case "$status" in
      2) info '!' 'config' "deferred validation to mitmproxy: $CONFIG" ;;
      *) fail 'config' "invalid config: $CONFIG" "fix the domains list; see README.md" ;;
    esac
  else
    info '✓' 'config' "$CONFIG"
  fi
}

state_root() {
  if [ -n "${PROXY_LAB_STATE_DIR:-}" ]; then
    printf '%s' "$PROXY_LAB_STATE_DIR"
  elif [ -n "${XDG_STATE_HOME:-}" ]; then
    printf '%s/state/proxy-lab' "$XDG_STATE_HOME"
  else
    printf '%s/.local/state/proxy-lab' "${HOME:-/tmp}"
  fi
}

state_read_from() {
  local directory="$1" field="$2"
  cat "$directory/$field" 2>/dev/null || true
}

state_read() {
  state_read_from "$STATE_DIR" "$1"
}

state_write() {
  local field="$1" value="$2" temporary
  case "$value" in
    *$'\n'*|*$'\r'*) fail 'state' "state field contains a newline: $field" ;;
  esac
  temporary="$(mktemp "$STATE_DIR/.$field.XXXXXX")"
  printf '%s\n' "$value" >"$temporary"
  mv "$temporary" "$STATE_DIR/$field"
}

state_pid_alive() {
  local pid="${1:-}"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  kill -0 "$pid" 2>/dev/null
}

state_owner_matches() {
  local directory="$1" pid actual expected_start actual_start
  pid="$(state_read_from "$directory" owner)"
  state_pid_alive "$pid" || return 1
  expected_start="$(state_read_from "$directory" owner_start)"
  actual_start="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^[[:space:]]*//')"
  if [ -n "$expected_start" ] && [ -n "$actual_start" ] && [ "$expected_start" != "$actual_start" ]; then
    return 1
  fi
  actual="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$actual" in
    *mitmdump*|*start-proxy.sh*|*control.sh*) return 0 ;;
    *) return 1 ;;
  esac
}

state_acquire() {
  local platform="$1" port="${2:-0}" owner
  umask 077
  STATE_ROOT="$(state_root)"
  mkdir -p "$STATE_ROOT"
  if [ "$platform" = "android" ]; then
    STATE_DIR="$STATE_ROOT/android-$port"
  else
    STATE_DIR="$STATE_ROOT/$platform"
  fi

  if ! mkdir "$STATE_DIR" 2>/dev/null; then
    owner="$(state_read_from "$STATE_DIR" owner)"
    if state_pid_alive "$owner"; then
      fail 'session' "another proxy-lab session is active for $platform" \
        "run: proxy-lab status $platform"
    fi
    fail 'session' "stale state exists: $STATE_DIR" \
      "run: proxy-lab reset $platform"
  fi

  STATE_ACQUIRED=1
  state_write owner "$$"
  state_write owner_command "$(ps -p "$$" -o command= 2>/dev/null || true)"
  state_write owner_start "$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^[[:space:]]*//')"
  state_write platform "$platform"
  state_write port "$port"
  state_write config "${CONFIG:-${PROJECT_DIR:-.}/domains.yaml}"
  [ -z "${UDID:-}" ] || state_write udid "$UDID"
  state_write started_at "$(date +%s)"
}

state_release() {
  [ "${STATE_ACQUIRED:-0}" -eq 1 ] || return 0
  [ "$(state_read owner)" = "$$" ] || return 0
  rm -rf "$STATE_DIR"
  STATE_ACQUIRED=0
}

state_restore_android_proxy() {
  local directory="$1" serial previous
  [ -f "$directory/previous_proxy" ] || return 0
  serial="$(state_read_from "$directory" serial)"
  previous="$(state_read_from "$directory" previous_proxy)"
  [ -n "$serial" ] || return 1
  command -v adb >/dev/null 2>&1 || return 1

  if [ -z "$previous" ] || [ "$previous" = "__PROXY_LAB_NULL__" ]; then
    adb -s "$serial" shell settings delete global http_proxy >/dev/null 2>&1 || return 1
  else
    adb -s "$serial" shell settings put global http_proxy "$previous" >/dev/null 2>&1 || return 1
  fi
}

ios_booted_udid() {
  if [ -n "${UDID:-}" ]; then
    printf '%s' "$UDID"
    return 0
  fi
  command -v xcrun >/dev/null 2>&1 || return 1
  xcrun simctl list devices booted 2>/dev/null |
    sed -nE 's/.*\(([0-9A-Fa-f-]+)\) \(Booted\).*/\1/p' |
    head -n 1
}

trust_ios_certificate() {
  local udid
  command -v xcrun >/dev/null 2>&1 || return 2
  udid="$(ios_booted_udid || true)"
  [ -n "$udid" ] || return 3
  xcrun simctl keychain "$udid" add-root-cert "$CERT" >/dev/null 2>&1 || return 1
  info '✓' 'simulator' "CA trusted in $udid"
}

mitmproxy_addon_args() {
  MITMPROXY_ADDON_ARGS=()
  if [ "${#ADDON_SCRIPTS[@]}" -gt 0 ]; then
    for script in "${ADDON_SCRIPTS[@]}"; do
      MITMPROXY_ADDON_ARGS+=( -s "$script" )
    done
  fi
}
