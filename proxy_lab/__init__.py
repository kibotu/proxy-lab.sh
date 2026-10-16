"""Package metadata for proxy-lab."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("proxy-lab")
except PackageNotFoundError:
    # A source tree is not always installed. hatch-vcs writes _version.py on
    # every build, editable included, so that is the next best answer. There is
    # nothing in the tree to read when neither is present, because the tag is
    # the only place the version lives.
    try:
        from ._version import __version__
    except ImportError:
        __version__ = "unknown"
