from __future__ import annotations

import contextlib
import importlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class LocalRouterTests(unittest.TestCase):
    def load_router(self, config: Path):
        mitmproxy = types.ModuleType("mitmproxy")
        mitmproxy.ctx = types.SimpleNamespace(log=Mock())
        mitmproxy.http = types.SimpleNamespace(HTTPFlow=object)
        with patch.dict(sys.modules, {"mitmproxy": mitmproxy}):
            with patch.dict("os.environ", {"PROXY_LAB_CONFIG": str(config)}):
                import local_router

                return importlib.reload(local_router)

    def test_loads_config_and_redacts_matching_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domains.yml"
            config.write_text("domains:\n  - '.example.com'\n", encoding="utf-8")
            router = self.load_router(config)
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


if __name__ == "__main__":
    unittest.main()
