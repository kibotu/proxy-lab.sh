#!/usr/bin/env bash
# Shared launcher, configuration, mitmproxy, and session-state helpers.
# This file is sourced by the platform launchers and control command.

if [ -n "${PROXY_LAB_COMMON_LOADED:-}" ]; then
  return 0
fi
PROXY_LAB_COMMON_LOADED=1

# Pinned so today's one-liner is tomorrow's one-liner. A host `mitmdump`
# still wins. Override with MITMPROXY_SPEC=mitmproxy==X.Y.Z when you need to.
: "${MITMPROXY_SPEC:=mitmproxy==12.2.3}"
: "${IOS_DEVELOPER_DIR:=}"
# Where macOS keeps Network Extension approvals. Verified present on macOS 26.
# shellcheck disable=SC2034 # consumed by proxy_lab/control.sh and ios/start-proxy.sh
EXTENSION_SETTINGS_PANE="x-apple.systempreferences:com.apple.LoginItems-Settings.extension"

# Exit codes are part of the contract: a script can branch on the reason a run
# failed without parsing English. Keep this table in sync with README.md.
#   0 ok  1 unspecified  2 arguments  3 config/input  4 missing tool
#   5 device/CA  6 port  7 mitmproxy  8 session state
label_exit_code() {
  case "$1" in
    arguments) printf '2' ;;
    config|addon|router|domains) printf '3' ;;
    tools) printf '4' ;;
    emulator|root|device\ CA|host\ CA|simulator|proxy|extension) printf '5' ;;
    port*) printf '6' ;;
    mitmproxy|mitmdump) printf '7' ;;
    session|state|timeout) printf '8' ;;
    *) printf '1' ;;
  esac
}

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
  exit "$(label_exit_code "$label")"
}

# Minimal JSON string escaping for the --json surfaces. These values are paths,
# hostnames, and tool output; the control characters below are stripped rather
# than escaped because no JSON reader wants them.
json_escape() {
  local value
  value="$(printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037')"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

json_field() {
  printf '  "%s": "%s"' "$1" "$(json_escape "$2")"
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
  # The Python CLI talks in environment variables; clone users pass flags.
  # Flags win. Never reset these to empty — that drops `proxy-lab start --detach`.
  DETACH="${DETACH:-0}"
  DURATION="${DURATION:-}"
  LOG_FORMAT="${PROXY_LAB_LOG_FORMAT:-${LOG_FORMAT:-text}}"
  JSON_OUTPUT="${JSON_OUTPUT:-0}"
  log_format_set=0

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
      --system-proxy)
        # shellcheck disable=SC2034 # consumed by the iOS launcher
        SYSTEM_PROXY=1
        shift
        ;;
      --detach)
        DETACH=1
        shift
        ;;
      --json)
        JSON_OUTPUT=1
        shift
        ;;
      --log-format)
        require_value "$1" "${2-}"
        LOG_FORMAT="$2"
        log_format_set=1
        shift 2
        ;;
      --duration)
        require_value "$1" "${2-}"
        DURATION="$2"
        shift 2
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

  # --json is agent mode: bounded, detachable, machine-readable.
  if [ "$JSON_OUTPUT" = "1" ]; then
    DETACH=1
    [ "$log_format_set" -eq 0 ] && [ -z "${PROXY_LAB_LOG_FORMAT:-}" ] && LOG_FORMAT=jsonl
  fi

  # The mitmproxy addon reads its output format from the environment.
  export PROXY_LAB_LOG_FORMAT="$LOG_FORMAT"
  export JSON_OUTPUT
  ROUTER="$(resolve_file "$ROUTER" 'router')"
  [ -z "$CONFIG" ] || CONFIG="$(resolve_file "$CONFIG" 'config')"
  MITMDUMP_SCRIPT_ARGS=(-s "$ROUTER")
  index=0
  for script in ${ADDON_SCRIPTS[@]+"${ADDON_SCRIPTS[@]}"}; do
    resolved="$(resolve_file "$script" 'addon')"
    ADDON_SCRIPTS[index]="$resolved"
    MITMDUMP_SCRIPT_ARGS+=(-s "$resolved")
    index=$((index + 1))
  done
}

