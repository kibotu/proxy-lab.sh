# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
