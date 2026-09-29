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

    def test_loads_config_and_redacts_matching_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory))
            router.load(None)
            request = types.SimpleNamespace(
                pretty_host="api.example.com",
                scheme="https",
                path="/v1?token=secret&page=2",
            )
            flow = types.SimpleNamespace(request=request)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.request(flow)
            self.assertIn("api.example.com/v1", output.getvalue())
            self.assertNotIn("secret", output.getvalue())

    def test_invalid_config_fails_during_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domains.yml"
            config.write_text("domains: wrong\n", encoding="utf-8")
            router = self.load_router(config)
            with self.assertRaises(RuntimeError):
                router.load(None)

    def make_flow(self) -> types.SimpleNamespace:
        headers = types.SimpleNamespace(items=lambda multi=False: iter(()))
        request = types.SimpleNamespace(
            pretty_host="api.example.com",
            scheme="https",
            path="/v1?token=secret",
            method="POST",
            http_version="HTTP/1.1",
            headers=headers,
            raw_content=b"{}",
            timestamp_start=1_700_000_000.0,
        )
        response_headers = types.SimpleNamespace(
            items=lambda multi=False: iter([("Content-Type", "application/json")])
        )
        return types.SimpleNamespace(
            request=request,
            response=types.SimpleNamespace(
                status_code=201,
                headers=response_headers,
                raw_content=b'{"ok":true}',
                timestamp_end=1_700_000_000.25,
            ),
            error=None,
        )

    def test_jsonl_emits_a_parseable_record_per_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory), "jsonl")
            router.load(None)
            flow = self.make_flow()
            flow.response = None  # in-flight: no status yet
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.request(flow)

            record = json.loads(output.getvalue())
            self.assertEqual(record["method"], "POST")
            self.assertEqual(record["url"], "https://api.example.com/v1?token=%3Cr%3E")
            self.assertIsNone(record["status_code"])
            self.assertEqual(record["request_body_bytes"], 2)

    def test_jsonl_response_adds_status_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory), "jsonl")
            router.load(None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.response(self.make_flow())

            record = json.loads(output.getvalue())
            self.assertEqual(record["status_code"], 201)
            self.assertEqual(record["duration_ms"], 250.0)
            self.assertEqual(
                record["response_headers"], {"Content-Type": "application/json"}
            )
            self.assertEqual(record["response_body_bytes"], 11)

    def test_jsonl_redacts_authorization_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory), "jsonl")
            router.load(None)
            flow = self.make_flow()
            flow.request.headers = types.SimpleNamespace(
                items=lambda multi=False: iter(
                    [("Authorization", "Bearer hunter2"), ("Accept", "*/*")]
                )
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.request(flow)

            printed = output.getvalue()
            self.assertNotIn("hunter2", printed)
            self.assertEqual(
                json.loads(printed)["request_headers"],
                {"Authorization": "<r>", "Accept": "*/*"},
            )

    def test_text_mode_response_adds_no_output(self) -> None:
        # Text mode stays request-only so terminal output keeps its shape.
        with tempfile.TemporaryDirectory() as directory:
            router = self.load_router(self.config_file(directory), "text")
            router.load(None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                router.response(self.make_flow())
            self.assertEqual(output.getvalue(), "")

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