# Android Studio installs the SDK without touching PATH. Find adb and emulator
# where it puts them, so a fresh machine works without shell-profile edits.
use_android_sdk() {
  # Android Studio does not put adb on PATH. Look where it actually installs.
  local sdk
  for sdk in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" \
    "$HOME/Library/Android/sdk" "$HOME/Android/Sdk"; do
    [ -n "$sdk" ] || continue
    if ! command -v adb >/dev/null 2>&1 && [ -x "$sdk/platform-tools/adb" ]; then
      PATH="$PATH:$sdk/platform-tools"
    fi
    if ! command -v emulator >/dev/null 2>&1 && [ -x "$sdk/emulator/emulator" ]; then
      PATH="$PATH:$sdk/emulator"
    fi
  done
  export PATH
}

# Local capture runs through a macOS Network Extension that the user has to
# grant. Until it is granted, mitmdump starts happily and captures nothing at
# all — no traffic, no error, no hint. So the state is read rather than assumed.
#   approved  granted and active
#   pending   installed, still waiting for the user to allow it
#   absent    not installed yet; the first local-mode run installs it
#   unknown   systemextensionsctl unavailable, or a state we do not recognise
mitmproxy_extension_state() {
  local line
  if ! command -v systemextensionsctl >/dev/null 2>&1; then
    printf 'unknown'
    return 0
  fi
  line="$(systemextensionsctl list 2>/dev/null | grep -i mitmproxy | head -n 1)"
  if [ -z "$line" ]; then
    printf 'absent'
  else
    case "$line" in
      *'[activated enabled]'*) printf 'approved' ;;
      *'waiting for user'*) printf 'pending' ;;
      *) printf 'unknown' ;;
    esac
  fi
}

# macOS local capture never sees loopback traffic. A local docker stack is
# usually reached through /etc/hosts entries that point at 127.0.0.1, and those
# requests go straight to the container: unproxied, unlogged, while mitmdump
# reports itself healthy. Print the names that /etc/hosts maps to loopback (one
# per line, stock entries excluded) so `doctor` and `start` can say so.
loopback_hosts() {
  local hosts_file="${PROXY_LAB_HOSTS_FILE:-/etc/hosts}"
  [ -r "$hosts_file" ] || return 0
  awk '
    /^[[:space:]]*#/ { next }
    $1 ~ /^127\./ || $1 == "::1" {
      for (i = 2; i <= NF; i++) {
        if ($i ~ /^#/) break
        if ($i != "localhost" && $i != "broadcasthost" && $i !~ /^ip6-/) print $i
      }
    }
  ' "$hosts_file" | sort -u
}

# `start ios --system-proxy` is the one opt-in way proxy-lab touches macOS
# settings, and only for as long as a session runs. The Simulator follows the
# Mac's proxy settings, which is the only way to reach hosts that /etc/hosts
# points at localhost. A small proxy auto-config file sends just your hosts to
# mitmproxy; everything else stays direct. The previous setting is recorded
# first and put back on stop, reset, and the next start.

# The network service behind the default route, e.g. "Wi-Fi".
ios_network_service() {
  local device
  device="$(route -n get default 2>/dev/null | awk '/interface:/ { print $2; exit }')"
  [ -n "$device" ] || return 1
  networksetup -listnetworkserviceorder 2>/dev/null | awk -v dev="$device" '
    /^\([0-9]+\)/ { name = $0; sub(/^\([0-9]+\) /, "", name) }
    index($0, "Device: " dev ")") { print name; exit }
  '
}

# "yes|url" for the automatic proxy configuration (PAC) of a service.
ios_autoproxy_read() {
  local service="$1" out enabled url
  out="$(networksetup -getautoproxyurl "$service" 2>/dev/null)" || return 1
  url="$(printf '%s\n' "$out" | awk -F': ' '$1 == "URL" { print $2 }')"
  enabled="$(printf '%s\n' "$out" | awk -F': ' '$1 == "Enabled" { print tolower($2) }')"
  [ "$url" = "(null)" ] && url=""
  printf '%s|%s' "$enabled" "$url"
}

ios_autoproxy_apply() {
  local service="$1" record="$2" enabled url
  IFS='|' read -r enabled url <<<"$record"
  if [ "$enabled" = "yes" ] && [ -n "$url" ]; then
    networksetup -setautoproxyurl "$service" "$url" >/dev/null 2>&1 || return 1
  else
    # Keep the old address on a disabled entry, as it was.
    if [ -n "$url" ]; then
      networksetup -setautoproxyurl "$service" "$url" >/dev/null 2>&1 || return 1
    fi
    networksetup -setautoproxystate "$service" off >/dev/null 2>&1 || return 1
  fi
}

# A proxy-lab PAC is always served from loopback as /proxy.pac.
ios_pac_is_ours() {
  case "$1" in
    http://127.0.0.1:*/proxy.pac) return 0 ;;
    *) return 1 ;;
  esac
}

