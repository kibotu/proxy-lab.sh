# Shared by the Android and iOS launchers. Load domains once at startup;
# edit domains.yaml, not this file.
import os
from pathlib import Path

import ruamel.yaml
from mitmproxy import http

# The CLI sets PROXY_LAB_CONFIG when a domains file is supplied; otherwise use
# the domains.yaml next to this file (in a checkout or installed package).
CONFIG = Path(os.environ.get("PROXY_LAB_CONFIG") or Path(__file__).with_name("domains.yaml"))

with CONFIG.open() as fh:
    _config = ruamel.yaml.YAML(typ="safe").load(fh) or {}
LOCAL_DOMAIN_SUFFIXES = tuple(_config["domains"])


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host

    # This is a literal suffix match; use a leading dot for domain boundaries.
    if not any(host.endswith(s) for s in LOCAL_DOMAIN_SUFFIXES):
        return

    # Flush because redirected stdout is block-buffered (CI logs, grep pipelines).
    print(f"[local_router] {flow.request.scheme}://{host}{flow.request.path}", flush=True)
