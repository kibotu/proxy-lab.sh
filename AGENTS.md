# AGENTS.md

How to drive proxy-lab from a coding agent. Humans should read the README.

## The command

```bash
uvx proxy-lab start android --json
uvx proxy-lab start ios --json
```

`--json` is agent mode: the proxy starts in the background, traffic is jsonl,
and stdout is **one JSON object** once the proxy is up. Exit 0 means it is
listening. Then:

```bash
# exercise the app or run tests
uvx proxy-lab logs android          # traffic (jsonl lines)
uvx proxy-lab stop android          # always clean up
```

From a checkout: `uvx --from . proxy-lab …`.

## Do not do this

`proxy-lab start <platform>` without `--json` or `--detach` runs until Ctrl-C.
A non-interactive agent cannot stop it. SIGKILL leaves the Android emulator
pointing at a dead proxy.

## Workflow

```bash
uvx proxy-lab doctor android --json     # usable? parse stdout, branch on .ok
uvx proxy-lab init android              # once: writes debug network-security-config
uvx proxy-lab start android --json      # ready document; save .log and .stop
# … exercise the app …
uvx proxy-lab logs android --lines 200
uvx proxy-lab stop android              # or the .stop command from the ready document
```

If a session may already exist: `uvx proxy-lab status --json` first. `start`
refuses to run twice for the same platform and port. Stale state from a
SIGKILL is reclaimed automatically.

## Ready document (`start --json`)

```json
{"ok": true, "platform": "android", "pid": 123, "port": 8080,
 "log": "/path/to/android-8080.log", "log_format": "jsonl",
 "stop": "proxy-lab stop android"}
```

Stdout is only that object. Failures go to stderr with an exit code below.

## Other commands

| Command | Purpose |
| --- | --- |
| `status [platform] [--json]` | List sessions (`running` / `stale`). |
| `stop <platform> [--json]` | Stop the session; restore the previous Android proxy. |
| `reset <platform>` | Recovery when stop is not enough. |
| `logs [platform] [--lines N]` | Print captured traffic. `--json` returns the file path. |
| `doctor [platform] [--json]` | Preflight. `.next` is the command to run when `.ok` is true. |
| `init android` | Write `network_security_config.xml`; print the manifest attribute. |
| `trust ios` | Install the CA into the Simulator keychain. |
| `start ios --system-proxy` | Opt in: capture hosts that `/etc/hosts` points at localhost, which local capture cannot see. Sets a PAC for those hosts only; restored on stop. The only mode that writes to macOS network settings. |

`--json` stdout is **only** the JSON document. Parse the whole stream.

`doctor --json` → `{ok, issues, next, checks: [{status, name, detail}]}`.
`status` is `ok`, `warn`, or `fail`. `doctor` exits 1 when any check fails.

## Exit codes

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

## Traffic (`--log-format jsonl`, the `--json` default)

One JSON object per line: `method`, `url`, `status_code`, `request_headers`,
`response_headers`, `duration_ms`. The request event has `status_code: null`;
the response event carries the result. Credential-bearing query parameters and
headers are redacted to `<r>`.

mitmproxy's own console still prints unredacted URLs — ignore it; read jsonl.

## Steps that need a human

Detect these early. Do not retry.

1. **iOS network extension.** The first `start ios` makes macOS ask for
   approval of mitmproxy's Network Extension. Until it is granted, mitmdump
   runs and captures nothing, so `start ios` refuses to start (exit 5) rather
   than return an empty log. `doctor --json` reports this as the `extension`
   check: `fail` means installed but unapproved, which needs a human in System
   Settings → General → Login Items & Extensions → Network Extensions;
   `warn` with `not installed yet` just means the first run has not happened.
   There is no non-interactive way to grant it.
2. **Android emulator image.** CA install needs `adb root`. Play Store images
   refuse it. Use a Google APIs AVD.
3. **Android debug build.** The app must trust user CAs. `init android` writes
   the XML; someone still has to add the printed attribute to
   `AndroidManifest.xml`. Never commit that for a release build.

No Xcode is a human step. proxy-lab resolves the iOS toolchain itself, in order:
an explicit `DEVELOPER_DIR`, the `xcode-select -p` selection if that directory
can run `simctl`, then the newest installed Xcode that can. No version is
pinned. When none resolves, `doctor ios` fails with `no usable Xcode` and
`start ios` exits 5; it needs `sudo xcode-select -s <Xcode>.app/Contents/Developer`.

## Recovery

`stop` restores the Android proxy recorded before the run. proxy-lab changes macOS network settings only for `start ios --system-proxy`; `stop` and `reset` put them back. A plain `start ios` only reads them, and says so when it finds one left behind — it never writes. `doctor` fails with `system proxy` when one was left behind: run `reset ios`. `start` and `reset` also end a `mitmdump` left behind by a killed launcher. `logs` still works
after stop, because the log lives outside the state directory. If that is not
enough: `reset`.

## Environment

| Variable | Effect |
| --- | --- |
| `PROXY_LAB_STATE_DIR` | Isolate parallel runs. |
| `PROXY_LAB_CONFIG` | Domain list (same as the positional `domains.yml`). Without one, the host names in `/etc/hosts` are captured. Only listed hosts are intercepted. |
| `PROXY_LAB_SCRIPTS` | Newline-separated addon scripts. |
| `PROXY_LAB_MITMDUMP` | Specific `mitmdump`, skipping discovery. |
| `MITMPROXY_SPEC` | uv fallback. Default `mitmproxy==12.2.3`. |
| `DEVELOPER_DIR` | iOS only. Xcode to drive. Honoured when set, verified to carry `simctl`. |
| `PORT`, `AVD`, `SERIAL`, `BOOT_TIMEOUT` | Android only. |
 |
