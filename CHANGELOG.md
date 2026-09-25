# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
