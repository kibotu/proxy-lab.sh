"""Return a deterministic JSON response for one API path."""

import json

from mitmproxy import http


TARGET_HOST = "api.example.com"
TARGET_PATH = "/v1/health"


def request(flow: http.HTTPFlow) -> None:
    if (
        flow.request.pretty_host == TARGET_HOST
        and flow.request.path.split("?", 1)[0] == TARGET_PATH
    ):
        flow.response = http.Response.make(
            200,
            json.dumps({"status": "ok"}).encode(),
            {"Content-Type": "application/json"},
        )
