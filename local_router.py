# Shared by android/ and ios/ — loads the domain list from domains.yaml
# once at startup. Edit domains.yaml, not this file.
import os
from pathlib import Path

import ruamel.yaml
from mitmproxy import http

# PROXY_LAB_CONFIG is set by the proxy-lab CLI when a domains file is passed;
# without it, the domains.yaml next to this file (repo checkout or package).
CONFIG = Path(os.environ.get("PROXY_LAB_CONFIG") or Path(__file__).with_name("domains.yaml"))

with CONFIG.open() as fh:
    _config = ruamel.yaml.YAML(typ="safe").load(fh) or {}
LOCAL_DOMAIN_SUFFIXES = tuple(_config["domains"])


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host

    if not any(host.endswith(s) for s in LOCAL_DOMAIN_SUFFIXES):
        return

    # flush: this marker must be visible the moment the request happens —
    # stdout is block-buffered when redirected (CI logs, | grep pipelines).
    print(f"[local_router] {flow.request.scheme}://{host}{flow.request.path}", flush=True)