# Network services that still carry a proxy-lab PAC with no live session behind
# it. One line each: "service|url".
ios_leftover_pacs() {
  local service record url
  command -v networksetup >/dev/null 2>&1 || return 0
  state_owner_matches "$(state_root)/ios" && return 0
  while IFS= read -r service; do
    [ -n "$service" ] || continue
    record="$(ios_autoproxy_read "$service" || true)"
    case "$record" in yes\|*) ;; *) continue ;; esac
    url="${record#*|}"
    ios_pac_is_ours "$url" || continue
    printf '%s|%s\n' "$service" "$url"
  done < <(networksetup -listallnetworkservices 2>/dev/null | tail -n +2 | sed 's/^\*//')
}

ios_clear_leftover_pacs() {
  local line service
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    service="${line%%|*}"
    networksetup -setautoproxystate "$service" off >/dev/null 2>&1 &&
      info '✓' 'cleanup' "cleared a leftover proxy-lab auto-config on $service"
  done < <(ios_leftover_pacs)
}

# A free loopback port, chosen by the OS.
free_local_port() {
  local python_bin="${PROXY_LAB_PYTHON:-}"
  [ -n "$python_bin" ] || python_bin="$(command -v python3 || true)"
  [ -n "$python_bin" ] || return 1
  "$python_bin" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

# Write the PAC for the hosts to capture and serve it on loopback. Prints the
# URL. The server pid is recorded so stop and reset can end it.
ios_pac_serve() {
  local proxy="$1" pac_dir="$STATE_DIR/pac" python_bin="${PROXY_LAB_PYTHON:-}" port server_pid
  [ -n "$python_bin" ] || python_bin="$(command -v python3 || true)"
  [ -n "$python_bin" ] || return 1
  mkdir -p "$pac_dir"
  if [ -n "$CONFIG" ]; then
    "$python_bin" -m proxy_lab.pac "$proxy" "$CONFIG" >"$pac_dir/proxy.pac" || return 1
  else
    capture_host_names | "$python_bin" -m proxy_lab.pac "$proxy" >"$pac_dir/proxy.pac" || return 1
  fi
  port="$(free_local_port)" || return 1
  "$python_bin" -m http.server "$port" --bind 127.0.0.1 --directory "$pac_dir" >/dev/null 2>&1 &
  server_pid=$!
  state_write pac_pid "$server_pid"
  sleep 0.3
  kill -0 "$server_pid" 2>/dev/null || return 1
  printf 'http://127.0.0.1:%s/proxy.pac' "$port"
}

# Ctrl-C reaches the whole foreground group, so a second one must not interrupt
# the restore it is waiting on. Children inherit an ignored signal.
state_restore_ios_proxy() {
  local directory="$1" status=0
  trap '' INT TERM HUP
  state_restore_ios_proxy_now "$directory" || status=$?
  trap - INT TERM HUP
  return "$status"
}

# 0: restored, or nothing was recorded
# 1: the restore failed, so the state must be kept
state_restore_ios_proxy_now() {
  local directory="$1" service pac_pid
  pac_pid="$(state_read_from "$directory" pac_pid)"
  if [ -n "$pac_pid" ]; then
    kill "$pac_pid" 2>/dev/null || true
  fi
  [ -f "$directory/previous_ios_service" ] || return 0
  service="$(state_read_from "$directory" previous_ios_service)"
  ios_autoproxy_apply "$service" "$(state_read_from "$directory" previous_ios_auto)"
}

# Can simctl run under this developer dir? Command Line Tools are a legal
# xcode-select target and contain no simctl, so `xcrun` being on PATH says
# nothing. Ask the command the tool actually runs, and read the exit status
# rather than the output: a working simctl with nothing booted still succeeds,
# which is exactly what separates a missing toolchain from an idle device list.
# With no argument, test whatever is selected right now.
simctl_works() {
  if [ -n "${1:-}" ]; then
    DEVELOPER_DIR="$1" xcrun simctl list devices booted >/dev/null 2>&1
  else
    xcrun simctl list devices booted >/dev/null 2>&1
  fi
}

# Which Xcode to drive. Never pins a version, and never overrides a working
# environment: whatever is selected right now wins if simctl answers under it,
# so a shimmed or pre-configured simctl keeps working untouched. Only when it
# does not answer — typically xcode-select pointing at Command Line Tools — is
# an installed Xcode sought. The result is exported to this run's own process
# tree; the machine-wide selection belongs to the user. Returns the resolved
# path when one was chosen and exported, and empty when the ambient selection
# was kept as-is.
ios_developer_dir() {
  if [ -n "$IOS_DEVELOPER_DIR" ]; then
    printf '%s' "$IOS_DEVELOPER_DIR"
    return 0
  fi

  local listing candidate devices fallback=""

  if simctl_works; then
    return 0
  fi

  # An explicit DEVELOPER_DIR is a decision, not a default. A broken one is an
  # error rather than an invitation to quietly drive a different Xcode.
  [ -z "${DEVELOPER_DIR:-}" ] || return 1

  listing="$(find /Applications "$HOME/Applications" -maxdepth 1 -name 'Xcode*.app' 2>/dev/null |
    sort -Vr || true)"
  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    candidate="$candidate/Contents/Developer"
    simctl_works "$candidate" || continue
    devices="$(DEVELOPER_DIR="$candidate" xcrun simctl list devices booted 2>/dev/null || true)"
    [ -n "$fallback" ] || fallback="$candidate"
    # A device booted by an older Xcode is invisible to the newer toolchain, so
    # a candidate that can see one outranks a merely newer candidate.
    if printf '%s' "$devices" | grep -q '(Booted)'; then
      IOS_DEVELOPER_DIR="$candidate"
      break
    fi
  done <<EOF
$listing
EOF
  IOS_DEVELOPER_DIR="${IOS_DEVELOPER_DIR:-$fallback}"

  [ -n "$IOS_DEVELOPER_DIR" ] || return 1
  export DEVELOPER_DIR="$IOS_DEVELOPER_DIR"
  printf '%s' "$IOS_DEVELOPER_DIR"
}

