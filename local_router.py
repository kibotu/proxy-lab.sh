"""Mitmproxy addon that logs configured hosts without storing traffic."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mitmproxy import ctx, http

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from proxy_lab.config import ConfigError, load_domains, matches_host, redact_url

CONFIG = Path(
    os.environ.get("PROXY_LAB_CONFIG")
    or Path(__file__).with_name("domains.yaml")
).expanduser()
_LOCAL_DOMAIN_SUFFIXES: tuple[str, ...] = ()


def load(loader) -> None:
    """Load and validate the domain configuration when mitmproxy starts."""

    del loader  # mitmproxy supplies this hook argument; the addon has no options.
    global _LOCAL_DOMAIN_SUFFIXES
    try:
        _LOCAL_DOMAIN_SUFFIXES = load_domains(CONFIG)
    except ConfigError as exc:
        ctx.log.error(f"proxy-lab configuration error: {exc}")
        raise RuntimeError(str(exc)) from exc
    ctx.log.info(f"proxy-lab domain filter: {len(_LOCAL_DOMAIN_SUFFIXES)} suffix(es)")


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    if not matches_host(host, _LOCAL_DOMAIN_SUFFIXES):
        return

    url = redact_url(
        f"{flow.request.scheme}://{host}{flow.request.path}"
    )
    # Flush because redirected stdout is block-buffered (CI logs, grep pipelines).
    print(f"[local_router] {url}", flush=True)
