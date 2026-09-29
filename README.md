# proxy-lab.sh

[![CI](https://github.com/kibotu/proxy-lab.sh/actions/workflows/ci.yml/badge.svg)](https://github.com/kibotu/proxy-lab.sh/actions/workflows/ci.yml)
[![Release](https://github.com/kibotu/proxy-lab.sh/actions/workflows/release.yml/badge.svg)](https://github.com/kibotu/proxy-lab.sh/actions/workflows/release.yml)
[![GitHub Release](https://img.shields.io/github/v/release/kibotu/proxy-lab.sh)](https://github.com/kibotu/proxy-lab.sh/releases)
[![PyPI](https://img.shields.io/pypi/v/proxy-lab)](https://pypi.org/project/proxy-lab/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)](#requirements)
[![Shell](https://img.shields.io/badge/shell-bash-informational)](#project-layout)

**See your app's HTTPS traffic with one command.** proxy-lab.sh starts [mitmproxy](https://www.mitmproxy.org/) for the **Android emulator** and **iOS Simulator**. It avoids `/system` remounts and macOS proxy settings; certificate setup remains explicit where the platform requires it.

```bash
# iOS
uvx proxy-lab start ios

# Android
uvx proxy-lab start android
```

These commands install the published release. From a checkout, use
`uvx --from . proxy-lab start ios` (or `android`) to run the current source.
Pinning the wrapper does not pin mitmproxy.

![proxy-lab.sh terminal output showing intercepted HTTPS requests](docs/teaser.png)

## Contents

- [Quickstart](#quickstart) — [Android](#android), [iOS](#ios)
- [Automation](#automation) — running it from a script or an agent
- [Choose the domains to log](#choose-the-domains-to-log)
- [Addons and examples](#addons-and-examples)
- [Options](#options)
- [Lifecycle and diagnostics](#lifecycle-and-diagnostics)
- [Exit codes](#exit-codes)
- [Run from a clone](#run-from-a-clone)
- [Requirements](#requirements)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Why Android needs this](#why-android-needs-this)
- [Scope and alternatives](#scope-and-alternatives)
- [Versions and releases](#versions-and-releases)
- [Project layout](#project-layout)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

Driving this tool from a coding agent or CI script? Read [AGENTS.md](AGENTS.md)
instead. It is the short operational contract: the one command to use, the exit
codes, and the JSON shapes.

## Quickstart

Install [uv](https://docs.astral.sh/uv/) to run the commands below. The scripts prefer a host-installed `mitmdump`; without one, they use uv to resolve `mitmproxy@latest`.

```bash
brew install uv

# optional: use a host binary instead of the uv fallback
brew install --cask mitmproxy
```

Then follow the path for your platform. If no host `mitmdump` is installed, the first run resolves the latest compatible mitmproxy release, downloading it when needed. Later runs are usually quick.

### Android

**1. Let your debug build trust user-installed certificates.** Most apps do not trust user-installed CAs by default. Write the file with:

```bash
uvx proxy-lab init android
```

It writes `app/src/debug/res/xml/network_security_config.xml` and prints the manifest attribute to add. The equivalent hand-written content is:

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
   <debug-overrides>
      <trust-anchors>
         <certificates src="user" />
      </trust-anchors>
   </debug-overrides>
</network-security-config>
```

Point to it from the `<application>` tag in `AndroidManifest.xml`:

```xml
<application
        android:networkSecurityConfig="@xml/network_security_config"
        ... >
```

`<debug-overrides>` applies only to debuggable builds, so it does not weaken a release build. Keep the file in the debug source set. See the [network security config docs](https://developer.android.com/privacy-and-security/security-config) and [why this is necessary](#why-android-needs-this).

**2. Start the proxy:**

```bash
uvx proxy-lab start android
```

The script checks your tools, reuses a running emulator or boots one, installs the mitmproxy CA into the user trust store, and sets the emulator proxy to `10.0.2.2:$PORT` (default `8080`). The first CA install reboots the emulator once per AVD.

**3. Start your debug build.** Requests print in the terminal as they happen.

**4. Press `Ctrl-C` or close the terminal.** The proxy stops and restores the Android proxy setting that existed before the run. The emulator keeps running. If the launcher is killed with `SIGKILL` or the machine loses power, use `uvx proxy-lab status android` followed by `uvx proxy-lab stop android` or `uvx proxy-lab reset android`.

> Use a **Google APIs** AVD image. "Google APIs Play Store" images refuse `adb root`, and the CA install needs it.

### iOS

**1. Start a simulator.** Launch it in Xcode or Device Hub. The launcher does not boot one.

**2. Start the proxy:**

```bash
uvx proxy-lab start ios
```

The launcher selects the first booted Simulator, or you can select one explicitly:

```bash
uvx proxy-lab start ios --udid 11111111-1111-1111-1111-111111111111
```

It also attempts to install the mitmproxy CA into the Simulator keychain with `simctl`. To perform that step explicitly:

```bash
uvx proxy-lab trust ios --udid 11111111-1111-1111-1111-111111111111
```

**3. Allow mitmproxy's network extension.** The launcher requests local capture with the `Simulator` process filter. It is intended to cover simulators launched from Xcode or Device Hub. If macOS has not approved the redirector yet, approve the prompt. Local capture is outbound-only; no system or app proxy setting is required. If you previously configured a manual system proxy, turn it off so it does not duplicate the capture path.

**4. Trust the CA manually if needed.** If automatic `simctl` installation is unavailable, open [`mitm.it`](http://mitm.it) in the simulator's Safari and download the profile. The page is served through local capture, so extension approval must work first. Install it under **Settings → General → VPN & Device Management**, then enable full trust under **Settings → General → About → Certificate Trust Settings**.

**5. Start your app.** Press `Ctrl-C` or close the terminal to stop the proxy. If the process is killed with `SIGKILL`, use `uvx proxy-lab stop ios`.

No root or reboot is required. HTTPS interception still requires the simulator to trust the CA.

## Automation

`start` runs in the foreground until Ctrl-C, which is right for a human at a terminal and wrong for a script or an agent. Three options make a run bounded and non-interactive.

**`--detach`** runs the proxy in the background and returns once it is **up**, so a zero exit status means the proxy is listening:

```bash
uvx proxy-lab start android --detach
uvx proxy-lab logs android --follow     # tail the traffic
uvx proxy-lab stop android
```

**`--duration SECONDS`** stops the run automatically, on the foreground path as well:

```bash
uvx proxy-lab start ios --duration 60
```

**`--log-format jsonl`** replaces the `[local_router]` lines with one JSON object per line, carrying method, status code, headers, and timing:

```bash
uvx proxy-lab start android --detach --log-format jsonl
uvx proxy-lab logs android | jq -r 'select(.status_code) | "\(.status_code) \(.method) \(.url)"'
```

The request event has `status_code: null`; the response event carries the result. Credential-bearing query parameters and headers (`Authorization`, `Cookie`, `X-Api-Key`, and similar) are redacted to `<r>`. mitmproxy's own console flow display still prints unredacted URLs — that is `mitmdump`, not this tool — so prefer `jsonl` over grepping raw output when secrets are in play.

`status`, `stop`, `doctor`, and `logs` accept `--json` for machine-readable output. The JSON document is the last line of stdout; the lines before it are human context.

```bash
uvx proxy-lab status --json | jq '.[].state'
uvx proxy-lab doctor --json | jq -r '.checks[] | select(.status=="fail")'
```

The Android debug-build step has a command too, so nobody has to hand-write the XML:

```bash
uvx proxy-lab init android    # writes app/src/debug/res/xml/network_security_config.xml
```

See [AGENTS.md](AGENTS.md) for the exit-code table and a copy-pasteable workflow.

## Choose the domains to log

Requests appear in the mitmdump output. Matching hosts also get a `[local_router]` line, which makes your own API easy to find in a busy log.

Write your list in a YAML file:

```yaml
domains:
   - ".example.com"   # subdomains only: api.example.com yes, example.com no
   - "acme.dev"       # literal suffix; use ".acme.dev" for domain boundaries
```

Entries are literal suffix matches. A leading dot is safest for subdomains; without one, `acme.dev` also matches names such as `notacme.dev`.

Pass the file as the last argument:

```bash
uvx proxy-lab start android my-domains.yml
```

Without an argument you get the [bundled `domains.yaml`](domains.yaml), which lists `.example.com` only. Keep a custom file with the project if the team should share the same filter. The loader validates the file, requires a `domains` list, and rejects malformed entries before mitmproxy starts.

## Addons and examples

Load one or more mitmproxy addons after the built-in domain logger. They run in the order supplied:

```bash
uvx proxy-lab start android \
  --script examples/modify_response.py \
  --script examples/mock_response.py
```

Only load addons you trust; they can modify traffic and expose sensitive data. Generic examples live in [`examples/`](examples/), including response modification, mocking, and blocking.

## Options

The main command surface is:

```
proxy-lab start <android|ios> [domains.yml] [options]
```

Common options:

| Option | Environment fallback | Effect |
| --- | --- | --- |
| `--port PORT` | `PORT` | Android mitmdump port. |
| `--avd AVD` | `AVD` | Android AVD to boot when none is running. |
| `--serial SERIAL` | `ANDROID_SERIAL` | Require a specific running Android emulator. |
| `--boot-timeout SECONDS` | `BOOT_TIMEOUT` | Android boot timeout. |
| `--udid UDID` | `UDID` | Select an iOS Simulator for discovery and CA trust. |
| `--script FILE` | `PROXY_LAB_SCRIPTS` | Add a mitmproxy addon; repeatable. |
| `--state-dir DIR` | `PROXY_LAB_STATE_DIR` | Store owner-scoped session state in `DIR`. |
| `--detach` | `DETACH` | Run in the background; return once the proxy is ready. |
| `--duration SECONDS` | `DURATION` | Stop automatically after this long. |
| `--log-format text\|jsonl` | `PROXY_LAB_LOG_FORMAT` | Traffic output format. Default `text`. |

`status`, `stop`, `doctor`, and `logs` additionally accept `--json`; `logs` accepts `--follow` and `--lines N`.

Further environment variables:

| Variable | Effect |
| --- | --- |
| `PROXY_LAB_CONFIG` | Domain list, equivalent to the positional `domains.yml`. |
| `PROXY_LAB_MITMDUMP` | Use a specific `mitmdump` binary instead of discovery. |
| `MITMPROXY_SPEC` | mitmproxy spec for the uv fallback. Defaults to `mitmproxy@latest`. |
| `PROXY_LAB_SKIP_UPDATE_CHECK` | `1` skips the PyPI version check. |

Environment variables remain supported for automation:

```bash
PORT=8081 AVD=Pixel_10a uvx proxy-lab start android
```

If you run this daily from a checkout, install the command once:

```bash
uv tool install .
proxy-lab start android
```

For the published release, use `uv tool install --force proxy-lab`.

## Lifecycle and diagnostics

The launcher records its owning process and the Android proxy value before changing the device. Lifecycle commands never kill an unknown process by name:

```bash
uvx proxy-lab status android
uvx proxy-lab stop android
uvx proxy-lab reset android
uvx proxy-lab doctor
```

`stop` signals only the recorded owner and restores the previous Android proxy value. `reset` is the explicit recovery command for stale state or proxy settings. `doctor` reports the same pre-flight inputs used by `start`—versions, tools, SDK paths, devices, CA, config, and state—without starting a proxy.

Use `uvx proxy-lab --version` to print the wrapper version.

## Exit codes

Failures are distinguishable without reading the message:

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Unspecified failure (`stop`/`reset` could not finish). |
| 2 | Invalid arguments or flags. |
| 3 | Invalid config, or a missing input file. |
| 4 | A required tool is not on `PATH`. |
| 5 | Device, emulator, or CA failure. |
| 6 | The requested port is already in use. |
| 7 | mitmproxy is missing, too old, or failed to start. |
| 8 | Session state conflict, or a detached run that never became ready. |

`doctor` exits 1 when any check fails, so it works as a CI gate.

## Run from a clone

Use a clone when you change the scripts or the addon:

```bash
git clone https://github.com/kibotu/proxy-lab.sh
cd proxy-lab.sh
./android/start-proxy.sh        # or ./ios/start-proxy.sh
```

The scripts are the same code that `uvx` runs. Edit [`domains.yaml`](domains.yaml) in place, or set `PROXY_LAB_CONFIG`. `PORT`, `AVD`, `SERIAL`, and `BOOT_TIMEOUT` affect Android only. Run the local checks with:

```bash
uv run python -m unittest discover -s tests -v
shellcheck android/start-proxy.sh ios/start-proxy.sh proxy_lab/common.sh proxy_lab/control.sh
```

## Requirements

- **macOS.** The iOS Simulator needs Xcode.
- **Python:** Python 3.9+ is used by the packaged CLI/configuration loader. `uvx` supplies it automatically.
- **mitmproxy:** Optional host `mitmdump`; otherwise the launchers request `mitmproxy@latest` through uv. See [Quickstart](#quickstart) for install commands.
- **[curl](https://curl.se/)** — optional; when available, preflight uses it to check whether a newer mitmproxy release is available. A failed or unavailable check is ignored.
- **Android:** `adb` and `emulator` on your `$PATH` (Android Studio's SDK provides both), plus a Google APIs AVD. Your debug build must trust user CAs, as shown in [the Android quickstart](#android).
- **iOS:** Xcode and a local-mode-capable mitmproxy (`10.1.5+`; see [macOS local capture](https://www.mitmproxy.org/posts/local-capture/macos/)). The fallback requests `mitmproxy@latest`.

The Android script also uses `openssl` and `lsof`, which macOS ships. Preflight reports missing tools with a PATH hint.

## Troubleshooting

The scripts fail loudly, and the error line usually contains the answer. These are the recurring ones:

- **`adbd cannot run as root in production builds`** — the AVD uses a Play Store image. Check with `grep image.sysdir ~/.android/avd/<AVD>.avd/config.ini` and create a Google APIs AVD instead.
- **Nothing shows up after `--detach`** — the traffic is on disk, not in your terminal: `uvx proxy-lab logs android`.
- **`net::ERR_CERT_AUTHORITY_INVALID`** — the app does not trust the CA. Confirm the [network security config](#android) is in the build you are running, and that it is a debug build. To reinstall the certificate: `adb root && adb shell rm /data/misc/user/0/cacerts-added/<hash>.0`, then run the script again. Restart the app afterwards, because a running process keeps its trust anchors.
- **Requests time out** — the proxy stopped while the emulator still points at it. Run `uvx proxy-lab status android`, then `uvx proxy-lab stop android` or `uvx proxy-lab reset android`. The launcher restores the exact proxy value that existed before the run; `reset` is the explicit recovery path for stale state.
- **"No internet connection" while proxied** — Android's connectivity check may report partial connectivity because it does not trust user CAs. App traffic may still work.
- **Nothing shows up on iOS** — make sure a simulator is running (from Xcode or Device Hub), the network extension was allowed, and the CA is trusted. Do not configure a system proxy.
- **No `[local_router]` lines** — the host is not in the domains file in use. See [Choose the domains to log](#choose-the-domains-to-log).

Still stuck? [Open an issue](https://github.com/kibotu/proxy-lab.sh/issues) with the exact error line.

## How it works

```
Android emulator ──▶ 10.0.2.2:$PORT ─┐
                                     ├──▶ mitmdump on the host ──▶ upstream via host DNS/hosts
iOS Simulator ──▶ macOS local capture ┘
```

`mitmdump` terminates TLS with its own CA, prints what it sees, and forwards the request. On Android, `10.0.2.2` is the host address as the emulator sees it ([emulator networking](https://developer.android.com/studio/run/emulator-networking)). On iOS, mitmproxy's signed network extension selects the `Simulator` process and feeds it to local capture; there is no system proxy or client-side proxy port. Name resolution happens on the host, so an `/etc/hosts` entry can point a development domain at a local server. `local_router.py` only logs matching requests.

For local debugging, both launchers set `ssl_insecure=true`, which disables upstream certificate verification. Remove that option when testing upstream certificate validation.

`local_router.py` is a [mitmproxy addon](https://docs.mitmproxy.org/stable/addons/overview/) that logs matching hosts. It does not route traffic. Both platforms load it.

## Why Android needs this

Since Android 7, apps that target API 24 and higher ignore user-installed CAs unless they opt in ([Android Developers Blog](https://android-developers.googleblog.com/2016/07/changes-to-trusted-certificate.html)). The common answer is to put the CA in the *system* store. That answer keeps getting more expensive:

- The [mitmproxy guide](https://docs.mitmproxy.org/stable/howto/install-system-trusted-ca-android/) hashes the certificate by hand, remounts `/system`, and needs `-writable-system` on every boot.
- Android 14 moved the store into the immutable Conscrypt APEX ([AOSP](https://source.android.com/docs/core/ota/modular-system/conscrypt)). That mount is private per process, so even root edits stay invisible to apps ([HTTP Toolkit](https://httptoolkit.com/blog/android-14-breaks-system-certificate-installation/)). The known workarounds are a Magisk module, or `nsenter` into Zygote's mount namespace.

proxy-lab.sh takes the other door: your debug build opts into the **user** store, and the script installs the CA there (`/data/misc/user/0/cacerts-added/`). The first install uses `adb root` and reboots the AVD; the script may call `adb root` again afterward. This flow is intended for debug builds and does not change release trust policy.

The second half of the problem is routing. The emulator must point at `10.0.2.2`, not `localhost`, through a setting that goes stale in silence. The script writes that setting after the port check succeeds, and its cleanup path clears it on normal shutdown.

The Android emulator is a guest, not a Mac process, so macOS local capture does not replace this flow. The [current mitmproxy documentation](https://docs.mitmproxy.org/stable/concepts/modes/) still treats Android proxying and CA setup as separate concerns. [WireGuard mode](https://docs.mitmproxy.org/stable/concepts/modes/#wireguard) avoids the explicit proxy setting, but requires a WireGuard client/configuration and does not remove the CA-trust requirement.

What you get for the Android run:

| Step | Behaviour |
| --- | --- |
| Tools | Checks `adb`, `openssl`, `lsof` and the addon, then selects a host `mitmdump` or warms uv's `mitmproxy@latest` fallback. It logs the resolved version and, when available, a newer PyPI release. |
| Host CA | Generates `~/.mitmproxy/` on the first run. |
| Emulator | Reuses a running emulator, or boots one and waits for it. |
| Device CA | Installs the certificate if missing. The first install reboots once per AVD. If `chmod`/`restorecon` fails, the partial file is removed; later verification failures are reported. |
| Port | Refuses any existing listener; it never kills a process it cannot prove it owns. |
| Proxy setting | Writes `10.0.2.2:$PORT` after the port check and restores the previous value during cleanup. |

Repeated runs reuse the existing CA and emulator state.

## Scope and alternatives

Out of scope, on purpose:

- **Physical devices.** Emulator and simulator only.
- **Release builds.** They do not trust user CAs, and that is correct.
- **Certificate pinning.** A pinning app rejects the proxy CA. Turn pinning off in debug builds, or use a pin bypass.
- **Response rewriting and mocking.** mitmproxy does all of that. Pass trusted addons with `--script`; see [Addons and examples](#addons-and-examples).

For a GUI or broader device support, use [HTTP Toolkit](https://httptoolkit.com/), [Proxyman](https://proxyman.io/), or [Charles](https://www.charlesproxy.com/). This project stays small, scriptable, and reviewable.

## Versions and releases

- **mitmproxy** uses the host `mitmdump` when available. Otherwise the scripts request the intentionally unpinned `mitmproxy@latest` through uv. Preflight logs the resolved version and, when `curl` is available, queries PyPI for a newer release.
- **proxy-lab.sh** keeps the package version in `pyproject.toml`; `proxy-lab --version` reads installed package metadata. The release workflow refuses to publish a tag that disagrees with that version.
- Tags are `X.Y.Z`, with no `v` prefix. A matching tag builds the wheel and sdist and publishes both a [GitHub Release](https://github.com/kibotu/proxy-lab.sh/releases) and the same artifacts to [PyPI](https://pypi.org/project/proxy-lab/). [CHANGELOG.md](CHANGELOG.md) has the per-version detail.

## Project layout

| Path | What it is |
| --- | --- |
| [`android/start-proxy.sh`](android/start-proxy.sh) | The full Android flow: checks, CA, emulator, port, proxy state, and mitmdump. |
| [`ios/start-proxy.sh`](ios/start-proxy.sh) | mitmdump in macOS local-capture mode with automatic Simulator CA trust. |
| [`proxy_lab/common.sh`](proxy_lab/common.sh) | Shared pre-flight, mitmproxy, configuration, and session-state helpers. |
| [`proxy_lab/control.sh`](proxy_lab/control.sh) | Owner-scoped `status`, `stop`, `reset`, and `doctor` commands. |
| [`local_router.py`](local_router.py) | Strict mitmproxy domain-filter addon; text or JSONL output. |
| [`proxy_lab/config.py`](proxy_lab/config.py) | Pure configuration loading, matching, and URL redaction helpers. |
| [`proxy_lab/cli.py`](proxy_lab/cli.py) | The `proxy-lab` entry point for `uvx`. |
| [`examples/`](examples/) | Generic mitmproxy addon examples. |
| [`tests/`](tests/) | Unit and fake-tool integration tests. |
| [`domains.yaml`](domains.yaml) | Default host list. |
| [`AGENTS.md`](AGENTS.md) | Operational contract for coding agents. |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Shellcheck, unit tests, package smoke tests, and proxy smoke tests on macOS and Ubuntu. |

## Contributing

Issues and pull requests are welcome, in particular real failure modes the pre-flight checks miss.

Run the unit and fake-tool tests plus [shellcheck](https://www.shellcheck.net/) before pushing:

```bash
uv run python -m unittest discover -s tests -v
shellcheck android/start-proxy.sh ios/start-proxy.sh proxy_lab/common.sh proxy_lab/control.sh
```

Add notable changes to [CHANGELOG.md](CHANGELOG.md) under `Unreleased`. Release tags must match the version in `pyproject.toml`.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Support

If proxy-lab.sh saved you an afternoon, or one `ERR_CERT_AUTHORITY_INVALID` hunt, consider [buying me a coffee](https://buymeacoffee.com/kibotu).