# Every launcher cleans up the same way on every exit path.
trap_cleanup() {
  trap cleanup EXIT
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
  trap 'cleanup; exit 129' HUP
  # --duration elapsed: the run ended as planned.
  trap 'cleanup; exit 0' USR1
}

validate_common_files() {
  if [ "${PLATFORM_NAME:-}" = android ]; then
    case "${PORT:-}" in
      ''|*[!0-9]*) fail 'arguments' "PORT must be a positive integer: ${PORT:-}" ;;
    esac
    [ "$PORT" -gt 0 ] 2>/dev/null || fail 'arguments' "PORT must be greater than zero: $PORT"
    case "${BOOT_TIMEOUT:-}" in
      ''|*[!0-9]*) fail 'arguments' "BOOT_TIMEOUT must be a positive integer: ${BOOT_TIMEOUT:-}" ;;
    esac
    [ "$BOOT_TIMEOUT" -gt 0 ] 2>/dev/null || fail 'arguments' "BOOT_TIMEOUT must be greater than zero: $BOOT_TIMEOUT"
  fi

  if [ -n "${DURATION:-}" ]; then
    case "$DURATION" in
      *[!0-9]*) fail 'arguments' "duration must be a positive integer of seconds: $DURATION" ;;
    esac
    [ "$DURATION" -gt 0 ] 2>/dev/null || fail 'arguments' "duration must be greater than zero: $DURATION"
  fi

  case "${LOG_FORMAT:-text}" in
    text|jsonl) ;;
    *) fail 'arguments' "unknown log format: ${LOG_FORMAT:-} (expected text or jsonl)" ;;
  esac
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
    MITMDUMP=(uv tool run --from "$MITMPROXY_SPEC" mitmdump)
    MITMPROXY_SOURCE="uv $MITMPROXY_SPEC"
  else
    return 1
  fi
}

