from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxy_lab.cli import main


def _posix(path: str) -> str:
    return path.replace("\\", "/")


class CliTests(unittest.TestCase):
    def test_json_is_agent_mode(self) -> None:
        with patch("proxy_lab.cli.os.execvpe") as execvpe:
            self.assertEqual(main(["start", "android", "--json"]), 0)
        _, argv, env = execvpe.call_args.args
        self.assertTrue(_posix(argv[1]).endswith("android/start-proxy.sh"))
        self.assertEqual(env["DETACH"], "1")
        self.assertEqual(env["JSON_OUTPUT"], "1")
        self.assertEqual(env["PROXY_LAB_LOG_FORMAT"], "jsonl")
        self.assertIn("--detach", argv)
        self.assertIn("--json", argv)
        self.assertIn("--log-format", argv)
        self.assertIn("jsonl", argv)

    def test_start_rejects_invalid_config_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domains.yml"
            config.write_text("domains: wrong\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                main(["start", "ios", str(config)])
            self.assertEqual(raised.exception.code, 2)

    def test_init_android_finds_the_project_and_writes_the_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app" / "src" / "debug" / "res").mkdir(parents=True)
            nested = root / "app" / "src" / "debug" / "java"
            nested.mkdir(parents=True)
            self.assertEqual(main(["init", "android", "--path", str(nested)]), 0)
            written = (
                root
                / "app"
                / "src"
                / "debug"
                / "res"
                / "xml"
                / "network_security_config.xml"
            )
            self.assertIn('<certificates src="user" />', written.read_text())

    def test_init_reports_a_missing_resource_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["init", "android", "--path", directory]), 3)


if __name__ == "__main__":
    unittest.main()
