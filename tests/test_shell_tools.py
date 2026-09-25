from __future__ import annotations

import os
import subprocess
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
                    "PROXY_LAB_PYTHON": os.environ.get("PYTHON", "python3"),
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


if __name__ == "__main__":
    unittest.main()