select_mitmproxy() {
  try_select_mitmproxy || fail 'mitmproxy' 'mitmdump not found and uv is not installed' \
    'brew install uv   # then this tool fetches the pinned mitmproxy' \
    'or: brew install --cask mitmproxy' \
    'run: proxy-lab doctor'
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
  local minimum="${1:-}" current
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

# Every host name /etc/hosts defines on purpose, any address, one per line.
capture_host_names() {
  local hosts_file="${PROXY_LAB_HOSTS_FILE:-/etc/hosts}"
  [ -r "$hosts_file" ] || return 0
  awk '
    /^[[:space:]]*#/ { next }
    {
      for (i = 2; i <= NF; i++) {
        if ($i ~ /^#/) break
        if ($i != "localhost" && $i != "broadcasthost" && $i !~ /^ip6-/) print tolower($i)
      }
    }
  ' "$hosts_file" | sort -u
}

# Say what will be intercepted, and stop if the answer is "nothing". Only host
# names from a domains file, or else from /etc/hosts, are ever intercepted:
# this is a local-development tool, not a tool for watching all your traffic.
# Status 1 from the validator means an invalid file; anything else means Python
# could not run the check, which mitmproxy will then do on load.
require_config_file() {
  local status=0 count
  if [ -n "$CONFIG" ]; then
    validate_config_file || status=$?
    case "$status" in
      0) info '✓' 'domains' "$CONFIG — only these hosts are intercepted" ;;
      1) fail 'config' "invalid config: $CONFIG" "fix the domains list; see README.md" ;;
      *) info '!' 'domains' "deferred validation to mitmproxy: $CONFIG" ;;
    esac
    export PROXY_LAB_CONFIG="$CONFIG"
    return 0
  fi
  count="$(capture_host_names | wc -l | tr -d ' ')"
  [ "$count" -gt 0 ] || fail 'domains' 'nothing to capture' \
    'add your dev hosts to /etc/hosts' \
    'or list them in a domains file: proxy-lab start <platform> domains.yml'
  info '✓' 'domains' "$count hosts from /etc/hosts — nothing else is intercepted"
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

# Only a session that is provably not ours is adopted: a recorded owner that is
# gone, or a PID that has since been recycled, can never release its own state.
# Such a PID is never signalled — it is not the process we recorded.
# A launcher that was killed outright cannot clean up after itself, and its
# mitmdump carries on. For iOS that means traffic still being redirected to a
# proxy nobody reads. End the recorded one, but only if the PID is still a
# mitmproxy: a recycled PID belongs to someone else and is left alone.
state_stop_recorded_proxy() {
  local directory="$1" pid command waited=0
  pid="$(state_read_from "$directory" proxy_pid)"
  state_pid_alive "$pid" || return 0
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command" in
    *mitmdump*|*mitmproxy*) ;;
    *) return 0 ;;
  esac
  kill -TERM "$pid" 2>/dev/null || true
  while state_pid_alive "$pid" && [ "$waited" -lt 20 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if state_pid_alive "$pid"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  info '✓' 'cleanup' "ended a leftover mitmdump (pid $pid)"
}

state_adopt_stale() {
  local directory="$1" restore_status=0 serial
  state_stop_recorded_proxy "$directory"
  state_restore_ios_proxy "$directory" ||
    fail 'session' "couldn't put your Mac's proxy setting back" \
      'run: proxy-lab reset ios'
  state_restore_android_proxy "$directory" || restore_status=$?
  case "$restore_status" in
    0) ;;
    2)
      serial="$(state_read_from "$directory" serial)"
      info '!' 'session' "${serial:-the recorded device} is not reachable; its proxy was not restored"
      ;;
    *)
      printf '  ! %-11s %s\n' 'session' \
        'could not restore the proxy left behind by the stopped session' >&2
      ;;
  esac
  rm -rf "$directory"
  mkdir "$directory" 2>/dev/null
}

