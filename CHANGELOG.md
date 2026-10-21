# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.5.0] - 2026-10-21

### Changed

- A plain `start ios` no longer writes to macOS network settings. It reads
  them, and reports an auto-config left behind by an earlier run instead of
  quietly putting it back. `stop` and `reset` are the commands that put a
  setting back; `start ios --system-proxy` remains the only mode that changes
  one, and only for as long as the session runs.
- The auto-config proxy-lab installs is now removed completely when it is put
  back: the address goes away with the switch. macOS has no command to unset
  an automatic proxy URL, so the address is set to a single space, which
  macOS stores as no address. A disabled entry that still carried a dead
  proxy-lab address was a trap for the next tool that switched it on.
- The record of the setting proxy-lab replaced now lives outside the session
  directory. A session that ended could no longer take it with it and lose
  the original setting.
- `doctor ios` reports an auto-config address left on a service as a warning,
  and an enabled one as a failure, as before.

## [2.4.3] - 2026-10-16

### Changed

- The package version now comes from the git tag via hatch-vcs. Nothing in the
  tree hardcodes it, so a tag and the published artifact cannot drift apart.
  Releasing is now just tagging the release commit `X.Y.Z`; there is no version
  to bump in `pyproject.toml`. `uv.lock` records the root as `(dynamic)`, so it
  no longer changes on every commit.

### Fixed

- The proxy smoke test no longer starts the addon without a domains file.
  Since 2.4.0 that made it capture the host names in `/etc/hosts`, which on a
  CI runner holds no `example.com`, so the router never logged the fetch.

## [2.4.0] - 2026-10-16

### Changed

- `start ios` no longer changes macOS network settings unless you ask. The
  system-proxy mode that switched on by itself, `PROXY_LAB_IOS_MODE`, and
  `PROXY_LAB_IOS_PORT` are gone. iOS uses local capture by default, which
  can't see hosts that `/etc/hosts` points at localhost; `doctor ios` says so.
- `--system-proxy` (iOS, opt-in) captures those hosts. It sets a proxy
  auto-config that sends only your hosts to mitmproxy and picks a free port by
  itself. It is put back on `stop`, `reset`, and the next `start`, and
  `doctor` fails when a proxy-lab setting was left behind.
- Only your own hosts are intercepted. They come from the domains file if you
  pass one, otherwise from the host names in `/etc/hosts`. Everything else
  passes through untouched: not decrypted, not logged, not modified. `start`
  stops with a hint when there is nothing to capture. The bundled
  `domains.yaml` is now an example, not a default.

### Fixed

- `start` and `reset` end a `mitmdump` left behind by a launcher that was
  killed outright, so iOS traffic is no longer redirected to a proxy nobody
  reads. Only a process that is still a mitmproxy is touched.

## [2.3.3] - 2026-10-16

### Fixed

- Restoring the Mac proxy after Ctrl-C failed when Ctrl-C was pressed twice:
  the second signal also killed the `networksetup` call doing the restore.
  The restore now ignores INT/TERM/HUP while it runs, and runs before mitmdump
  is waited on (mitmdump is force-killed after about 2 seconds), so the Mac is
  not left pointed at a dying proxy. A failed restore now also prints a manual
  `networksetup` command.

### Changed

- iOS proxy mode no longer routes the whole Mac through mitmproxy. It serves a
  proxy auto-config (PAC) file that sends only the hosts in your domain list
  and the loopback hosts in `/etc/hosts` to the proxy; everything else, such as
  Outlook or the browser, stays direct. The PAC falls back to DIRECT if the
  proxy is down. The previous automatic proxy setting is restored on `stop`
  and `reset`, which also end the PAC server.

## [2.3.2] - 2026-10-16

### Added

- iOS reaches dev hosts that `/etc/hosts` points at localhost, such as a local
  docker stack. macOS local capture cannot see loopback traffic, so when
  `start ios` finds such hosts it captures through the system proxy instead,
  and puts your previous proxy settings back on `stop`, `reset`, or stale-state
  recovery. No change to `/etc/hosts` is needed, and Android is unaffected.
  `doctor ios` says which mode it will use and why.
- `PROXY_LAB_IOS_MODE=local|proxy` forces a mode. `PROXY_LAB_IOS_PORT` sets the
  proxy-mode port (default 8080). `PROXY_LAB_HOSTS_FILE` points the detection
  at another hosts file.

### Fixed

- Release 2.3.1 failed its version check and was never published; 2.3.2 carries
  the same changes with a matching `pyproject.toml`.

