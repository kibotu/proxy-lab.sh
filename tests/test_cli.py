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


if __name__ == "__main__":
    unittest.main()
