# proxy-lab.sh

[![CI](https://github.com/kibotu/proxy-lab.sh/actions/workflows/ci.yml/badge.svg)](https://github.com/kibotu/proxy-lab.sh/actions/workflows/ci.yml)
[![Release](https://github.com/kibotu/proxy-lab.sh/actions/workflows/release.yml/badge.svg)](https://github.com/kibotu/proxy-lab.sh/actions/workflows/release.yml)
[![GitHub Release](https://img.shields.io/github/v/release/kibotu/proxy-lab.sh)](https://github.com/kibotu/proxy-lab.sh/releases)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)](#requirements)
[![Shell](https://img.shields.io/badge/shell-bash-informational)](#project-layout)

**See your app's HTTPS traffic with one command.** proxy-lab.sh starts [mitmproxy](https://www.mitmproxy.org/) for the **Android emulator** and the **iOS simulator**, and it does the certificate work for you. No `/system` remount, no Magisk, no stale proxy setting.

```bash
# iOS
uvx --from git+https://github.com/kibotu/proxy-lab.sh proxy-lab start ios domains.yml

# android
uvx --from git+https://github.com/kibotu/proxy-lab.sh proxy-lab start android domains.yml
```

![proxy-lab.sh terminal output showing intercepted HTTPS requests](docs/teaser.jpeg)

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

Install [uv](https://docs.astral.sh/uv/). It runs mitmproxy at a pinned version for you, so there is no Python setup and nothing global to install:

```bash
brew install uv
```

Then follow the path for your platform. The first run takes a few minutes, because it downloads mitmproxy and prepares the device. Later runs start in seconds.

### Android

**1. Let your debug build trust user-installed certificates.** Android apps ignore them by default, so your app must opt in. Add `res/xml/network_security_config.xml` to your **debug** source set:

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

`<debug-overrides>` applies only when the build is debuggable, so it cannot weaken a release build. Keep it in the debug source set anyway. See the [network security config docs](https://developer.android.com/privacy-and-security/security-config) and [why this is necessary](#why-android-needs-this).

**2. Start the proxy:**

```bash
uvx --from git+https://github.com/kibotu/proxy-lab.sh@ proxy-lab start android
```

The script checks your tools, reuses a running emulator or boots one, installs the mitmproxy CA into the user trust store, and sets the emulator proxy to `10.0.2.2:8080`. The CA install reboots the emulator one time per AVD.

**3. Start your debug build.** Requests print in the terminal as they happen.

**4. Press `Ctrl-C`.** The proxy stops and the emulator's proxy setting is cleared. The emulator keeps running.

> Use a **Google APIs** AVD image. "Google APIs Play Store" images refuse `adb root`, and the CA install needs it.

### iOS

**1. Start the proxy:**

```bash
uvx --from git+https://github.com/kibotu/proxy-lab.sh@ proxy-lab start ios
```

**2. Send the simulator's traffic through it.** The simulator uses your Mac's network stack, so it has no proxy setting of its own. Pick one:

- **System proxy** — **System Settings → Network → (your interface) → Details → Proxies**. Turn on *Web proxy* and *Secure web proxy*, both `127.0.0.1` port `8080`. Every app on the Mac goes through the proxy while this is on.
- **App only** — point your debug build at `localhost:8080`, for example with `URLSessionConfiguration.connectionProxyDictionary`.

**3. Trust the CA, one time per simulator:**

1. Open [`mitm.it`](http://mitm.it) in the simulator's Safari and download the profile. The page is served by the proxy, so step 2 must work first.
2. Install it: **Settings → General → VPN & Device Management**.
3. Turn on full trust: **Settings → General → About → Certificate Trust Settings**.

**4. Start your app.** Press `Ctrl-C` to stop the proxy.

No root, no reboot, nothing to undo in the app.

## Choose the domains to log

Every request goes through the proxy and appears in the mitmdump output. On top of that, hosts you list get a `[local_router]` line, which makes your own API easy to find in a busy log.

Write your list in a YAML file:

```yaml
domains:
   - ".example.com"   # subdomains only: api.example.com yes, example.com no
   - "acme.dev"       # the host itself and its subdomains
```

Entries match the end of the host name. A leading dot excludes the apex domain.

Pass the file as the last argument:

```bash
uvx --from git+https://github.com/kibotu/proxy-lab.sh@ proxy-lab start android my-domains.yml
```

Without an argument you get the [bundled `domains.yaml`](domains.yaml), which lists `.example.com` only. Keep your own file next to your project and commit it, so the team logs the same hosts.

## Options

The command surface is one line:

```
proxy-lab start <android|ios> [domains.yml]
```

Environment variables cover the rest:

| Variable | Default | Effect |
| --- | --- | --- |
| `PORT` | `8080` | Port for mitmdump on the host. Android points the emulator at `10.0.2.2:$PORT`. |
| `AVD` | first entry of `emulator -list-avds` | AVD to boot when none is running. Android only. |
| `BOOT_TIMEOUT` | `240` | Seconds to wait for the emulator to finish booting. Android only. |
| `PROXY_LAB_CONFIG` | bundled `domains.yaml` | Path to your domains file. Same effect as the argument above. |

```bash
PORT=8081 AVD=Pixel_10a uvx --from git+https://github.com/kibotu/proxy-lab.sh@ proxy-lab start android
```

If you run this daily, install the command once and keep the line short:

```bash
uv tool install git+https://github.com/kibotu/proxy-lab.sh@
proxy-lab start android
```

Move to a newer version with `uv tool install --force git+https://github.com/kibotu/proxy-lab.sh@<X.Y.Z>`.

## Run from a clone

Use a clone when you change the scripts or the addon:

```bash
git clone https://github.com/kibotu/proxy-lab.sh
cd proxy-lab.sh
./android/start-proxy.sh        # or ./ios/start-proxy.sh
```

The scripts are the same code that `uvx` runs. Edit [`domains.yaml`](domains.yaml) in place, or set `PROXY_LAB_CONFIG`. All environment variables above apply.

## Requirements

- **macOS.** The iOS Simulator needs Xcode, and Xcode needs macOS. The Android script uses portable tools only, so Linux probably works, but nobody tests it there.
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — `brew install uv`. It runs [mitmproxy](https://www.mitmproxy.org/) at a pinned version. No Python install of your own is necessary.
- **Android:** [Android Studio](https://developer.android.com/studio) with `adb` and `emulator` on your `$PATH`, plus a Google APIs AVD. Your debug build must trust user CAs, as shown in [the Android quickstart](#android).
- **iOS:** Xcode.

The Android script also uses `openssl` and `lsof`, which macOS ships. It tells you if something is missing, and it prints the command that fixes it.

## Troubleshooting

The scripts fail loudly, and the error line usually contains the answer. These are the recurring ones:

- **`adbd cannot run as root in production builds`** — the AVD uses a Play Store image. Check with `grep image.sysdir ~/.android/avd/<AVD>.avd/config.ini` and create a Google APIs AVD instead.
- **`net::ERR_CERT_AUTHORITY_INVALID`** — the app does not trust the CA. Confirm the [network security config](#android) is in the build you are running, and that it is a debug build. To reinstall the certificate: `adb root && adb shell rm /data/misc/user/0/cacerts-added/<hash>.0`, then run the script again. Restart the app afterwards, because a running process keeps its trust anchors.
- **Requests time out** — the proxy stopped while the emulator still points at it. `adb shell settings get global http_proxy` prints `10.0.2.2:8080` when the script runs, and `null` after a clean exit. If it prints an address and nothing listens, start the script again, or clear it with `adb shell settings delete global http_proxy`.
- **"No internet connection" while proxied** — Android's connectivity check does not trust user CAs, so the system reports partial connectivity. Your app traffic works. Ignore it.
- **Nothing shows up on iOS** — the simulator does not use the proxy. Go back to [step 2 of the iOS quickstart](#ios).
- **No `[local_router]` lines** — the host is not in the domains file in use. See [Choose the domains to log](#choose-the-domains-to-log).

Still stuck? [Open an issue](https://github.com/kibotu/proxy-lab.sh/issues) with the exact error line.

## How it works

```
Android emulator ──▶ 10.0.2.2:$PORT ─┐
                                     ├──▶ mitmdump on the host ──▶ upstream, or 127.0.0.1 for local dev domains
iOS simulator ─────▶ 127.0.0.1:$PORT ┘
```

`mitmdump` terminates TLS with its own CA, prints what it sees, and forwards the request. `10.0.2.2` is the host address as the emulator sees it ([emulator networking](https://developer.android.com/studio/run/emulator-networking)). Name resolution happens on the host, so an `/etc/hosts` entry sends a dev domain to a server on your machine.

`local_router.py` is a [mitmproxy addon](https://docs.mitmproxy.org/stable/addons/overview/). The name promises more than it delivers: it logs matching hosts, it does not route. Both platforms load it.

## Why Android needs this

Since Android 7, apps that target API 24 and higher ignore user-installed CAs unless they opt in ([Android Developers Blog](https://android-developers.googleblog.com/2016/07/changes-to-trusted-certificate.html)). The common answer is to put the CA in the *system* store. That answer keeps getting more expensive:

- The [mitmproxy guide](https://docs.mitmproxy.org/stable/howto/install-system-trusted-ca-android/) hashes the certificate by hand, remounts `/system`, and needs `-writable-system` on every boot.
- Android 14 moved the store into the immutable Conscrypt APEX ([AOSP](https://source.android.com/docs/core/ota/modular-system/conscrypt)). That mount is private per process, so even root edits stay invisible to apps ([HTTP Toolkit](https://httptoolkit.com/blog/android-14-breaks-system-certificate-installation/)). The known workarounds are a Magisk module, or `nsenter` into Zygote's mount namespace.

proxy-lab.sh takes the other door: your debug build opts into the **user** store, and the script installs the CA there (`/data/misc/user/0/cacerts-added/`). That needs `adb root` once and one reboot per AVD. The certificate survives later reboots. Release builds are unaffected.

The second half of the problem is routing. The emulator must point at `10.0.2.2`, not `localhost`, through a setting that goes stale in silence. The script writes that setting after it owns the port, and clears it on exit.

iOS has neither problem. The iOS side is a thin mitmdump wrapper, and this repo will not pretend otherwise.

What you get for the Android run:

| Step | Behaviour |
| --- | --- |
| Tools | Checks `adb`, `uv`, `openssl`, `lsof` and the addon, with install hints. Warms the mitmproxy download. |
| Host CA | Generates `~/.mitmproxy/` on the first run. |
| Emulator | Reuses a running emulator, or boots one and waits for it. |
| Device CA | Installs the certificate if it is missing. One reboot, one time per AVD. Rolls back a failed install. |
| Port | Stops stale proxies from earlier runs. Refuses to touch a process it does not own. |
| Proxy setting | Writes `10.0.2.2:$PORT` after the port is confirmed. Clears it on exit. |

Re-run it as often as you want. The steps are idempotent.

## Scope and alternatives

Out of scope, on purpose:

- **Physical devices.** Emulator and simulator only.
- **Release builds.** They do not trust user CAs, and that is correct.
- **Certificate pinning.** A pinning app rejects the proxy CA. Turn pinning off in debug builds, or use a pin bypass.
- **Response rewriting and mocking.** mitmproxy does all of that. Write your own [addon](https://docs.mitmproxy.org/stable/addons/overview/) next to `local_router.py`.

If you need those, or a GUI, look at [HTTP Toolkit](https://httptoolkit.com/), [Proxyman](https://proxyman.io/), or [Charles](https://www.charlesproxy.com/). proxy-lab.sh stays a small, scriptable, reviewable pile of bash instead.

## Versions and releases

- **mitmproxy** is pinned to `12.2.3` inside the scripts, so the whole team sees the same behaviour.
- **proxy-lab.sh** is pinned by you: `@1.0.0` in the `uvx` command. Without a tag you get `main`. Put the pinned command in your project README or a Makefile, and the team runs one version.
- Tags are `X.Y.Z`, with no `v` prefix. A tag push builds the wheel and sdist at that version and publishes a [GitHub Release](https://github.com/kibotu/proxy-lab.sh/releases). [CHANGELOG.md](CHANGELOG.md) has the per-version detail.

## Project layout

| Path | What it is |
| --- | --- |
| [`android/start-proxy.sh`](android/start-proxy.sh) | The full Android flow: checks, CA, emulator, port, proxy setting, mitmdump. |
| [`ios/start-proxy.sh`](ios/start-proxy.sh) | mitmdump with the shared addon. |
| [`local_router.py`](local_router.py) | mitmproxy addon. Logs hosts from the domains file. |
| [`domains.yaml`](domains.yaml) | Default host list. |
| [`proxy_lab/cli.py`](proxy_lab/cli.py) | The `proxy-lab` entry point for `uvx`. Dispatches to the scripts. |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | shellcheck, plus a proxy smoke test on macOS and Ubuntu. |

## Contributing

Issues and pull requests are welcome, in particular real failure modes the pre-flight checks miss.

Run [shellcheck](https://www.shellcheck.net/) on the scripts before you push, because CI does. Add notable changes to [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Support

If proxy-lab.sh saved you an afternoon, or one `ERR_CERT_AUTHORITY_INVALID` hunt, consider [buying me a coffee](https://buymeacoffee.com/kibotu).
