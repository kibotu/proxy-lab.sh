from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from proxy_lab import __version__

ROOT = Path(__file__).resolve().parents[1]


class MetadataTests(unittest.TestCase):
    def test_version_is_resolved(self) -> None:
        # "unknown" means neither installed metadata nor the hatch-vcs version
        # file was found, and --version would print nothing useful.
        self.assertNotEqual(__version__, "unknown")

    def test_version_matches_the_tag(self) -> None:
        # Only meaningful on a clean release commit, which is where a wrong
        # version would actually ship. A dirty tree is deliberately a dev
        # version, so it is not this test's business.
        described = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "--dirty"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        tag = described.stdout.strip()
        if described.returncode != 0:
            self.skipTest("HEAD is not an exact tag")
        if tag.endswith("-dirty"):
            self.skipTest(f"working tree is dirty, {tag}")
        self.assertEqual(__version__, tag)


if __name__ == "__main__":
    unittest.main()
