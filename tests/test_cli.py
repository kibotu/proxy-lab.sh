from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxy_lab.cli import main


class CliTests(unittest.TestCase):
    def test_version_option(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)

    def test_start_dispatches_explicit_options_and_addons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domains.yml"
            config.write_text("domains: []\n", encoding="utf-8")
            addon = Path(directory) / "addon.py"
            addon.write_text("addons = []\n", encoding="utf-8")

            with patch("proxy_lab.cli.os.execvpe") as execvpe:
                result = main(
                    [
                        "start",
                        "android",
                        str(config),
                        "--script",
                        str(addon),
                        "--port",
                        "9090",
                        "--avd",
                        "Pixel_10a",
                        "--serial",
                        "emulator-5554",
                    ]
                )

            self.assertEqual(result, 0)
            command, argv, env = execvpe.call_args.args
            self.assertEqual(command, "bash")
            self.assertTrue(argv[1].endswith("android/start-proxy.sh"))
            self.assertEqual(env["PROXY_LAB_CONFIG"], str(config.resolve()))
            self.assertEqual(env["PROXY_LAB_SCRIPTS"], str(addon.resolve()))
            self.assertEqual(env["PORT"], "9090")
            self.assertEqual(env["AVD"], "Pixel_10a")
            self.assertEqual(env["SERIAL"], "emulator-5554")

    def test_start_rejects_invalid_config_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domains.yml"
            config.write_text("domains: wrong\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                main(["start", "ios", str(config)])
            self.assertEqual(raised.exception.code, 2)

    def test_control_commands_dispatch(self) -> None:
        with patch("proxy_lab.cli.subprocess.call", return_value=0) as call:
            self.assertEqual(main(["status", "android", "--port", "8080"]), 0)
        command = call.call_args.args[0]
        self.assertEqual(command[:3], ["bash", str(Path(__file__).parents[1] / "proxy_lab" / "control.sh"), "status"])
        self.assertIn("android", command)
        self.assertIn("--port", command)

    def test_platform_specific_options_are_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["start", "ios", "--port", "8080"])
        self.assertEqual(raised.exception.code, 2)

    def test_trust_dispatches_ios_launcher(self) -> None:
        with patch("proxy_lab.cli.subprocess.call", return_value=0) as call:
            self.assertEqual(main(["trust", "ios", "--udid", "sim-1"]), 0)
        command, = call.call_args.args
        self.assertTrue(command[1].endswith("ios/start-proxy.sh"))
        self.assertIn("--trust-only", command)
        self.assertIn("--udid", command)

    def test_start_forwards_detach_and_log_format(self) -> None:
        with patch("proxy_lab.cli.os.execvpe") as execvpe:
            result = main(
                [
                    "start",
                    "android",
                    "--detach",
                    "--duration",
                    "30",
                    "--log-format",
                    "jsonl",
                ]
            )

        self.assertEqual(result, 0)
        command, argv, env = execvpe.call_args.args
        self.assertTrue(argv[1].endswith("android/start-proxy.sh"))
        self.assertEqual(env["DETACH"], "1")
        self.assertEqual(env["DURATION"], "30")
        self.assertEqual(env["PROXY_LAB_LOG_FORMAT"], "jsonl")

    def test_logs_forwards_json_and_follow(self) -> None:
        with patch("proxy_lab.cli.subprocess.call", return_value=0) as call:
            self.assertEqual(
                main(["logs", "android", "--json", "--follow", "--lines", "20"]), 0
            )
        command, = call.call_args.args
        self.assertIn("logs", command)
        self.assertIn("--json", command)
        self.assertIn("--follow", command)
        self.assertIn("--lines", command)

    def test_status_forwards_json_to_control(self) -> None:
        with patch("proxy_lab.cli.subprocess.call", return_value=0) as call:
            self.assertEqual(main(["status", "ios", "--json"]), 0)
        command, = call.call_args.args
        self.assertIn("--json", command)

    def test_init_android_writes_the_network_security_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app" / "src" / "debug" / "res").mkdir(parents=True)
            self.assertEqual(main(["init", "android", "--path", str(root)]), 0)
            target = (
                root
                / "app"
                / "src"
                / "debug"
                / "res"
                / "xml"
                / "network_security_config.xml"
            )
            self.assertIn('<certificates src="user" />', target.read_text())

    def test_init_reports_a_missing_resource_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["init", "android", "--path", directory]), 3)

    def test_init_ios_explains_that_no_file_is_needed(self) -> None:
        self.assertEqual(main(["init", "ios"]), 0)


if __name__ == "__main__":
    unittest.main()
