# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-09-29

### Added

- `start --json` is agent mode: it detaches, defaults traffic to jsonl, and
  prints one ready document (`ok`, `pid`, `log`, `stop`) once the proxy is up.
  `--detach` and `--duration` remain available on their own.
- `--json` on `status`, `stop`, `doctor`, and `logs`. Stdout is only that
  document. `doctor --json` includes `next`, the command to run when the
  environment is usable.
- `--log-format jsonl` emits one JSON object per request and per response, with
  method, status code, headers, body sizes, and timing, for consumption by
  `jq` or any line-oriented parser. Credential-bearing headers are redacted
  alongside the existing query-parameter redaction.
- Distinct exit codes so a script can branch on the reason a run failed:
  2 arguments, 3 config/input, 4 missing tool, 5 device/CA, 6 port,
  7 mitmproxy, 8 session state.
- `proxy-lab init android` writes `network_security_config.xml` into the
  project's debug resource tree, walks up from the current directory to find
  that tree, and prints whether the manifest attribute is already present.
  iOS needs no file and says so.
- `doctor` reports whether a simulator is booted, warns on Play Store AVDs,
  locates `adb` in the Android SDK without a PATH edit, and names the next
  command. Local capture still needs the network extension approved once
  through a GUI prompt that cannot be scripted.
- Session state records the resolved mitmproxy version, its source, the log
  path, and the log format, all reported by `status`.
- `AGENTS.md` documents the operational contract for coding agents.

### Fixed

- `--detach` no longer leaves the launching process running alongside the
  detached child; the parent exits once the child reports itself ready.
- `proxy-lab start --detach` honours `DETACH=1` from the environment. The
  Python CLI talks in env vars; wiping them during bash argument parsing made
  detached start a no-op.
- `start ios` no longer fails a session that was actually healthy. Local
  capture has no listening port to poll, so the launcher slept a second and
  checked whether mitmdump was still alive — a guess that read a quickly
  finishing mitmdump as a failed start and exited 7, failing the CI smoke job
  on both platforms. Readiness is now declared directly. An unapproved network
  extension still fails, because mitmdump reports it and exits on its own.

### Changed

- The uv fallback pins `mitmproxy==12.2.3`. A host `mitmdump` still wins.
  Override with `MITMPROXY_SPEC`. `doctor` (not `start`) mentions a newer
  PyPI release, so everyday runs do not phone home.
- Documented `PROXY_LAB_MITMDUMP`, `MITMPROXY_SPEC`, and
  `PROXY_LAB_SKIP_UPDATE_CHECK`.

## [2.0.2] - 2026-09-29

### Fixed

- `start` now recovers from a stale session on its own instead of asking for a manual
  `proxy-lab reset`. State left by a session that is no longer running can never be
  released by that session, so it is reclaimed at startup — restoring the proxy the
  stopped run had captured first, so the new run does not record proxy-lab's own
  leftover setting as the user's.
- A session is only reclaimed when the recorded owner is gone or its PID has been
  recycled; such a PID is never signalled, and a live proxy-lab session still refuses
  to start, exactly as before.

## [2.0.1] - 2026-09-29

### Fixed

- `reset`/`stop` no longer deadlock when the recorded emulator is gone. Restoring the
  proxy previously failed hard whenever `adb` could not reach the device, which left
  the stale state directory in place and blocked every following `start` behind a
  "run `proxy-lab reset`" hint that ran the same failing path. An unreachable device
  now releases the state, since a dead emulator takes its proxy setting with it.
- `reset` no longer aborts on the first session it cannot fully restore: the remaining
  sessions and the Android proxy sweep still run, and the exit status reports the
  failure.

## [2.0.0] - 2026-09-25

### Added

- Owner-scoped session state with `status`, `stop`, `reset`, and `doctor` commands.
- Automatic iOS Simulator CA installation through `simctl`, with manual fallback.
- Strict configuration validation, URL redaction, repeatable user addons, and generic examples.
- Unit, fake-tool, and packaged-wheel tests.

### Changed

- The release workflow now requires the Git tag to match the checked-in package version.
- Android cleanup restores the exact proxy value that existed before the run and never kills an unknown listener.
- Terminal-close (`SIGHUP`) cleanup now follows the same path as `Ctrl-C` and `SIGTERM`.

## [1.1.1] - 2026-09-25

### Changed

- Documented the published `uvx proxy-lab` invocation.

## [1.1.0] - 2026-09-25

### Added

- `uvx` support: run the proxy straight from GitHub, no clone needed —
  `uvx --from git+https://github.com/kibotu/proxy-lab.sh proxy-lab start <android|ios> [domains.yml]`
  (`pyproject.toml`, `proxy_lab/`).
- Optional domains file argument for `proxy-lab start`, exported as
  `PROXY_LAB_CONFIG` — honoured by both start scripts and `local_router.py`, so
  a checkout can also run `PROXY_LAB_CONFIG=my.yml ./android/start-proxy.sh`.
  Without it, the bundled `domains.yaml` applies, unchanged.
- Release workflow: pushing a tag shaped `X.Y.Z` (no `v` prefix) builds the
  wheel and sdist at that version and publishes a GitHub Release
  (`.github/workflows/release.yml`).

### Changed

- The iOS launcher now uses mitmproxy's macOS local-capture mode with the
  `Simulator` process filter and `--showhost`; it does not boot a simulator.
  The filter is intended to cover simulators launched from Xcode or Device Hub,
  without macOS or app proxy settings.
- Both launchers prefer a host `mitmdump` and fall back to uv's
  `mitmproxy@latest` resolver when one is not installed. Preflight logs the
  selected version and, when the network check is available, reports a newer
  stable release on PyPI.
