"""Add a response header to requests for one host."""

from mitmproxy import http


TARGET_HOST = "api.example.com"


def response(flow: http.HTTPFlow) -> None:
    if flow.request.pretty_host == TARGET_HOST and flow.response is not None:
        flow.response.headers["X-Proxy-Lab-Example"] = "1"
