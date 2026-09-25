"""Block one host with a small 403 response."""

from mitmproxy import http


BLOCKED_HOST = "blocked.example.com"


def request(flow: http.HTTPFlow) -> None:
    if flow.request.pretty_host == BLOCKED_HOST:
        flow.response = http.Response.make(403, b"Blocked by proxy-lab example\n")
