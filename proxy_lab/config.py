"""Strict configuration loading and host matching for proxy-lab."""

from __future__ import annotations

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


def matches_host(host: str, suffixes: tuple[str, ...]) -> bool:
    """Return whether *host* matches one of the configured literal suffixes."""

    normalized_host = normalize_host(host)
    return any(normalized_host.endswith(suffix) for suffix in suffixes)


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
