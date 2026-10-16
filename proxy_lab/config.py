"""Strict configuration loading and host matching for proxy-lab."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import ruamel.yaml


class ConfigError(ValueError):
    """Raised when a domain configuration is invalid."""


_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "client_secret",
        "code",
        "csrf",
        "csrf_token",
        "guid",
        "id_token",
        "key",
        "nonce",
        "oauth_token",
        "password",
        "push_token",
        "refresh_token",
        "secret",
        "session",
        "sig",
        "signature",
        "state",
        "token",
    }
)

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "www-authenticate",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)


def _ascii_host(value: str) -> str:
    value = value.strip().rstrip(".").casefold()
    if not value:
        return value
    try:
        return value.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return value


def normalize_host(host: str) -> str:
    """Return a comparable hostname without a trailing DNS dot."""

    return _ascii_host(host)


def normalize_suffix(suffix: str) -> str:
    """Return a comparable domain suffix while preserving dot semantics."""

    return _ascii_host(suffix)


def load_domains(path: str | Path) -> tuple[str, ...]:
    """Load and validate the configured domain suffixes.

    A leading dot retains its documented meaning: ``.example.com`` matches
    subdomains but not the apex. A suffix without a leading dot remains a
    literal suffix match.
    """

    config_path = Path(path).expanduser()
    try:
        with config_path.open(encoding="utf-8") as file:
            data: Any = ruamel.yaml.YAML(typ="safe").load(file)
    except OSError as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
    except UnicodeError as exc:
        raise ConfigError(f"config {config_path} is not valid UTF-8: {exc}") from exc
    except ruamel.yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{config_path}: top-level value must be a mapping")

    domains = data.get("domains")
    if domains is None:
        raise ConfigError(f"{config_path}: missing required 'domains' list")
    if not isinstance(domains, list):
        raise ConfigError(f"{config_path}: 'domains' must be a list")

    normalized: list[str] = []
    for index, suffix in enumerate(domains):
        if not isinstance(suffix, str) or not suffix.strip():
            raise ConfigError(
                f"{config_path}: domains[{index}] must be a non-empty string"
            )
        normalized_suffix = normalize_suffix(suffix)
        if not normalized_suffix:
            raise ConfigError(
                f"{config_path}: domains[{index}] must contain a hostname"
            )
        normalized.append(normalized_suffix)
    return tuple(normalized)


def matches_host(
    host: str, suffixes: tuple[str, ...], exact: tuple[str, ...] = ()
) -> bool:
    """Return whether *host* is one of *exact* or ends with one of *suffixes*."""

    normalized_host = normalize_host(host)
    return normalized_host in exact or any(
        normalized_host.endswith(suffix) for suffix in suffixes
    )


# Names /etc/hosts carries on every Mac that are not anyone's dev host.
_STOCK_HOST_NAMES = frozenset({"localhost", "broadcasthost"})


def hosts_file_names(path: str | Path = "/etc/hosts") -> tuple[str, ...]:
    """Return the host names a hosts file defines, minus the stock entries.

    These are the names a developer pointed somewhere on purpose, which makes
    them the right default for what proxy-lab should capture. An unreadable
    file yields no names.
    """

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    names: dict[str, None] = {}
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        for name in fields[1:]:
            normalized = normalize_host(name)
            if (
                normalized
                and normalized not in _STOCK_HOST_NAMES
                and not normalized.startswith("ip6-")
            ):
                names[normalized] = None
    return tuple(names)


def allow_host_patterns(
    suffixes: tuple[str, ...], exact: tuple[str, ...] = ()
) -> list[str]:
    """Return mitmproxy ``allow_hosts`` regexes equivalent to :func:`matches_host`.

    mitmproxy matches these against ``host:port``. Anything that matches none
    of them is passed through untouched instead of being intercepted.
    """

    patterns = [re.escape(suffix) + r":\d+$" for suffix in suffixes]
    patterns += ["^" + re.escape(name) + r":\d+$" for name in exact]
    return patterns


def _is_sensitive_query_key(name: str) -> bool:
    key = name.casefold()
    return key in _SENSITIVE_QUERY_KEYS or any(
        part in key for part in ("password", "secret", "token", "session")
    )


def redact_url(url: str) -> str:
    """Redact common credential-bearing query parameters from a URL."""

    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted = [
        (key, "<r>" if _is_sensitive_query_key(key) else value)
        for key, value in pairs
    ]
    return urlunsplit(parts._replace(query=urlencode(redacted)))


def _is_sensitive_header(name: str) -> bool:
    key = name.casefold()
    return key in _SENSITIVE_HEADERS or any(
        part in key for part in ("authorization", "api-key", "apikey", "secret", "session")
    )


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact credential-bearing header values, preserving header names.

    Header names are safe to log and carry most of the debugging value; the
    values are where bearer tokens and cookies live.
    """

    return {
        name: "<r>" if _is_sensitive_header(name) else value
        for name, value in headers.items()
    }
