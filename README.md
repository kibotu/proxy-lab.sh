# proxy-lab.sh

[![CI](https://github.com/kibotu/proxy-lab.sh/actions/workflows/ci.yml/badge.svg)](https://github.com/kibotu/proxy-lab.sh/actions/workflows/ci.yml)
[![Release](https://github.com/kibotu/proxy-lab.sh/actions/workflows/release.yml/badge.svg)](https://github.com/kibotu/proxy-lab.sh/actions/workflows/release.yml)
[![GitHub Release](https://img.shields.io/github/v/release/kibotu/proxy-lab.sh)](https://github.com/kibotu/proxy-lab.sh/releases)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)](#requirements)
[![Shell](https://img.shields.io/badge/shell-bash-informational)](#layout)

One command to see your app's HTTPS traffic: a [mitmproxy](https://www.mitmproxy.org/) setup for the **Android emulator** and the **iOS simulator**. No certificate plumbing, no stale proxy settings, no guesswork.

```
./android/start-proxy.sh    # checks → CA → proxy setting → mitmdump
./ios/start-proxy.sh        # shares the host network, no device setup
```

![proxy-lab.sh terminal output showing intercepted HTTPS requests](docs/teaser.jpeg)

## Contents

- [Quickstart](#quickstart)
- [Why this repo](#why-this-repo)
- [Requirements](#requirements)
- [How it works](#how-it-works)
- [Usage](#usage)
- [How trust works](#how-trust-works)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Quickstart

1. Install [uv](https://docs.astral.sh/uv/): `brew install uv`
2. Add your API domains to a domains file:
   ```yaml
   domains:
     - ".example.com"
   ```
   Cloning the repo? Edit [`domains.yaml`](domains.yaml) in place. Using `uvx` below with no clone? Save this as your own file and pass its path as shown in step 3.
3. Run the script for your platform:
   ```
   ./android/start-proxy.sh   # Android emulator
   ./ios/start-proxy.sh       # iOS simulator
   ```
   Or run it straight from GitHub, no clone needed:
   ```
   uvx --from git+https://github.com/kibotu/proxy-lab.sh proxy-lab start android
   uvx --from git+https://github.com/kibotu/proxy-lab.sh proxy-lab start ios mydomains.yml
   ```
   Pin a released version with `@X.Y.Z` (see [Releases](#releases)):
   ```
   uvx --from git+https://github.com/kibotu/proxy-lab.sh@1.0.0 proxy-lab start ios
   ```
4. Build and run your debug app. Traffic to your domains shows up in the terminal.
5. Stop with `Ctrl-C`.

That's it. See [Requirements](#requirements) if step 3 stops you.

## Why this repo

An app dev just wants to see their app's traffic, not spend a day on certificate plumbing. On the Android emulator, mitmproxy doesn't work out of the box. Two separate things need to be wired up by hand before the first HTTPS request shows up in your terminal:

- **Trust.** Since Android 7, apps ignore user-installed CAs. The [official mitmproxy guide](https://docs.mitmproxy.org/stable/howto/install-system-trusted-ca-android/) puts the CA into the read-only *system* store instead: hash the certificate by hand, remount `/system`, disable verified boot, reboot — or build a Magisk module for Google Play images. Android 14+ moved that store into an immutable APEX, so any boot without `-writable-system` loads a clean image anyway.
- **Routing.** The emulator must point at the host — `10.0.2.2:8080`, not `localhost` — through a manual settings command that goes stale without telling anyone.

Miss one step and the error tells you nothing useful: `ERR_CERT_AUTHORITY_INVALID`, or requests that quietly time out.

One script per platform replaces the checklist. Android gets the full treatment, iOS a thin wrapper:

- **The whole sequence** — checks → CA → proxy setting → `mitmdump`. Stop with `Ctrl-C`.
- **Pre-flight checks, not stack traces** — missing tools, the wrong AVD image, a busy port, missing files: all caught before they cost you an afternoon.
- **Certificate, once** — the CA goes into the *user* trust store: no remount, no Magisk, no `-writable-system`. One reboot, once per AVD (see [How trust works](#how-trust-works)).
- **Idempotent** — re-run any time. A stale proxy from a `kill -9` is cleaned up on the next Android run.
- **Reproducible** — mitmproxy runs at a version pinned per run, so the whole team sees the same behaviour. Pinning the scripts themselves across a team is a separate, coarser lever — see [Releases](#releases).
- **One config, both platforms** — your domains live in one file.

## Requirements

> **macOS only.** The iOS Simulator requires Xcode, which runs on macOS. If you only target Android, Linux likely works too, but it isn't tested.

- **[uv](https://docs.astral.sh/uv/)** — runs mitmproxy at a pinned version, no global Python install needed: `brew install uv`
- **Android:** [Android Studio](https://developer.android.com/studio) with `adb` and `emulator` on `$PATH`, and a **Google APIs** AVD. Play Store images refuse `adb root`, which the one-time CA install needs.
- **iOS:** just Xcode. The simulator shares the host's network stack, so there's no device-side setup.

Your debug build must trust user-installed CAs. That means a [network security config](https://developer.android.com/privacy-and-security/security-config) with `<debug-overrides>` trusting `user`. See [How trust works](#how-trust-works) if this is new to you.

> **Disclaimer:** these scripts install the CA on the device. They can't make your app trust it — that's your app's job, and it's outside this repo's scope. `res/xml/network_security_config.xml`:
> ```xml
> <?xml version="1.0" encoding="utf-8"?>
> <network-security-config>
>     <debug-overrides>
>         <trust-anchors>
>             <certificates src="user" />
>         </trust-anchors>
>     </debug-overrides>
> </network-security-config>
> ```
> Reference it from `AndroidManifest.xml`, on the `<application>` tag, **debug variant only**:
> ```xml
> <application
>     android:networkSecurityConfig="@xml/network_security_config"
>     ... >
> ```
> Never ship `<debug-overrides>` in a release build — see [How trust works](#how-trust-works) for why.

## How it works

```
Emulator → 10.0.2.2:8080 → mitmdump on the host → DNS → 127.0.0.1:443 (your dev server)
```

`mitmdump` intercepts HTTPS traffic. `10.0.2.2` is how the emulator sees the host machine. The proxy resolves your dev domains on the host (for example, `/etc/hosts` → `127.0.0.1`), so routing is plain DNS — the `local_router.py` addon only logs matching requests for visibility.

The simulator needs none of this. `127.0.0.1` already reaches your local server directly through the proxy.

## Usage

### Android

```
./android/start-proxy.sh
```

The script boots the first AVD if none is running, and reuses one if it is. Stop with `Ctrl-C`: the proxy exits and the device's proxy setting is cleared. The emulator itself keeps running.

Override the defaults with environment variables:

```
AVD=Pixel_10a PORT=8081 ./android/start-proxy.sh
```

Failures print one line plus a concrete fix: an install command, a `PATH` export, or a doc link. The script never touches processes it doesn't own — a stale `mitmdump` on the port gets stopped, anything else fails with a hint (`PORT=<other>`).

#### What the script checks

| Check | Behaviour |
| --- | --- |
| Tools | `adb`, `uv`, `openssl`, `lsof`, and the addon file — with install hints; warms mitmproxy via `uv` |
| Host CA | Generates `~/.mitmproxy/` on first run |
| Emulator | Reuses a running one, boots one if not, uses `adb root` to push the CA |
| Device CA | Installs into the user trust store if missing — one reboot, once per AVD |
| Port | Cleans up stale proxies from earlier runs, refuses to touch foreign processes |
| Proxy setting | Writes `10.0.2.2:8080` only once the port is confirmed ours; cleared on exit |

### iOS

```
./ios/start-proxy.sh          # PORT=8080, Ctrl-C to stop
```

One-time trust setup for HTTPS:

1. Open **`mitm.it`** in the simulator's Safari.
2. Install the profile: **Settings → General → VPN & Device Management**.
3. Enable full trust: **Settings → General → About → Certificate Trust Settings**.

No root, no reboot.

## How trust works

Android 14+ reads system CAs from an immutable APEX. Pushing certs into `/system/etc/security/cacerts` no longer works. These scripts put the CA into the **user** trust store instead (`/data/misc/user/0/cacerts-added/`), which debug builds trust through `<debug-overrides>`. It needs root once and one reboot, then it survives reboots.

Release builds don't trust user CAs. These scripts only intercept debug builds — that's by design, not a limitation.

## Configuration

`local_router.py`, at the repo root, is loaded by both scripts. It reads `domains.yaml` next to it. Add your domains once, and both platforms pick them up:

```yaml
domains:
  - ".example.com"
```

Matching requests log as `[local_router] …` in the proxy output. mitmproxy's addon API is Python, but `uv` runs it for you — you never touch an interpreter directly.

Run via `uvx` and there is no checkout to edit: pass your domains file as an argument (`proxy-lab start ios mydomains.yml`). Without one, the packaged `domains.yaml` applies — which only lists `.example.com`, so keep a local copy of your own.

## Troubleshooting

The script's own error messages cover most failures. The recurring ones:

- **`adbd cannot run as root in production builds`** — your AVD uses a Google Play image. Switch to Google APIs: `grep image.sysdir ~/.android/avd/<AVD>.avd/config.ini`.
- **`net::ERR_CERT_AUTHORITY_INVALID`** — the CA is missing from the user store, or you're intercepting a release build. Force a reinstall: `adb root && adb shell rm /data/misc/user/0/cacerts-added/<hash>.0`, re-run the script (one reboot), then restart the app — a running process doesn't reload trust anchors.
- **Pages time out** — is the script still running? `adb shell settings get global http_proxy` should return `10.0.2.2:8080`. `null` means the script stopped and traffic is going direct, as intended.
- **"No internet connection" banner while proxied** — Android's connectivity probe doesn't trust user CAs, so the OS marks the network as "partial connectivity". App traffic still works. Safe to ignore.
- **`[local_router]` lines missing from the log** — the domain isn't in the domains file in use (`domains.yaml`, or the one you passed to `proxy-lab start`).

Still stuck? [Open an issue](https://github.com/kibotu/proxy-lab.sh/issues) with the exact error line — the scripts are meant to fail loudly, so that line usually has the answer.
what changed per version. The [Releases page](https://github.com/kibotu/proxy-lab.sh/releases) has the tags and diffs. Both are built by the CI and release workflows above.

## Contributing

Issues and pull requests are welcome — especially real-world failure modes the pre-flight checks don't catch yet. Add notable changes to [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.

## Support

If proxy-lab.sh saved you a few hours, or a few `ERR_CERT_AUTHORITY_INVALID` hunts, consider [buying me a coffee](https://buymeacoffee.com/kibotu).
