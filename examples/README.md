# proxy-lab addons

These examples are loaded after `local_router.py` when passed with
`--script`:

```bash
uvx proxy-lab start android --script examples/mock_response.py
```

Scripts run in the order supplied. A script can inspect or modify traffic, so
only load code you trust.

- `modify_response.py` — add a response header for one host.
- `mock_response.py` — return a small JSON response for one path.
- `block_domain.py` — block requests to one host with a 403 response.
