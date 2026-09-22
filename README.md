# proxy-lab.sh

One command to see your app's HTTPS traffic: a [mitmproxy](https://www.mitmproxy.org/) for the **Android emulator** and the **iOS simulator**.

**Get started:** list your domains in [`domains.yaml`](domains.yaml), then run the script for your platform:

```bash
./android/start-proxy.sh    # Android: checks → CA → proxy setting → mitmdump
./ios/start-proxy.sh        # iOS: shares the host network, no device setup
```

## Why this repo

An app dev just wants to see their app's traffic — not spend a day on certificate plumbing. On the Android emulator, mitmproxy doesn't work out of the box: two separate things must be hand-wired before the first HTTPS request shows up in your terminal:

- **Trust.** Since Android 7, apps ignore user-installed CAs. The [official mitmproxy guide](https://docs.mitmproxy.org/stable/howto/install-system-trusted-ca-android/) puts the CA into the read-only *system* store instead: hash the certificate by hand, remount `/system`, disable verified boot, reboot — or handcraft a Magisk module for Google Play images. Android 14+ moved that store into an immutable APEX, and any boot without `-writable-system` loads a clean image anyway.
- **Routing.** The emulator must be pointed at the host — `10.0.2.2:8080`, not `localhost` — through a manual settings command that goes stale without telling anyone.

Miss one and the symptoms explain nothing: `ERR_CERT_AUTHORITY_INVALID`, or requests that quietly time out.

One script per platform replaces the checklist — Android gets the full treatment, iOS a thin wrapper:

- **The whole sequence** — checks → CA → proxy setting → `mitmdump`; stop with Ctrl-C.
- **Pre-flight, not stack traces** — tooling, AVD image, busy port, missing files are caught before they cost you an afternoon.
- **Certificate, once** — the CA goes into the *user* trust store: no remount, no Magisk, no `-writable-system`; one reboot, once per AVD (see [How trust works](#how-trust-works)).
- **Idempotent** — re-run any time; a stale proxy from a `kill -9` is reaped on the next Android run.
- **Reproducible** — the mitmproxy version is pinned, so the whole team sees the same behaviour.
- **One config, both platforms** — your domains live in `domains.yaml`.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — runs mitmproxy at a pinned version, no global Python involved: `brew install uv`
- **Android:** [Android Studio](https://developer.android.com/studio) (`adb`, `emulator` on `$PATH`) and a **Google APIs** AVD — Play Store images refuse `adb root`, which the one-time CA install needs
- **iOS:** just Xcode — the simulator shares the host's network stack

Your debug build must trust user-installed CAs. That's a network security config with `<debug-overrides>` trusting `user` — see [How trust works](#how-trust-works).

## How it works

```
Emulator → 10.0.2.2:8080 → mitmdump on the host → DNS → 127.0.0.1:443 (your dev server)
```

`mitmdump` intercepts HTTPS traffic; `10.0.2.2` is the host from the emulator's point of view. The proxy resolves your dev domains on the host (e.g. `/etc/hosts` → `127.0.0.1`), so routing is plain DNS — the `local_router.py` addon only logs matching requests for visibility.

The simulator needs none of this plumbing: `127.0.0.1` already reaches your local server directly through the proxy.

## Usage

```bash
# Android — boots the first AVD if none is running, reuses one if it is
./android/start-proxy.sh

# Overrides
AVD=Pixel_10a PORT=8081 ./android/start-proxy.sh

# Stop: Ctrl-C — proxy dies, device proxy setting is cleared. The emulator keeps running.
```

Failures print one line plus a concrete fix — install command, `PATH` export, or doc link. The script never touches processes that aren't its own: a stale `mitmdump` on the port is stopped, anything else fails with a hint (`PORT=<other>`).

### What the script verifies

| Check | Behaviour |
|---|---|
| Tools | `adb`, `uv`, `openssl`, `lsof`, addon file — with install hints; warms mitmproxy via uv |
| Host CA | Generates `~/.mitmproxy/` on first run |
| Emulator | Reuses a running one, boots one if not, `adb root` for the CA push |
| Device CA | Installs into the user trust store if missing — one reboot, once per AVD |
| Port | Reaps stale proxies from earlier runs, refuses foreign processes |
| Proxy setting | Writes `10.0.2.2:8080` only after the port is confirmed ours; cleared on exit |

### iOS

```bash
./ios/start-proxy.sh          # PORT=8080, Ctrl-C to stop
```

One-time trust for HTTPS: open **`mitm.it`** in the simulator's Safari, install the profile (Settings → General → VPN & Device Management), then enable full trust (Settings → General → About → Certificate Trust Settings). No root, no reboot.

## How trust works

Android 14+ reads system CAs from an immutable APEX — pushing certs into `/system/etc/security/cacerts` no longer works. These scripts put the CA into the **user** trust store (`/data/misc/user/0/cacerts-added/`), which debug builds trust via `<debug-overrides>`. It needs root once, reboots once, then survives reboots.

Release builds don't trust user CAs — intercept debug builds only.

## One config, both platforms

`local_router.py` at the repo root is loaded by both scripts; it reads `domains.yaml` next to it. Add your domains once:

```yaml
domains:
  - ".example.com"
```

Matching requests log as `[local_router] …` in the proxy output. mitmproxy's addon API is Python; uv runs it — you never touch an interpreter.

## Troubleshooting

The script's own hints cover most failures. The recurring ones:

- **`adbd cannot run as root in production builds`** — your AVD uses a Google Play image; switch to Google APIs (`grep image.sysdir ~/.android/avd/<AVD>.avd/config.ini`).
- **`net::ERR_CERT_AUTHORITY_INVALID`** — CA missing from the user store, or you're intercepting a release build. Force a reinstall: `adb root && adb shell rm /data/misc/user/0/cacerts-added/<hash>.0`, re-run the script (one reboot), restart the app — running processes don't reload trust anchors.
- **Pages time out** — script running? `adb shell settings get global http_proxy` should say `10.0.2.2:8080`. If it's `null` you stopped the script — traffic going direct is intentional.
- **"No internet connection" banner while proxied** — Android's connectivity probe doesn't trust user CAs, so the OS rates the network "partial connectivity". App traffic is unaffected; ignore it.
- **`[local_router]` lines missing** — domain not in `domains.yaml`.

## Layout

```
android/start-proxy.sh   full lifecycle: checks → CA → proxy setting → mitmdump
ios/start-proxy.sh       bare mitmdump for the simulator (shared host network)
domains.yaml             the domain list — one edit, both platforms
local_router.py          shared addon, reads domains.yaml
```

## Support

If proxy-lab.sh saved you a few hours (or a few `ERR_CERT_AUTHORITY_INVALID` hunts), consider [buying me a coffee](https://buymeacoffee.com/kibotu).