# Where a session's output is written. Deliberately outside the state directory:
# `stop` removes the state directory, and a log that vanished with it would be
# useless to the caller that just asked for it.
log_path_for() {
  local platform="$1" port="${2:-0}"
  printf '%s/logs/%s-%s.log' "$(state_root)" "$platform" "$port"
}

state_refuse_live() {
  local directory="$1" platform="$2"
  state_owner_matches "$directory" || return 0
  fail 'session' "another proxy-lab session is active for $platform" \
    "run: proxy-lab status $platform" "or stop it: proxy-lab stop $platform"
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
    state_refuse_live "$STATE_DIR" "$platform"
    if state_pid_alive "$owner"; then
      info '!' 'session' "PID $owner is not the recorded owner; reclaiming its state"
    else
      info '!' 'session' 'reclaiming state left by a stopped session'
    fi
    state_adopt_stale "$STATE_DIR" ||
      fail 'session' "stale state exists: $STATE_DIR" \
        "run: proxy-lab reset $platform"
  fi

  STATE_ACQUIRED=1
  state_write owner "$$"
  state_write owner_command "$(ps -p "$$" -o command= 2>/dev/null || true)"
  state_write owner_start "$(ps -p "$$" -o lstart= 2>/dev/null | sed 's/^[[:space:]]*//')"
  state_write platform "$platform"
  state_write port "$port"
  state_write config "${CONFIG:-${PROXY_LAB_HOSTS_FILE:-/etc/hosts}}"
  [ -z "${UDID:-}" ] || state_write udid "$UDID"
  [ -z "${LOG_FORMAT:-}" ] || state_write log_format "$LOG_FORMAT"
  [ -z "${DURATION:-}" ] || state_write duration "$DURATION"
  # Record what is actually running, so `status` can report it. mitmproxy
  # resolves unpinned by default, so this is the only version evidence.
  if [ -n "${MITMDUMP+x}" ] && [ "${#MITMDUMP[@]}" -gt 0 ]; then
    state_write mitmproxy "$(mitmproxy_version || true)"
  fi
  state_write mitmproxy_source "${MITMPROXY_SOURCE:-unknown}"
  # `proxy-lab logs` finds the traffic through this path.
  state_write log "$(log_path_for "$platform" "$port")"
  state_write started_at "$(date +%s)"
}

state_release() {
  [ "${STATE_ACQUIRED:-0}" -eq 1 ] || return 0
  [ "$(state_read owner)" = "$$" ] || return 0
  rm -rf "$STATE_DIR"
  STATE_ACQUIRED=0
}

# Stop the proxy after a fixed number of seconds so a bounded run always exits.
# Uses a watchdog subshell rather than a sleep in the foreground so the proxy
# output keeps flowing while the timer runs. USR1 tells the launcher the run
# ended as planned; the redirect keeps the timer from holding a caller's pipe
# open after an early stop.
start_duration_watchdog() {
  [ -n "${DURATION:-}" ] || return 0
  (
    sleep "$DURATION"
    kill -USR1 "$$" 2>/dev/null || true
  ) >/dev/null 2>&1 </dev/null &
  # shellcheck disable=SC2034 # consumed by the launcher cleanup trap
  DURATION_PID=$!
}

# Re-exec the launcher detached from the terminal, with output going to the
# session log. The child re-runs the identical script, so preflight, CA install,
# and cleanup behave exactly as they do in the foreground.
run_detached() {
  local script="$1" log="$2"
  shift 2
  mkdir -p "$(dirname "$log")"
  : >"$log"
  # nohup plus redirected stdio already detaches the child. Do not disown it:
  # disowning drops the job from the table, after which `wait` can no longer
  # report its exit status and the caller sees a failed run as status 0.
  PROXY_LAB_DETACH_CHILD=1 \
    nohup bash "$script" "$@" >>"$log" 2>&1 </dev/null &
  DETACH_PID=$!
}

