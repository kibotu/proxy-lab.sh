"""Proxy auto-config for iOS proxy mode: only dev hosts reach mitmproxy."""

from __future__ import annotations

import json
import sys

from proxy_lab.config import load_domains


def _pattern(suffix: str) -> str:
    # Suffix semantics match matches_host: ".acme.dev" is a literal suffix.
    return "*" + suffix


def build_pac(suffixes: tuple[str, ...], hosts: list[str], proxy: str) -> str:
    """Return PAC text sending the given hosts via *proxy*, everything else DIRECT.

    The proxy entry falls back to DIRECT, so a dead proxy never takes the Mac offline.
    """

    patterns = [_pattern(s) for s in suffixes] + [h.casefold() for h in hosts if h]
    patterns = list(dict.fromkeys(patterns))
    return (
        "function FindProxyForURL(url, host) {\n"
        "  host = host.toLowerCase();\n"
        f"  var patterns = {json.dumps(patterns)};\n"
        "  for (var i = 0; i < patterns.length; i++) {\n"
        "    if (shExpMatch(host, patterns[i])) {\n"
        f'      return "PROXY {proxy}; DIRECT";\n'
        "    }\n"
        "  }\n"
        '  return "DIRECT";\n'
        "}\n"
    )


def main(argv: list[str]) -> int:
    """`python -m proxy_lab.pac CONFIG PROXY` with extra hosts on stdin."""

    config, proxy = argv
    hosts = [line.strip() for line in sys.stdin if line.strip()]
    sys.stdout.write(build_pac(load_domains(config), hosts, proxy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
