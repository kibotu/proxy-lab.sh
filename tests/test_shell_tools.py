from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_HOSTS_DIR = tempfile.TemporaryDirectory()


def setUpModule() -> None:
    # Tests must not depend on whatever the machine running them has in
    # /etc/hosts: scope capture to a known file instead.
    hosts = Path(_HOSTS_DIR.name) / "hosts"
    hosts.write_text("127.0.0.1 localhost\n127.0.0.1 app-dev.example.test\n", encoding="utf-8")
    os.environ["PROXY_LAB_HOSTS_FILE"] = str(hosts)


def tearDownModule() -> None:
    os.environ.pop("PROXY_LAB_HOSTS_FILE", None)
    _HOSTS_DIR.cleanup()


class ShellIntegrationTests(unittest.TestCase):
    def run_command(self, command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def approved_extension(self, fake_bin: Path) -> None:
        """Stub the network-extension approval.

        Without this the launcher reads the real host state, so a developer
        machine with mitmproxy installed but not approved fails these tests
        while CI, which has no systemextensionsctl at all, passes them.
        """
        stub = fake_bin / "systemextensionsctl"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "cat <<'PAYLOAD'\n"
            "\t*\tTEAM\torg.mitmproxy.macos-redirector.network-extension (2.0/1)"
            "\tnetwork-extension\t[activated enabled]\n"
            "PAYLOAD\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def test_ios_trust_uses_selected_udid_with_fake_xcrun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            home = root / "home"
            fake_bin.mkdir()
            home.mkdir()
            xcrun_log = root / "xcrun.log"
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                """#!/usr/bin/env bash
printf '%s\\n' \"$*\" >> \"$XCRUN_LOG\"
if [ \"$1\" = simctl ] && [ \"$2\" = list ]; then
  printf '%s\\n' '== Devices ==' '    iPhone (SIM-UDID) (Booted)'
fi
""",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            fake_mitmproxy = fake_bin / "mitmdump"
            fake_mitmproxy.write_text(
                """#!/usr/bin/env bash
if [ \"${1:-}\" = --version ]; then
  echo 'Mitmproxy: 12.2.3'
  exit 0
fi
if [ \"${1:-}\" = --listen-port ]; then
  mkdir -p \"$HOME/.mitmproxy\"
  printf 'fake-certificate\\n' > \"$HOME/.mitmproxy/mitmproxy-ca-cert.pem\"
fi
""",
                encoding="utf-8",
            )
            fake_mitmproxy.chmod(0o755)
            xcrun_log.write_text("", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(home),
                    "XCRUN_LOG": str(xcrun_log),
                    "PROXY_LAB_MITMDUMP": str(fake_mitmproxy),
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_PYTHON": "python3",
                }
            )
            result = self.run_command(
                [
                    "bash",
                    str(ROOT / "ios" / "start-proxy.sh"),
                    "--trust-only",
                    "--udid",
                    "SIM-UDID",
                ],
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "simctl keychain SIM-UDID add-root-cert",
                xcrun_log.read_text(encoding="utf-8"),
            )

    def test_ios_resolves_an_installed_xcode_when_simctl_does_not_answer(self) -> None:
        # xcode-select points at Command Line Tools: xcrun is on PATH but every
        # simctl call fails. The run must fall back to an installed Xcode.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            home = root / "home"
            fake_bin.mkdir()
            home.mkdir()
            developer = home / "Applications" / "Xcode-99.0.app" / "Contents" / "Developer"
            developer.mkdir(parents=True)
            xcrun_log = root / "xcrun.log"
            (fake_bin / "xcrun").write_text(
                """#!/usr/bin/env bash
printf '%s|%s\\n' \"${DEVELOPER_DIR:-}\" \"$*\" >> \"$XCRUN_LOG\"
if [ \"${DEVELOPER_DIR:-}\" = \"$EXPECTED_DEVELOPER_DIR\" ]; then
  printf '%s\\n' '== Devices ==' '    iPhone (SIM-UDID) (Booted)'
  exit 0
fi
exit 1
""",
                encoding="utf-8",
            )
            (fake_bin / "xcrun").chmod(0o755)
            xcrun_log.write_text("", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(home),
                    "XCRUN_LOG": str(xcrun_log),
                    "EXPECTED_DEVELOPER_DIR": str(developer),
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_PYTHON": "python3",
                }
            )
            env.pop("DEVELOPER_DIR", None)
            result = self.run_command(
                [
                    "bash",
                    "-c",
                    f'source "{ROOT}/proxy_lab/common.sh"; ios_developer_dir',
                ],
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(developer))
            # One ambient probe that fails, then the candidate is tried and its
            # device list read. Every call after the first is against the Xcode.
            self.assertEqual(
                xcrun_log.read_text(encoding="utf-8").splitlines(),
                [
                    "|simctl list devices booted",
                    f"{developer}|simctl list devices booted",
                    f"{developer}|simctl list devices booted",
                ],
            )

    def test_ios_keeps_a_working_simctl_and_overrides_nothing(self) -> None:
        # An environment whose simctl already works is left exactly as it is:
        # no scan, no export, nothing to fix.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            home = root / "home"
            fake_bin.mkdir()
            home.mkdir()
            xcrun_log = root / "xcrun.log"
            (fake_bin / "xcrun").write_text(
                """#!/usr/bin/env bash
printf '%s|%s\\n' \"${DEVELOPER_DIR:-}\" \"$*\" >> \"$XCRUN_LOG\"
printf '%s\\n' '== Devices ==' '    iPhone (SIM-UDID) (Booted)'
""",
                encoding="utf-8",
            )
            (fake_bin / "xcrun").chmod(0o755)
            xcrun_log.write_text("", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(home),
                    "XCRUN_LOG": str(xcrun_log),
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_PYTHON": "python3",
                }
            )
            env.pop("DEVELOPER_DIR", None)
            result = self.run_command(
                [
                    "bash",
                    "-c",
                    f'source "{ROOT}/proxy_lab/common.sh"; ios_developer_dir',
                ],
                env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            # Exactly one probe, against the ambient selection. Nothing was
            # scanned, exported, or substituted.
            self.assertEqual(
                xcrun_log.read_text(encoding="utf-8").splitlines(),
                ["|simctl list devices booted"],
            )

    def test_ios_extension_state_is_read_from_systemextensionsctl(self) -> None:
        # An installed-but-unapproved extension makes mitmdump capture nothing
        # while reporting itself healthy, so the state has to be read, not
        # assumed. Each case is a different systemextensionsctl line.
        header = (
            "--- com.apple.system_extension.network_extension ---\n"
            "enabled\tactive\tteamID\tbundleID (version)\tname\t[state]\n"
        )
        cases = {
            "pending": "\t*\tTEAM\torg.mitmproxy.macos-redirector."
            "network-extension (2.0/1)\tnetwork-extension\t"
            "[activated waiting for user]\n",
            "approved": "\t*\tTEAM\torg.mitmproxy.macos-redirector."
            "network-extension (2.0/1)\tnetwork-extension\t[activated enabled]\n",
            "absent": "",
            "unknown": "\t*\tTEAM\torg.mitmproxy.macos-redirector."
            "network-extension (2.0/1)\tnetwork-extension\t[something else]\n",
        }
        for expected, line in cases.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake_bin = root / "bin"
                home = root / "home"
                fake_bin.mkdir()
                home.mkdir()
                (fake_bin / "systemextensionsctl").write_text(
                    f"#!/usr/bin/env bash\ncat <<'PAYLOAD'\n{header}{line}PAYLOAD\n",
                    encoding="utf-8",
                )
                (fake_bin / "systemextensionsctl").chmod(0o755)
                env = os.environ.copy()
                env.update({"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(home)})
                result = self.run_command(
                    [
                        "bash",
                        "-c",
                        f'source "{ROOT}/proxy_lab/common.sh"; mitmproxy_extension_state',
                    ],
                    env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_android_start_restores_proxy_and_removes_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            home = root / "home"
            adb_state = root / "adb-state"
            session_state = root / "session"
            fake_bin.mkdir()
            home.mkdir()
            adb_state.mkdir()
            (adb_state / "proxy").write_text("10.0.0.1:8888\n", encoding="utf-8")
            lsof_pid = root / "lsof.pid"

            scripts = {
                "mitmdump": """#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then
  echo 'Mitmproxy: 12.2.3'
  exit 0
fi
if [ "${1:-}" = --listen-port ] && [ "${2:-}" = 0 ]; then
  mkdir -p "$HOME/.mitmproxy"
  printf 'fake-certificate\\n' > "$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
  exit 0
fi
printf '%s\\n' "$$" > "$LSOF_PID_FILE"
exec sleep 60
""",
                "openssl": """#!/usr/bin/env bash
if [ "${1:-}" = x509 ]; then
  echo FAKEHASH
  exit 0
fi
exit 0
""",
                "lsof": """#!/usr/bin/env bash
if [ -f "$LSOF_PID_FILE" ]; then
  cat "$LSOF_PID_FILE"
fi
exit 0
""",
                "adb": """#!/usr/bin/env bash
set -e
args=("$@")
if [ "${args[0]:-}" = -s ]; then
  args=("${args[@]:2}")
fi
command="${args[0]:-}"
if [ "$command" = devices ]; then
  printf '%s\\n' 'List of devices attached' 'emulator-5554 device'
  exit 0
fi
if [ "$command" = root ]; then
  echo restarting adbd
  exit 0
fi
case "$command" in
  wait-for-device|reboot) exit 0 ;;
  push) touch "$ADB_STATE/cert"; exit 0 ;;
  shell) ;;
  *) exit 0 ;;
esac
rest="${args[*]:1}"
case "$rest" in
  *'getprop sys.boot_completed'*) echo 1 ;;
  *'settings get global http_proxy'*) cat "$ADB_STATE/proxy" ;;
  *'settings put global http_proxy'*) printf '%s\\n' "${rest##* }" > "$ADB_STATE/proxy" ;;
  *'settings delete global http_proxy'*) echo null > "$ADB_STATE/proxy" ;;
  *'test -f /data/misc/user/0/cacerts-added/FAKEHASH.0'*) test -f "$ADB_STATE/cert" ;;
esac
""",
            }
            for name, content in scripts.items():
                path = fake_bin / name
                path.write_text(content, encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(home),
                    "ADB_STATE": str(adb_state),
                    "LSOF_PID_FILE": str(lsof_pid),
                    "PROXY_LAB_STATE_DIR": str(session_state),
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_MITMDUMP": str(fake_bin / "mitmdump"),
                    "PROXY_LAB_PYTHON": sys.executable,
                    "PORT": "18999",
                }
            )
            process = subprocess.Popen(
                ["bash", str(ROOT / "android" / "start-proxy.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if lsof_pid.exists() and (session_state / "android-18999" / "proxy_pid").exists():
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertIsNone(process.poll(), "Android launcher exited before listening")
            process.terminate()
            output, _ = process.communicate(timeout=10)
            self.assertIn("device proxy restored", output)
            self.assertEqual(
                (adb_state / "proxy").read_text(encoding="utf-8"), "10.0.0.1:8888\n"
            )
            self.assertFalse((session_state / "android-18999").exists())

    def test_state_acquire_reclaims_a_stopped_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "android-8080"
            state_dir.mkdir()
            (state_dir / "owner").write_text("999999\n", encoding="utf-8")
            (state_dir / "platform").write_text("android\n", encoding="utf-8")
            (state_dir / "serial").write_text("emulator-5554\n", encoding="utf-8")
            (state_dir / "previous_proxy").write_text("10.0.0.1:8888\n", encoding="utf-8")
            adb_log = root / "adb.log"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_adb = fake_bin / "adb"
            fake_adb.write_text(
                """#!/usr/bin/env bash
printf '%s\\n' \"$*\" >> \"$ADB_LOG\"
if [ \"$1\" = devices ]; then
  printf '%s\\n' 'List of devices attached' 'emulator-5554 device'
fi
exit 0
""",
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)
            adb_log.write_text("", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "ADB_LOG": str(adb_log),
                    "PROXY_LAB_STATE_DIR": str(root),
                }
            )

            result = self.run_command(
                [
                    "bash",
                    "-c",
                    (
                        "set -euo pipefail; "
                        f"PROJECT_DIR={str(ROOT)!r}; "
                        f"source {str(ROOT / 'proxy_lab' / 'common.sh')!r}; "
                        "state_acquire android 8080; "
                        'test "$(cat "$STATE_DIR/platform")" = android; '
                        'test "$(cat "$STATE_DIR/previous_proxy")" = "" || '
                        'test ! -f "$STATE_DIR/previous_proxy"; '
                        "state_release; "
                        'test ! -d "$STATE_DIR"'
                    ),
                ],
                env,
            )

            # start must clear the stale slot itself, restoring the proxy the
            # stopped session left behind.
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("reclaiming state", result.stdout)
            self.assertIn("settings put global http_proxy 10.0.0.1:8888", adb_log.read_text(encoding="utf-8"))

    def test_state_acquire_refuses_a_live_proxy_lab_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "android-8080"
            state_dir.mkdir()
            owner = subprocess.Popen(
                ["bash", "-c", 'exec -a "bash /x/start-proxy.sh" sleep 30']
            )
            try:
                (state_dir / "owner").write_text(f"{owner.pid}\n", encoding="utf-8")
                (state_dir / "platform").write_text("android\n", encoding="utf-8")
                result = self.run_command(
                    [
                        "bash",
                        "-c",
                        (
                            "set -euo pipefail; "
                            f"PROJECT_DIR={str(ROOT)!r}; "
                            f"source {str(ROOT / 'proxy_lab' / 'common.sh')!r}; "
                            "state_acquire android 8080"
                        ),
                    ],
                    {**os.environ, "PROXY_LAB_STATE_DIR": str(root)},
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("another proxy-lab session is active", result.stderr)
                self.assertEqual(
                    (state_dir / "owner").read_text(encoding="utf-8"), f"{owner.pid}\n"
                )
            finally:
                owner.kill()
                owner.wait()

    def test_stop_does_not_kill_an_unowned_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "ios"
            state_dir.mkdir()
            (state_dir / "owner").write_text(f"{os.getpid()}\n", encoding="utf-8")
            (state_dir / "platform").write_text("ios\n", encoding="utf-8")
            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "stop", "ios"],
                {**os.environ, "PROXY_LAB_STATE_DIR": directory},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(state_dir.exists())
            self.assertIn("not the recorded proxy-lab owner", result.stderr)

    def test_status_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "android-8080"
            state_dir.mkdir()
            # A path with a quote and a backslash must not break the JSON.
            (state_dir / "owner").write_text("999999\n", encoding="utf-8")
            (state_dir / "platform").write_text("android\n", encoding="utf-8")
            (state_dir / "port").write_text("8080\n", encoding="utf-8")
            (state_dir / "config").write_text('a"b\\c.yml\n', encoding="utf-8")

            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "status", "--json"],
                {**os.environ, "PROXY_LAB_STATE_DIR": directory},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            sessions = json.loads(result.stdout)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["state"], "stale")
            self.assertEqual(sessions[0]["platform"], "android")
            self.assertEqual(sessions[0]["config"], 'a"b\\c.yml')

    def test_doctor_json_reports_the_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            fake_mitmproxy = fake_bin / "mitmdump"
            fake_mitmproxy.write_text(
                """#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then echo 'Mitmproxy: 12.2.3'; exit 0; fi
exit 0
""",
                encoding="utf-8",
            )
            fake_mitmproxy.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "PROXY_LAB_STATE_DIR": directory,
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_MITMDUMP": str(fake_mitmproxy),
                    "PROXY_LAB_PYTHON": sys.executable,
                }
            )

            result = self.run_command(
                [
                    "bash",
                    str(ROOT / "proxy_lab" / "control.sh"),
                    "doctor",
                    "ios",
                    "--json",
                ],
                env,
            )

            # --json is the whole document, not a trailer after human output.
            report = json.loads(result.stdout)
            self.assertIn("checks", report)
            self.assertIn("ok", report)
            self.assertIn("next", report)
            self.assertTrue(any(c["name"] == "mitmproxy" for c in report["checks"]))

    def test_doctor_exits_non_zero_when_a_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "bad.yml"
            config.write_text("domains: not-a-list\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": "/usr/bin:/bin",
                    "PROXY_LAB_STATE_DIR": directory,
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_CONFIG": str(config),
                    "PROXY_LAB_PYTHON": sys.executable,
                    "PROXY_LAB_PYTHONPATH": str(ROOT),
                }
            )
            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "doctor", "--json"],
                env,
            )

            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertFalse(report["ok"])
            failed = [c for c in report["checks"] if c["status"] == "fail"]
            self.assertTrue(any(c["name"] == "config" for c in failed))

    def test_stop_json_reports_the_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "ios"
            state_dir.mkdir()
            # A live PID that is not a proxy-lab owner: stop must refuse it.
            (state_dir / "owner").write_text(f"{os.getpid()}\n", encoding="utf-8")
            (state_dir / "platform").write_text("ios\n", encoding="utf-8")

            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "stop", "ios", "--json"],
                {**os.environ, "PROXY_LAB_STATE_DIR": directory},
            )

            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report[0]["status"], "fail")
            self.assertIn("not the recorded", report[0]["detail"])

    def test_reset_clears_stale_state_when_the_recorded_device_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "android-8080"
            state_dir.mkdir()
            (state_dir / "owner").write_text("999999\n", encoding="utf-8")
            (state_dir / "platform").write_text("android\n", encoding="utf-8")
            (state_dir / "serial").write_text("emulator-5554\n", encoding="utf-8")
            (state_dir / "previous_proxy").write_text("10.0.0.1:8888\n", encoding="utf-8")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_adb = fake_bin / "adb"
            fake_adb.write_text(
                """#!/usr/bin/env bash
if [ "$1" = devices ]; then
  printf '%s\\n' 'List of devices attached'
fi
exit 1
""",
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {"PATH": f"{fake_bin}:/usr/bin:/bin", "PROXY_LAB_STATE_DIR": str(root)}
            )

            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "reset", "android"],
                env,
            )

            # No emulator to restore the proxy on, so reset must still release the
            # stale state directory; otherwise start is blocked forever.
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("emulator-5554 is not reachable", result.stdout)
            self.assertFalse(state_dir.exists())

    def test_reset_keeps_state_but_still_clears_the_proxy_when_restore_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "android-8080"
            state_dir.mkdir()
            (state_dir / "owner").write_text("999999\n", encoding="utf-8")
            (state_dir / "platform").write_text("android\n", encoding="utf-8")
            (state_dir / "serial").write_text("emulator-5554\n", encoding="utf-8")
            (state_dir / "previous_proxy").write_text("10.0.0.1:8888\n", encoding="utf-8")
            adb_log = root / "adb.log"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_adb = fake_bin / "adb"
            fake_adb.write_text(
                """#!/usr/bin/env bash
printf '%s\\n' \"$*\" >> \"$ADB_LOG\"
case \"$*\" in
  *devices*) printf '%s\\n' 'List of devices attached' 'emulator-5554 device'; exit 0 ;;
  *'settings put global http_proxy'*) exit 1 ;;
esac
exit 0
""",
                encoding="utf-8",
            )
            fake_adb.chmod(0o755)
            adb_log.write_text("", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "ADB_LOG": str(adb_log),
                    "PROXY_LAB_STATE_DIR": str(root),
                }
            )

            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "reset", "android"],
                env,
            )

            # A reachable device that refuses the restore keeps the state for a
            # retry, but the rest of reset must still run.
            self.assertEqual(result.returncode, 1)
            self.assertTrue(state_dir.exists())
            self.assertIn("settings delete global http_proxy", adb_log.read_text(encoding="utf-8"))

    def test_detach_from_the_environment_survives_argument_parsing(self) -> None:
        result = self.run_command(
            [
                "bash",
                "-c",
                (
                    "set -euo pipefail; "
                    f"PROJECT_DIR={str(ROOT)!r}; "
                    f"source {str(ROOT / 'proxy_lab' / 'common.sh')!r}; "
                    f"ROUTER={str(ROOT / 'local_router.py')!r}; "
                    f"CONFIG={str(ROOT / 'domains.yaml')!r}; "
                    "PLATFORM_NAME=ios; PORT=0; "
                    "DETACH=1; DURATION=30; PROXY_LAB_LOG_FORMAT=jsonl; "
                    "parse_launcher_args; "
                    'test "$DETACH" = 1; '
                    'test "$DURATION" = 30; '
                    'test "$LOG_FORMAT" = jsonl'
                ),
            ],
            os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def ios_env(self, root: Path, fake_bin: Path, hosts: str | None = None) -> dict[str, str]:
        """An isolated iOS environment with a fake mitmdump that stays up."""
        home = root / "home"
        home.mkdir(exist_ok=True)
        fake_bin.mkdir(exist_ok=True)
        mitmdump = fake_bin / "mitmdump"
        mitmdump.write_text(
            """#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then echo 'Mitmproxy: 12.2.3'; exit 0; fi
mkdir -p "$HOME/.mitmproxy"
printf 'fake-certificate\\n' > "$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
printf '%s\\n' "$$" > "$MITMDUMP_PID"
# Stay a process named mitmdump, as the real one does.
while :; do sleep 1; done
""",
            encoding="utf-8",
        )
        mitmdump.chmod(0o755)
        xcrun = fake_bin / "xcrun"
        xcrun.write_text(
            "#!/usr/bin/env bash\n"
            "if [ \"${1:-}\" = simctl ]; then echo '    iPhone (SIM-1) (Booted)'; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        xcrun.chmod(0o755)
        self.approved_extension(fake_bin)
        # Never let a test reach the real networksetup: by default there are no
        # network services to read or change.
        networksetup = fake_bin / "networksetup"
        networksetup.write_text(
            "#!/usr/bin/env bash\n"
            "[ \"${1:-}\" = -listallnetworkservices ] && "
            "echo 'An asterisk (*) denotes that a network service is disabled.'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        networksetup.chmod(0o755)
        hosts_file = root / "hosts"
        hosts_file.write_text(
            hosts
            if hosts is not None
            else "127.0.0.1 localhost\n127.0.0.1 app-dev.example.test\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(home),
                "MITMDUMP_PID": str(root / "mitmdump.pid"),
                "PROXY_LAB_STATE_DIR": str(root / "state"),
                "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                "PROXY_LAB_HOSTS_FILE": str(hosts_file),
                "PROXY_LAB_MITMDUMP": str(mitmdump),
                "PROXY_LAB_PYTHON": sys.executable,
            }
        )
        env.pop("PROXY_LAB_CONFIG", None)
        return env

    def wait_for(self, condition, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_start_refuses_when_there_is_nothing_to_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.ios_env(root, root / "bin", hosts="127.0.0.1 localhost\n")
            result = self.run_command(["bash", str(ROOT / "ios" / "start-proxy.sh")], env)
            self.assertEqual(result.returncode, 3)
            self.assertIn("nothing to capture", result.stderr)
            self.assertFalse((root / "state" / "ios").exists())

    def test_start_scopes_capture_to_the_hosts_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.ios_env(root, root / "bin")
            result = self.run_command(
                ["bash", str(ROOT / "ios" / "start-proxy.sh"), "--duration", "1"], env
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1 hosts from /etc/hosts", result.stdout)
            self.assertIn("1 /etc/hosts names point at localhost", result.stdout)

    def test_ctrl_c_stops_mitmdump_and_releases_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.ios_env(root, root / "bin")
            launcher = subprocess.Popen(
                ["bash", str(ROOT / "ios" / "start-proxy.sh")],
                cwd=ROOT, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            try:
                pid_file = root / "mitmdump.pid"
                self.assertTrue(self.wait_for(lambda: (root / "state" / "ios" / "ready").exists()))
                mitmdump = int(pid_file.read_text().strip())
                launcher.send_signal(signal.SIGINT)
                launcher.send_signal(signal.SIGINT)  # a nervous second Ctrl-C
                launcher.wait(timeout=20)
                self.assertFalse((root / "state" / "ios").exists(), "state left behind")
                self.assertTrue(self.wait_for(lambda: not self.pid_alive(mitmdump)), "mitmdump left running")
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.wait()
                if launcher.stdout:
                    launcher.stdout.close()

    def test_reset_ends_a_mitmdump_left_by_a_killed_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.ios_env(root, root / "bin")
            launcher = subprocess.Popen(
                ["bash", str(ROOT / "ios" / "start-proxy.sh")],
                cwd=ROOT, env=env, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            mitmdump = 0
            try:
                self.assertTrue(self.wait_for(lambda: (root / "state" / "ios" / "ready").exists()))
                mitmdump = int((root / "mitmdump.pid").read_text().strip())
                launcher.kill()  # SIGKILL: no trap, no cleanup
                launcher.wait()
                self.assertTrue(self.pid_alive(mitmdump), "expected an orphan to test with")
                result = self.run_command(
                    ["bash", str(ROOT / "proxy_lab" / "control.sh"), "reset", "ios"], env
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.wait_for(lambda: not self.pid_alive(mitmdump)), "orphan survived reset")
                self.assertFalse((root / "state" / "ios").exists())
            finally:
                if mitmdump and self.pid_alive(mitmdump):
                    os.kill(mitmdump, signal.SIGKILL)

    def test_start_ends_a_mitmdump_left_by_a_killed_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.ios_env(root, root / "bin")
            launcher = subprocess.Popen(
                ["bash", str(ROOT / "ios" / "start-proxy.sh")],
                cwd=ROOT, env=env, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            mitmdump = 0
            try:
                self.assertTrue(self.wait_for(lambda: (root / "state" / "ios" / "ready").exists()))
                mitmdump = int((root / "mitmdump.pid").read_text().strip())
                launcher.kill()
                launcher.wait()
                result = self.run_command(
                    ["bash", str(ROOT / "ios" / "start-proxy.sh"), "--duration", "1"], env
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ended a leftover mitmdump", result.stdout)
                self.assertFalse(self.pid_alive(mitmdump))
            finally:
                if mitmdump and self.pid_alive(mitmdump):
                    os.kill(mitmdump, signal.SIGKILL)

    ORIGINAL_PAC = "no|http://old.example/proxy.pac"

    def system_proxy_env(self, root: Path) -> dict[str, str]:
        """iOS environment with a fake networksetup and a mitmdump that listens."""
        fake_bin = root / "bin"
        env = self.ios_env(root, fake_bin)
        (root / "ns").write_text(self.ORIGINAL_PAC + "\n", encoding="utf-8")
        (fake_bin / "networksetup").write_text(
            """#!/usr/bin/env bash
S="$NS_STATE"
IFS='|' read -r enabled url <"$S"
case "$1" in
  -listallnetworkservices) printf '%s\\n' 'An asterisk (*) denotes that a network service is disabled.' 'Wi-Fi' ;;
  -listnetworkserviceorder) printf '%s\\n' '(1) Wi-Fi' '(Hardware Port: Wi-Fi, Device: en0)' ;;
  -getautoproxyurl)
    [ "$enabled" = yes ] && shown=Yes || shown=No
    printf 'URL: %s\\nEnabled: %s\\n' "${url:-(null)}" "$shown" ;;
  -setautoproxyurl) printf 'yes|%s\\n' "$3" >"$S" ;;
  -setautoproxystate) [ "$3" = off ] && printf 'no|%s\\n' "$url" >"$S" ;;
esac
""",
            encoding="utf-8",
        )
        (fake_bin / "route").write_text(
            "#!/usr/bin/env bash\necho '   interface: en0'\n", encoding="utf-8"
        )
        (fake_bin / "mitmdump").write_text(
            """#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then echo 'Mitmproxy: 12.2.3'; exit 0; fi
port=''
while [ "$#" -gt 0 ]; do
  [ "$1" = --listen-port ] && port="$2"
  shift
done
mkdir -p "$HOME/.mitmproxy"
printf 'fake-certificate\\n' > "$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
printf '%s\\n' "$$" > "$MITMDUMP_PID"
# The listener exits with its parent, so a force-killed fake leaves nothing behind.
"$FAKE_PYTHON" -c 'import os, socket, sys, time
parent = os.getppid()
s = socket.socket()
if sys.argv[1]:
    s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen()
while os.getppid() == parent:
    time.sleep(0.2)' "$port" &
child=$!
trap 'kill $child 2>/dev/null' TERM EXIT
wait $child
""",
            encoding="utf-8",
        )
        for name in ("networksetup", "route", "mitmdump"):
            (fake_bin / name).chmod(0o755)
        env["NS_STATE"] = str(root / "ns")
        env["FAKE_PYTHON"] = sys.executable
        return env

    def start_system_proxy(self, root: Path, env: dict[str, str]) -> subprocess.Popen:
        launcher = subprocess.Popen(
            ["bash", str(ROOT / "ios" / "start-proxy.sh"), "--system-proxy"],
            cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertTrue(
            self.wait_for(lambda: (root / "state" / "ios" / "ready").exists()),
            "system-proxy session never became ready",
        )
        return launcher

    def test_system_proxy_sets_a_pac_for_your_hosts_and_restores_it(self) -> None:
        import urllib.request

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.system_proxy_env(root)
            launcher = self.start_system_proxy(root, env)
            try:
                enabled, url = (root / "ns").read_text().strip().split("|", 1)
                self.assertEqual(enabled, "yes")
                self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/proxy\.pac$")
                pac = urllib.request.urlopen(url, timeout=5).read().decode()
                self.assertIn("app-dev.example.test", pac)
                self.assertIn('return "DIRECT"', pac)
                launcher.send_signal(signal.SIGINT)
                launcher.send_signal(signal.SIGINT)
                launcher.wait(timeout=20)
                self.assertEqual((root / "ns").read_text().strip(), self.ORIGINAL_PAC)
                self.assertFalse((root / "state" / "ios").exists())
            finally:
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.wait()
                if launcher.stdout:
                    launcher.stdout.close()

    def killed_system_proxy_session(self, root: Path, env: dict[str, str]) -> None:
        launcher = self.start_system_proxy(root, env)
        launcher.kill()  # SIGKILL: no trap, no cleanup
        launcher.wait()
        if launcher.stdout:
            launcher.stdout.close()
        self.assertTrue((root / "ns").read_text().startswith("yes|"), "expected a leftover")

    def reap_stragglers(self, root: Path) -> None:
        pid_file = root / "mitmdump.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            if self.pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        pac_pid = root / "state" / "ios" / "pac_pid"
        if pac_pid.exists():
            try:
                os.kill(int(pac_pid.read_text().strip()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_doctor_flags_a_leftover_proxy_and_reset_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.system_proxy_env(root)
            try:
                self.killed_system_proxy_session(root, env)
                doctor = self.run_command(
                    ["bash", str(ROOT / "proxy_lab" / "control.sh"), "doctor", "ios"], env
                )
                self.assertIn("system proxy", doctor.stdout)
                self.assertIn("proxy-lab reset ios", doctor.stdout)
                reset = self.run_command(
                    ["bash", str(ROOT / "proxy_lab" / "control.sh"), "reset", "ios"], env
                )
                self.assertEqual(reset.returncode, 0, reset.stderr)
                self.assertEqual((root / "ns").read_text().strip(), self.ORIGINAL_PAC)
            finally:
                self.reap_stragglers(root)

    def test_next_start_puts_a_leftover_proxy_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.system_proxy_env(root)
            try:
                self.killed_system_proxy_session(root, env)
                result = self.run_command(
                    ["bash", str(ROOT / "ios" / "start-proxy.sh"), "--duration", "1"], env
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / "ns").read_text().strip(), self.ORIGINAL_PAC)
            finally:
                self.reap_stragglers(root)

    def test_system_proxy_is_ios_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.ios_env(root, root / "bin")
            result = self.run_command(
                ["bash", str(ROOT / "android" / "start-proxy.sh"), "--system-proxy"], env
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("only valid for iOS", result.stderr)

    def test_ios_launcher_survives_a_mitmdump_that_exits_immediately(self) -> None:
        # CI's smoke fake writes its argv and returns at once. Anything that
        # probes mitmdump liveness misreads that as a failed start.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            home = root / "home"
            args_file = root / "args"
            fake_bin.mkdir()
            home.mkdir()
            fake_mitmproxy = fake_bin / "mitmdump"
            fake_mitmproxy.write_text(
                """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  echo 'Mitmproxy: 12.0.0'
  exit 0
fi
if [ "${1:-}" = "--listen-port" ] && [ "${2:-}" = 0 ]; then
  mkdir -p "$HOME/.mitmproxy"
  : >"$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
fi
printf '%s\\n' "$@" >"$MITMDUMP_ARGS"
""",
                encoding="utf-8",
            )
            fake_mitmproxy.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                """#!/usr/bin/env bash
if [ "${1:-}" = simctl ]; then echo '== Devices =='; fi
exit 0
""",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            self.approved_extension(fake_bin)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(home),
                    "MITMDUMP_ARGS": str(args_file),
                    "PROXY_LAB_STATE_DIR": str(root / "state"),
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_MITMDUMP": str(fake_mitmproxy),
                    "PROXY_LAB_PYTHON": sys.executable,
                }
            )

            result = self.run_command(
                ["bash", str(ROOT / "ios" / "start-proxy.sh")], env
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = args_file.read_text(encoding="utf-8").splitlines()
            self.assertIn("--mode", argv)
            self.assertIn("local:Simulator", argv)
            self.assertIn("--showhost", argv)
            self.assertNotIn("--listen-port", argv)

    def test_detached_ios_run_is_stoppable_and_its_log_survives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            home = root / "home"
            fake_bin.mkdir()
            home.mkdir()
            fake_mitmproxy = fake_bin / "mitmdump"
            fake_mitmproxy.write_text(
                """#!/usr/bin/env bash
if [ "${1:-}" = --version ]; then echo 'Mitmproxy: 12.2.3'; exit 0; fi
if [ "${1:-}" = "--listen-port" ] && [ "${2:-}" = 0 ]; then
  mkdir -p "$HOME/.mitmproxy"
  printf 'fake-certificate\\n' > "$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
  exit 0
fi
echo '[local_router] https://api.example.com/v1'
exec sleep 300
""",
                encoding="utf-8",
            )
            fake_mitmproxy.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                """#!/usr/bin/env bash
if [ "${1:-}" = simctl ]; then echo '    iPhone (SIM-1) (Booted)'; fi
exit 0
""",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            self.approved_extension(fake_bin)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "HOME": str(home),
                    "PROXY_LAB_STATE_DIR": str(root / "state"),
                    "PROXY_LAB_SKIP_UPDATE_CHECK": "1",
                    "PROXY_LAB_MITMDUMP": str(fake_mitmproxy),
                    "PROXY_LAB_PYTHON": sys.executable,
                }
            )

            # --json is agent mode: return once up, with a ready document.
            start = time.monotonic()
            result = self.run_command(
                ["bash", str(ROOT / "ios" / "start-proxy.sh"), "--json"], env
            )
            elapsed = time.monotonic() - start
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(elapsed, 60, "detached start blocked on the proxy")
            ready = json.loads(result.stdout)
            self.assertTrue(ready["ok"])
            self.assertEqual(ready["platform"], "ios")
            self.assertEqual(ready["log_format"], "jsonl")
            log = Path(ready["log"])
            self.assertTrue(log.is_file())

            # stop must signal the owner and release the session.
            stop = self.run_command(
                [
                    "bash",
                    str(ROOT / "proxy_lab" / "control.sh"),
                    "stop",
                    "ios",
                    "--json",
                ],
                env,
            )
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertEqual(json.loads(stop.stdout)[0]["status"], "ok")

            # The log outlives the session: a caller that just ran a test needs it.
            after = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "logs", "ios"], env
            )
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertIn(
                "[local_router] https://api.example.com/v1", after.stdout
            )


if __name__ == "__main__":
    unittest.main()