### Changed

- Messages are shorter and say what to do next. The network-extension error now
  also offers `PROXY_LAB_IOS_MODE=proxy` as a way around the approval.

## [2.3.0] - 2026-10-16

### Added

- `doctor ios` reports the state of mitmproxy's macOS Network Extension as a
  new `extension` check, read from `systemextensionsctl`: `ok` when approved,
  `fail` when installed but still waiting for the user, `warn` when not
  installed yet, and `warn` when the state cannot be read. This is the failure
  that looks like success: mitmdump starts, reports itself healthy, and
  captures nothing at all.
- The `capture` doctor check is gone. It warned that there was "no
  non-interactive check" for the approval, which `extension` now contradicts
  and supersedes; keeping both meant one of them was always wrong.
- `start ios` refuses to start when the extension is installed but unapproved
  (exit 5) instead of returning a proxy that will never log a request, and
  prints the System Settings path plus a pasteable
  `open x-apple.systempreferences:com.apple.LoginItems-Settings.extension`.
  The first run, where the extension does not exist yet and macOS still has to
  ask, is unaffected. After a first run the state is re-checked, so the prompt
  is reported as soon as it appears instead of leaving an empty log to
  investigate.

### Fixed

- A detached `start --json` reported the child's failure as `status 0`.
  `run_detached` disowned the child, and `wait` on a disowned PID can no
  longer report an exit status, so a run that failed with 5 looked successful
  in the message. `nohup` plus redirected stdio already detaches the process;
  the `disown` is gone, so the real status reaches the caller.
- The iOS launcher tests read the developer's real network-extension state, so
  they passed on CI, which has no `systemextensionsctl`, and failed on any Mac
  where mitmproxy was installed but not yet approved. They now stub it.

## [2.2.1] - 2026-10-16

### Fixed

- 2.2.0 broke `start ios` wherever `simctl` works without a discoverable
  `Xcode.app` — the test suite and the CI smoke job both caught it. The
  resolver required a *discovered* developer directory and treated "no
  `/Applications/Xcode*.app` found" as fatal, instead of first asking whether
  `xcrun simctl` already answers in the current environment. That is the
  common case for a shimmed or pre-configured `simctl`, and it is the case
  CI runs on Linux.
- Resolution now works from the command the tool actually runs
  (`xcrun simctl list devices booted`, exit status as the signal), rather than
  a separate `simctl list devicetypes` probe that any shim had to implement
  too. A working ambient selection is kept as-is: nothing scanned, nothing
  exported, nothing substituted.

### Added

- Two tests covering the resolution: a `simctl` that fails resolves to an
  installed Xcode under `$HOME/Applications`, and a working one is left exactly
  as it is.

## [2.2.0] - 2026-10-16

### Fixed

- The iOS CA install no longer fails silently when `xcode-select` points at the
  standalone Command Line Tools. Those are a legal `xcode-select` target, they
  are what a fresh machine usually resolves to, and they ship no `simctl` — so
  `xcrun simctl` errored out while every guard in `trust_ios_certificate`
  passed. Simulator discovery came back empty, and the run reported
  `automatic CA trust unavailable` even with a booted simulator and a healthy
  local capture.
- `doctor ios` reported `simulator: none booted` for that case and still
  returned `ok: true`. A broken toolchain is now a `fail` that names the
  selection at fault, so `doctor --json` no longer reads green while the CA
  cannot be installed.
- `start ios` distinguishes a missing toolchain from a missing simulator. Only
  the latter degrades to manual trust via `mitm.it`; an unusable developer
  directory now stops the run with the `xcode-select` command that fixes it.
- `trust ios` reports the same three reasons separately instead of one generic
  message.

### Added

- `DEVELOPER_DIR` for iOS runs: the Xcode to drive, verified to carry `simctl`.
- A per-run resolution for the iOS toolchain, with no version pinned anywhere.
  Whatever is selected right now wins if `simctl` answers under it, so an
  explicit `DEVELOPER_DIR`, a healthy `xcode-select`, and a pre-configured
  `simctl` are all left untouched. Only when it does not answer — typically
  `xcode-select` pointing at Command Line Tools — is an installed Xcode sought:
  the newest that can run `simctl`, preferring one that can see a booted
  simulator, since a device booted by an older Xcode is invisible to the newer
  toolchain. The result is exported to the run's own process tree only; the
  machine-wide `xcode-select` setting is never modified, and no step needs
  `sudo`. See 2.2.1 for a correction to this resolution.

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
