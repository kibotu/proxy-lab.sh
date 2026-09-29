from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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

    def test_state_acquire_is_atomic_and_owner_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PROXY_LAB_STATE_DIR"] = directory
            result = self.run_command(
                [
                    "bash",
                    "-c",
                    (
                        "set -euo pipefail; "
                        f"PROJECT_DIR={str(ROOT)!r}; "
                        f"source {str(ROOT / 'proxy_lab' / 'common.sh')!r}; "
                        "state_acquire android 8123; "
                        "test -f \"$STATE_DIR/owner\"; "
                        "state_release; "
                        "test ! -d \"$STATE_DIR\""
                    ),
                ],
                env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_status_json_is_an_empty_array_without_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "status", "--json"],
                {**os.environ, "PROXY_LAB_STATE_DIR": directory},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [])

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

            # The JSON document is the last line; the rest is human context.
            report = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertIn("checks", report)
            self.assertIn("ok", report)
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
            report = json.loads(result.stdout.strip().splitlines()[-1])
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
            report = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(report[0]["status"], "fail")
            self.assertIn("not the recorded", report[0]["detail"])

    def test_logs_reports_nothing_when_no_run_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "logs", "--json"],
                {**os.environ, "PROXY_LAB_STATE_DIR": directory},
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {"sessions": []})

    def test_logs_prints_a_previously_written_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            log_dir.mkdir()
            log = log_dir / "ios-0.log"
            log.write_text("[local_router] https://a.example.com/x\n", encoding="utf-8")

            result = self.run_command(
                ["bash", str(ROOT / "proxy_lab" / "control.sh"), "logs", "ios"],
                {**os.environ, "PROXY_LAB_STATE_DIR": directory},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[local_router] https://a.example.com/x", result.stdout)

    def test_invalid_duration_is_rejected_with_the_arguments_code(self) -> None:
        result = self.run_command(
            [
                "bash",
                "-c",
                (
                    "set -euo pipefail; "
                    f"PROJECT_DIR={str(ROOT)!r}; "
                    f"source {str(ROOT / 'proxy_lab' / 'common.sh')!r}; "
                    "PORT=8080; VALIDATE_PORT=1; BOOT_TIMEOUT=240; "
                    "DURATION=notanumber; validate_common_files"
                ),
            ],
            os.environ.copy(),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("duration must be", result.stderr)

    def test_unknown_log_format_is_rejected(self) -> None:
        result = self.run_command(
            [
                "bash",
                "-c",
                (
                    "set -euo pipefail; "
                    f"PROJECT_DIR={str(ROOT)!r}; "
                    f"source {str(ROOT / 'proxy_lab' / 'common.sh')!r}; "
                    "PORT=8080; VALIDATE_PORT=1; BOOT_TIMEOUT=240; "
                    "LOG_FORMAT=xml; validate_common_files"
                ),
            ],
            os.environ.copy(),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown log format", result.stderr)

    def test_reset_restores_recorded_android_proxy(self) -> None:
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
  printf '%s\\n' 'emulator-5554 device'
fi
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
            self.assertEqual(result.returncode, 0, result.stderr)
            log = adb_log.read_text(encoding="utf-8")
            self.assertIn("settings put global http_proxy 10.0.0.1:8888", log)
            self.assertIn("settings delete global http_proxy", log)
            self.assertFalse(state_dir.exists())

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

            # --detach must return promptly, with the proxy up behind it.
            start = time.monotonic()
            result = self.run_command(
                ["bash", str(ROOT / "ios" / "start-proxy.sh"), "--detach"], env
            )
            elapsed = time.monotonic() - start
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(elapsed, 60, "detached start blocked on the proxy")
            self.assertIn("detached", result.stdout)

            # status reports a running session and where its log lives.
            status = self.run_command(
                [
                    "bash",
                    str(ROOT / "proxy_lab" / "control.sh"),
                    "status",
                    "ios",
                    "--json",
                ],
                env,
            )
            session = json.loads(status.stdout)[0]
            self.assertEqual(session["state"], "running")
            log = Path(session["log"])
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
