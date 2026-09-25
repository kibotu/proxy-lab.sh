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
- [Choose the domains to log](#choose-the-domains-to-log)
- [Options](#options)
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

## Quickstart

Install [uv](https://docs.astral.sh/uv/) to run the commands below. The scripts prefer a host-installed `mitmdump`; without one, they use uv to resolve `mitmproxy@latest`.

```bash
brew install uv

# optional: use a host binary instead of the uv fallback
brew install --cask mitmproxy
```

Then follow the path for your platform. If no host `mitmdump` is installed, the first run resolves the latest compatible mitmproxy release, downloading it when needed. Later runs are usually quick.

### Android

**1. Let your debug build trust user-installed certificates.** Most apps do not trust user-installed CAs by default. For a debug build, opt in with `res/xml/network_security_config.xml`:

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

**4. Press `Ctrl-C`.** The proxy stops and its cleanup path clears the emulator's proxy setting. The emulator keeps running.

> Use a **Google APIs** AVD image. "Google APIs Play Store" images refuse `adb root`, and the CA install needs it.

### iOS

**1. Start a simulator.** Launch it in Xcode or Device Hub. The launcher does not boot one.

**2. Start the proxy:**

```bash
uvx proxy-lab start ios
```

**3. Allow mitmproxy's network extension.** The launcher requests local capture with the `Simulator` process filter. It is intended to cover simulators launched from Xcode or Device Hub. If macOS has not approved the redirector yet, approve the prompt. Local capture is outbound-only; no system or app proxy setting is required. If you previously configured a manual system proxy, turn it off so it does not duplicate the capture path.

**4. Trust the CA, one time per simulator:**

1. Open [`mitm.it`](http://mitm.it) in the simulator's Safari and download the profile. The page is served through local capture, so extension approval must work first.
2. Install it: **Settings → General → VPN & Device Management**.
3. Turn on full trust: **Settings → General → About → Certificate Trust Settings**.

**5. Start your app.** Press `Ctrl-C` to stop the proxy.

No root or reboot is required. HTTPS interception still requires the simulator to trust the CA.

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

Without an argument you get the [bundled `domains.yaml`](domains.yaml), which lists `.example.com` only. Keep a custom file with the project if the team should share the same filter.

## Options

The command surface is one line:

```
proxy-lab start <android|ios> [domains.yml]
```

Environment variables cover the rest:

| Variable | Default | Effect |
| --- | --- | --- |
| `PORT` | `8080` | Android mitmdump port. The emulator points at `10.0.2.2:$PORT`; iOS local capture has no proxy port. |
| `AVD` | first entry of `emulator -list-avds` | AVD to boot when none is running. Android only. |
| `BOOT_TIMEOUT` | `240` | Seconds to wait for the emulator to finish booting. Android only. |
| `PROXY_LAB_CONFIG` | bundled `domains.yaml` | Path to your domains file. Same effect as the argument above. |

```bash
PORT=8081 AVD=Pixel_10a uvx proxy-lab start android
```

If you run this daily from a checkout, install the command once:

```bash
uv tool install .
proxy-lab start android
```

For the published release, use `uv tool install --force proxy-lab`.

## Run from a clone

Use a clone when you change the scripts or the addon:

```bash
git clone https://github.com/kibotu/proxy-lab.sh
cd proxy-lab.sh
./android/start-proxy.sh        # or ./ios/start-proxy.sh
```

The scripts are the same code that `uvx` runs. Edit [`domains.yaml`](domains.yaml) in place, or set `PROXY_LAB_CONFIG`. `PORT`, `AVD`, and `BOOT_TIMEOUT` affect Android only.

## Requirements

- **macOS.** The iOS Simulator needs Xcode.
- **mitmproxy:** Optional host `mitmdump`; otherwise the launchers request `mitmproxy@latest` through uv. See [Quickstart](#quickstart) for install commands.
- **[curl](https://curl.se/)** — optional; when available, preflight uses it to check whether a newer mitmproxy release is available. A failed or unavailable check is ignored.
- **Android:** `adb` and `emulator` on your `$PATH` (Android Studio's SDK provides both), plus a Google APIs AVD. Your debug build must trust user CAs, as shown in [the Android quickstart](#android).
- **iOS:** Xcode and a local-mode-capable mitmproxy (`10.1.5+`; see [macOS local capture](https://www.mitmproxy.org/posts/local-capture/macos/)). The fallback requests `mitmproxy@latest`.

The Android script also uses `openssl` and `lsof`, which macOS ships. Preflight reports missing tools with a PATH hint.

## Troubleshooting

The scripts fail loudly, and the error line usually contains the answer. These are the recurring ones:

- **`adbd cannot run as root in production builds`** — the AVD uses a Play Store image. Check with `grep image.sysdir ~/.android/avd/<AVD>.avd/config.ini` and create a Google APIs AVD instead.
- **`net::ERR_CERT_AUTHORITY_INVALID`** — the app does not trust the CA. Confirm the [network security config](#android) is in the build you are running, and that it is a debug build. To reinstall the certificate: `adb root && adb shell rm /data/misc/user/0/cacerts-added/<hash>.0`, then run the script again. Restart the app afterwards, because a running process keeps its trust anchors.
- **Requests time out** — the proxy stopped while the emulator still points at it. `adb shell settings get global http_proxy` prints `10.0.2.2:$PORT` while the script runs, and `null` after a clean exit (`$PORT` defaults to `8080`). If it prints an address and nothing listens, start the script again, or clear it with `adb shell settings delete global http_proxy`.
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
| Port | Refuses a non-mitmdump listener and stops commands whose arguments contain `mitmdump`; it does not prove ownership. |
| Proxy setting | Writes `10.0.2.2:$PORT` after the port check. Clears it during normal cleanup. |

Repeated runs reuse the existing CA and emulator state.

## Scope and alternatives

Out of scope, on purpose:

- **Physical devices.** Emulator and simulator only.
- **Release builds.** They do not trust user CAs, and that is correct.
- **Certificate pinning.** A pinning app rejects the proxy CA. Turn pinning off in debug builds, or use a pin bypass.
- **Response rewriting and mocking.** mitmproxy does all of that. Write your own [addon](https://docs.mitmproxy.org/stable/addons/overview/) next to `local_router.py`.

For a GUI or broader device support, use [HTTP Toolkit](https://httptoolkit.com/), [Proxyman](https://proxyman.io/), or [Charles](https://www.charlesproxy.com/). This project stays small, scriptable, and reviewable.

## Versions and releases

- **mitmproxy** uses the host `mitmdump` when available. Otherwise the scripts request the intentionally unpinned `mitmproxy@latest` through uv. Preflight logs the resolved version and, when `curl` is available, queries PyPI for a newer release.
- **proxy-lab.sh** resolves the published package by name. Pin the wrapper yourself when the team needs reproducibility; pinning it does not pin mitmproxy, so put the command in your project README or Makefile.
- Tags are `X.Y.Z`, with no `v` prefix. A tag push builds the wheel and sdist at that version and publishes both a [GitHub Release](https://github.com/kibotu/proxy-lab.sh/releases) and the same artifacts to [PyPI](https://pypi.org/project/proxy-lab/). [CHANGELOG.md](CHANGELOG.md) has the per-version detail.

## Project layout

| Path | What it is |
| --- | --- |
| [`android/start-proxy.sh`](android/start-proxy.sh) | The full Android flow: checks, CA, emulator, port, proxy setting, mitmdump. |
| [`ios/start-proxy.sh`](ios/start-proxy.sh) | mitmdump in macOS local-capture mode with the shared addon. |
| [`local_router.py`](local_router.py) | mitmproxy addon. Logs hosts from the domains file. |
| [`domains.yaml`](domains.yaml) | Default host list. |
| [`proxy_lab/cli.py`](proxy_lab/cli.py) | The `proxy-lab` entry point for `uvx`. Dispatches to the scripts. |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Shellcheck, iOS argument validation, and proxy smoke tests on macOS and Ubuntu. |

## Contributing

Issues and pull requests are welcome, in particular real failure modes the pre-flight checks miss.

Run [shellcheck](https://www.shellcheck.net/) on the scripts before you push, because CI does. Add notable changes to [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Support

If proxy-lab.sh saved you an afternoon, or one `ERR_CERT_AUTHORITY_INVALID` hunt, consider [buying me a coffee](https://buymeacoffee.com/kibotu).
