from __future__ import annotations

import re
import unittest
from pathlib import Path

from proxy_lab import __version__


class MetadataTests(unittest.TestCase):
    def test_checked_in_version_matches_package_version(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        match = re.search(
            r'^version\s*=\s*"([^"]+)"',
            pyproject.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(__version__, match.group(1))


if __name__ == "__main__":
    unittest.main()