# Parent side of --detach: hand off to a detached child and block only until the
# session reports itself ready, or the child dies. Exits 0 on success so the
# parent never continues into the run it just delegated; a caller that sees a
# zero exit status can rely on the proxy being up.
run_detach_handoff() {
  local script="$1"
  shift
  [ "${DETACH:-0}" -eq 1 ] || return 0
  # The child owns the real run and must not hand off a second time.
  [ -n "${PROXY_LAB_DETACH_CHILD:-}" ] && return 0

  local root log dir timeout deadline child_status port
  root="$(state_root)"
  if [ "$PLATFORM_NAME" = "android" ]; then
    dir="$root/android-${PORT:-8080}"
    port="${PORT:-8080}"
  else
    dir="$root/ios"
    port=0
  fi
  log="$(log_path_for "$PLATFORM_NAME" "$port")"
  timeout="${DETACH_READY_TIMEOUT:-600}"
  # Refuse before spawning: the child would fail anyway, and truncating the
  # log here would erase the live session's traffic.
  state_refuse_live "$dir" "$PLATFORM_NAME"

  run_detached "$script" "$log" "$@"
  if [ "${JSON_OUTPUT:-0}" != "1" ]; then
    info '…' 'detached' "starting in the background (pid $DETACH_PID)"
  fi

  deadline=$((SECONDS + timeout))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ -f "$dir/ready" ] && [ "$(state_read_from "$dir" owner)" = "$DETACH_PID" ]; then
      port="$(state_read_from "$dir" port)"
      if [ "${JSON_OUTPUT:-0}" = "1" ]; then
        printf '{'
        printf '"ok": true, '
        json_field 'platform' "$PLATFORM_NAME"; printf ', '
        printf '"pid": %s, ' "$DETACH_PID"
        printf '"port": %s, ' "$port"
        json_field 'log' "$log"; printf ', '
        json_field 'log_format' "${LOG_FORMAT:-text}"; printf ', '
        json_field 'stop' "proxy-lab stop $PLATFORM_NAME"
        printf '}\n'
      else
        info '✓' 'detached' "pid $DETACH_PID — log $log"
      fi
      exit 0
    fi
    if ! kill -0 "$DETACH_PID" 2>/dev/null; then
      child_status=0
      wait "$DETACH_PID" 2>/dev/null || child_status=$?
      tail -n 20 "$log" >&2
      fail 'session' "the detached run exited before it was ready (status $child_status)" \
        "full output: $log"
    fi
    sleep 0.2
  done
  fail 'timeout' "the detached run was not ready after ${timeout}s" \
    "full output: $log"
}

# Child side: record readiness so the parent --detach caller can stop waiting.
mark_session_ready() {
  [ "${STATE_ACQUIRED:-0}" -eq 1 ] || return 0
  state_write ready "$(date +%s)"
}

android_device_ready() {
  local serial="${1:-}"
  [ -n "$serial" ] || return 1
  command -v adb >/dev/null 2>&1 || return 1
  adb devices | awk -v selected="$serial" '$1 == selected && $2 == "device" { found = 1 } END { exit(found ? 0 : 1) }'
}

# 0: restored, or nothing was recorded
# 1: the device answered but the restore failed, so the state must be kept
# 2: the device is gone, so there is nothing left to restore it on
state_restore_android_proxy() {
  local directory="$1" serial previous
  [ -f "$directory/previous_proxy" ] || return 0
  serial="$(state_read_from "$directory" serial)"
  previous="$(state_read_from "$directory" previous_proxy)"
  android_device_ready "$serial" || return 2

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
  command -v xcrun >/dev/null 2>&1 || return 4
  ios_developer_dir >/dev/null || return 4
  xcrun simctl list devices booted 2>/dev/null |
    sed -nE 's/.*\(([0-9A-Fa-f-]+)\) \(Booted\).*/\1/p' |
    head -n 1
}

# 2 no xcrun, 4 no usable Xcode, 3 no booted simulator, 1 add-root-cert failed.
trust_ios_certificate() {
  local udid
  command -v xcrun >/dev/null 2>&1 || return 2
  ios_developer_dir >/dev/null || return 4
  udid="$(ios_booted_udid || true)"
  [ -n "$udid" ] || return 3
  xcrun simctl keychain "$udid" add-root-cert "$CERT" >/dev/null 2>&1 || return 1
  info '✓' 'simulator' "CA trusted in $udid"
}
