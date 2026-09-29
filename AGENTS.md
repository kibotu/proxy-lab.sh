# AGENTS.md

Operational contract for coding agents driving proxy-lab. The README explains
*why* the tool works this way; this file explains *how to drive it*.

## Canonical invocation

```bash
uvx proxy-lab <command> [platform] [options]
```

From a checkout, use `uvx --from . proxy-lab …`. Pinning this wrapper does not
pin mitmproxy.

## The one command you want

```bash
proxy-lab start <android|ios> --detach --duration 30 --log-format jsonl
```

Returns once the proxy is **up**, stops itself after 30s, and leaves the traffic
on disk. This is the shape to build automation around: it terminates, it does not
need a TTY, and it does not leave a stale Android proxy setting behind.

Read the result afterwards:

```bash
proxy-lab logs <platform> --lines 200
```

## Do not do this

`proxy-lab start <platform>` without `--detach` runs in the foreground until
Ctrl-C. A non-interactive agent has no way to stop it, and if it is killed with
SIGKILL the emulator keeps pointing at a proxy that no longer exists. Always pass
`--detach`, or `--duration` when you want the foreground path.

## Commands

| Command | Purpose |
| --- | --- |
| `start <platform> [--detach] [--duration N] [--log-format text\|jsonl]` | Run the proxy. |
| `status [platform] [--json]` | List recorded sessions. |
| `stop <platform>` | Stop a recorded session and restore the previous proxy. |
| `reset <platform>` | Recovery for stale state or a proxy that will not restore. |
| `logs [platform] [--follow] [--lines N]` | Print captured traffic from a detached run. |
| `doctor [platform] [--json]` | Preflight without starting anything. |
| `init android` | Write `network_security_config.xml` into the project. |
| `trust ios` | Install the CA into the Simulator keychain. |

## Exit codes

Branch on these instead of parsing messages.

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 2 | Invalid arguments or flags. |
| 3 | Invalid config, or a missing input file. |
| 4 | A required tool is not on `PATH`. |
| 5 | Device, emulator, or CA failure. |
| 6 | The port is already in use. |
| 7 | mitmproxy is missing, too old, or failed to start. |
| 8 | Session state conflict, or a readiness timeout. |

`doctor` exits 1 when any check fails, so it works as a CI gate.

## Machine-readable output

`--json` is available on `status`, `stop`, `doctor`, and `logs`. The document is
the **last line** of stdout; earlier lines are human context, so parse the last
line rather than the whole stream.

- `status --json` → array of sessions, each with `state` (`running`/`stale`),
  `log`, `mitmproxy`, `log_format`.
- `doctor --json` → `{ok, issues, checks: [{status, name, detail}]}` where
  `status` is `ok`, `warn`, or `fail`.

## Traffic output

`--log-format jsonl` emits one JSON object per line with `method`, `url`,
`status_code`, `request_headers`, `response_headers`, and `duration_ms`. The
request event has `status_code: null`; the response event carries the result.

Credential-bearing query parameters and headers (`Authorization`, `Cookie`,
`X-Api-Key`, …) are redacted to `<r>`. Note that mitmproxy's own console flow
display prints unredacted URLs — that is mitmdump, not this tool. Use `--jsonl`
plus `jq` rather than grepping raw output when secrets are in play.

## Steps that need a human

The agent cannot complete these. Detect them early and say so rather than
retrying.

1. **iOS network extension.** The first `start ios` triggers a macOS approval
   prompt. `doctor --json` surfaces this as a `capture` check. There is no
   non-interactive way to read the approval state.
2. **Android emulator image.** CA install needs `adb root`, which "Google APIs
   Play Store" images refuse. Use a "Google APIs" AVD.
3. **Android debug build.** The app must trust user-installed CAs. Run
   `proxy-lab init android` and add the printed attribute to
   `AndroidManifest.xml`. This is a source change — never commit it for a
   release build.

## Recommended agent workflow

```bash
proxy-lab doctor android --json     # 1. environment is usable?
proxy-lab init android              # 2. write the CA trust config (once)
proxy-lab start android --detach --log-format jsonl   # 3. proxy up
#    … exercise the app or run the test suite …
proxy-lab logs android              # 4. read what it called
proxy-lab stop android              # 5. always clean up
```

If a session may already exist, `proxy-lab status --json` first; `start` refuses
to run twice for the same platform and port.

## Recovery

`stop` restores the exact Android proxy value recorded before the run. When a
session was SIGKILLed and state went stale, `start` reclaims it automatically. If
that is not enough, `reset` is the explicit recovery path. `logs` keeps working
after both, because the log lives outside the state directory.

## Environment variables

Undocumented in older versions but fully supported:

| Variable | Effect |
| --- | --- |
| `PROXY_LAB_STATE_DIR` | Session state and log root. Set this to isolate parallel runs. |
| `PROXY_LAB_CONFIG` | Domain list, equivalent to the positional `domains.yml`. |
| `PROXY_LAB_SCRIPTS` | Newline-separated addon scripts. |
| `PROXY_LAB_MITMDUMP` | Path to a specific `mitmdump`, bypassing discovery. |
| `MITMPROXY_SPEC` | mitmproxy spec for the uv fallback. Defaults to `mitmproxy@latest`. |
| `PROXY_LAB_SKIP_UPDATE_CHECK` | `1` disables the PyPI version check. |
| `PORT`, `AVD`, `SERIAL`, `BOOT_TIMEOUT` | Android only. |
