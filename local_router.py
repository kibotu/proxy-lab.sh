"""Mitmproxy addon that logs configured hosts without storing traffic."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mitmproxy import ctx, http

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from proxy_lab.config import (  # noqa: E402
    ConfigError,
    load_domains,
    matches_host,
    redact_headers,
    redact_url,
)

CONFIG = Path(
    os.environ.get("PROXY_LAB_CONFIG")
    or Path(__file__).with_name("domains.yaml")
).expanduser()
LOG_FORMAT = os.environ.get("PROXY_LAB_LOG_FORMAT") or "text"
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
    ctx.log.info(
        f"proxy-lab domain filter: {len(_LOCAL_DOMAIN_SUFFIXES)} suffix(es) "
        f"[log-format: {LOG_FORMAT}]"
    )


def _iso_timestamp(value) -> str | None:
    """Format a mitmproxy epoch timestamp as ISO-8601 UTC, or None."""

    if value is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _duration_ms(flow: http.HTTPFlow) -> float | None:
    start = getattr(flow.request, "timestamp_start", None)
    end = getattr(flow.response, "timestamp_end", None) if flow.response else None
    if start is None or end is None:
        return None
    return round((end - start) * 1000, 1)


def _header_map(headers) -> dict[str, str]:
    """Collapse mitmproxy's multi-valued header pairs into a redacted mapping."""

    collected: dict[str, str] = {}
    try:
        pairs = headers.items(multi=True)
    except TypeError:  # a plain mapping, as used in tests
        pairs = headers.items()
    for name, value in pairs:
        redacted = redact_headers({name: value})
        rendered = redacted.get(name, value)
        # Repeated headers join rather than overwrite, so nothing is dropped.
        collected[name] = f"{collected[name]}, {rendered}" if name in collected else rendered
    return collected


def _jsonl_record(flow: http.HTTPFlow) -> dict[str, object]:
    """Build the machine-readable record for one flow."""

    request = flow.request
    url = redact_url(f"{request.scheme}://{request.pretty_host}{request.path}")
    record: dict[str, object] = {
        "host": request.pretty_host,
        "method": request.method,
        "url": url,
        "http_version": request.http_version,
        "request_headers": _header_map(request.headers),
    }

    body = getattr(request, "raw_content", b"") or b""
    if body:
        record["request_body_bytes"] = len(body)

    if flow.response is not None:
        response = flow.response
        record["status_code"] = response.status_code
        record["response_headers"] = _header_map(response.headers)
        response_body = getattr(response, "raw_content", b"") or b""
        if response_body:
            record["response_body_bytes"] = len(response_body)
    else:
        # No response yet: this is the request-side event for an in-flight flow.
        record["status_code"] = None

    started = getattr(request, "timestamp_start", None)
    if started is not None:
        record["timestamp_start"] = _iso_timestamp(started)
    duration = _duration_ms(flow)
    if duration is not None:
        record["duration_ms"] = duration

    error = getattr(flow, "error", None)
    if error is not None:
        record["error"] = getattr(error, "msg", None) or str(error)

    return record


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host
    if not matches_host(host, _LOCAL_DOMAIN_SUFFIXES):
        return

    if LOG_FORMAT == "jsonl":
        # One JSON object per line, flushed: agents pipe this straight into jq.
        print(json.dumps(_jsonl_record(flow)), flush=True)
        return

    url = redact_url(f"{flow.request.scheme}://{host}{flow.request.path}")
    # Flush because redirected stdout is block-buffered (CI logs, grep pipelines).
    print(f"[local_router] {url}", flush=True)


def response(flow: http.HTTPFlow) -> None:
    """Emit the completed flow, including status and response headers.

    Text mode stays request-only so the terminal output keeps its original
    shape; jsonl mode gains the status code an agent actually needs.
    """

    if LOG_FORMAT != "jsonl":
        return
    if not matches_host(flow.request.pretty_host, _LOCAL_DOMAIN_SUFFIXES):
        return
    print(json.dumps(_jsonl_record(flow)), flush=True)
