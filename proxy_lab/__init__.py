"""Package metadata for proxy-lab."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


try:
    __version__ = version("proxy-lab")
except PackageNotFoundError:
    # Source-tree invocations do not necessarily install package metadata.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        import tomllib

        with pyproject.open("rb") as file:
            __version__ = tomllib.load(file)["project"]["version"]
    except (KeyError, OSError, ImportError):
        try:
            import re

            match = re.search(
                r'^version\s*=\s*"([^"]+)"',
                pyproject.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            __version__ = match.group(1) if match else "unknown"
        except (OSError, AttributeError):
            __version__ = "unknown"
