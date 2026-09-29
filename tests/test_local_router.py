from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class LocalRouterTests(unittest.TestCase):
    def load_router(self, config: Path, log_format: str = "text"):
        mitmproxy = types.ModuleType("mitmproxy")
        mitmproxy.ctx = types.SimpleNamespace(log=Mock())
        mitmproxy.http = types.SimpleNamespace(HTTPFlow=object)
        with patch.dict(sys.modules, {"mitmproxy": mitmproxy}):
            with patch.dict(
                "os.environ",
                {
                    "PROXY_LAB_CONFIG": str(config),
                    "PROXY_LAB_LOG_FORMAT": log_format,
                },
            ):
                import local_router

                return importlib.reload(local_router)

    def config_file(self, directory: str) -> Path:
        config = Path(directory) / "domains.yml"
        config.write_text("domains:\n  - '.example.com'\n", encoding="utf-8")
        return config

    def make_flow(self) -> types.SimpleNamespace:
        request = types.SimpleNamespace(
            pretty_host="api.example.com",
            scheme="https",
            path="/v1?token=secret",
            method="POST",
            http_version="HTTP/1.1",
            headers=types.SimpleNamespace(
                items=lambda multi=False: iter(
                    [("Authorization", "Bearer hunter2"), ("Accept", "*/*")]
                )
            ),
            raw_content=b"{}",
            timestamp_start=1_700_000_000.0,
        )
        return types.SimpleNamespace(
            request=request,
            response=types.SimpleNamespace(
                status_code=201,
                headers=types.SimpleNamespace(
                    items=lambda multi=False: iter([("Content-Type", "application/json")])
                ),
                raw_content=b'{"ok":true}',
                timestamp_end=1_700_000_000.25,
            ),
            error=None,
        )

    def test_text_mode_logs_matching_hosts_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory))
            router.load(None)
            flow = self.make_flow()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.request(flow)
                router.response(flow)
            printed = output.getvalue()
            self.assertIn("api.example.com/v1", printed)
            self.assertNotIn("secret", printed)
            self.assertNotIn("201", printed)

    def test_jsonl_is_the_agent_traffic_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory), "jsonl")
            router.load(None)
            flow = self.make_flow()
            request_flow = types.SimpleNamespace(request=flow.request, response=None, error=None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.request(request_flow)
                router.response(flow)
            lines = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(lines[0]["status_code"], None)
            self.assertEqual(lines[1]["status_code"], 201)
            self.assertEqual(lines[1]["duration_ms"], 250.0)
            self.assertNotIn("hunter2", output.getvalue())
            self.assertEqual(lines[0]["request_headers"]["Authorization"], "<r>")

    def test_invalid_config_fails_during_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domains.yml"
            config.write_text("domains: wrong\n", encoding="utf-8")
            router = self.load_router(config)
            with self.assertRaises(RuntimeError):
                router.load(None)

    def test_non_matching_host_is_never_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory), "jsonl")
            router.load(None)
            flow = self.make_flow()
            flow.request.pretty_host = "api.other.test"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.request(flow)
                router.response(flow)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
