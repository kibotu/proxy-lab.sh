from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from proxy_lab.config import ConfigError, load_domains, matches_host, redact_url


class ConfigTests(unittest.TestCase):
    def write_config(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "domains.yml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_and_normalises_suffixes(self) -> None:
        path = self.write_config("domains:\n  - '.Example.COM.'\n  - api.example.net\n")
        self.assertEqual(
            load_domains(path), (".example.com", "api.example.net")
        )

    def test_literal_suffix_semantics(self) -> None:
        suffixes = (".example.com", "acme.dev")
        self.assertTrue(matches_host("api.example.com", suffixes))
        self.assertFalse(matches_host("example.com", suffixes))
        self.assertTrue(matches_host("notacme.dev", suffixes))
        self.assertTrue(matches_host("API.EXAMPLE.COM.", suffixes))

    def test_rejects_missing_or_wrong_domains_shape(self) -> None:
        for text in (
            "other: value\n",
            "domains: example.com\n",
            "domains:\n  - ''\n",
            "domains:\n  - '.'\n",
            "domains:\n  - 42\n",
        ):
            with self.subTest(text=text):
                path = self.write_config(text)
                with self.assertRaises(ConfigError):
                    load_domains(path)

    def test_redacts_common_query_credentials(self) -> None:
        self.assertEqual(
            redact_url("https://example.com/a?token=secret&page=2"),
            "https://example.com/a?token=%3Cr%3E&page=2",
        )


if __name__ == "__main__":
    unittest.main()
