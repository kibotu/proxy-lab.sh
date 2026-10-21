"""The certificate a local development server presents, and the CA behind it.

A Simulator only loads a local server's page when it trusts that server's
certificate. A private CA — a Docker nginx with a CA of its own, the usual
case for local development — is not trusted by default: the app reports an
invalid certificate, the request dies during the TLS handshake, and the
server never sees it.

The CA does not have to be known in advance, and it does not have to be
configured. A server sends the chain it was given, and the chain ends with
the certificate that signed the leaf. A root is never sent, so when the
server signs its leaf with one, the CA comes from the place that already
trusts it: the Mac's own keychain. Whoever browses the local site in Chrome
has already installed it there.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PEM_CERTIFICATE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)

# A file name that is safe to build from a host name.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def _run(binary: str, *args: str, stdin: str = "", timeout: float = 5.0) -> str:
    """Run a command and return its output. Empty when it is missing or fails."""

    path = shutil.which(binary)
    if path is None:
        return ""
    try:
        result = subprocess.run(
            [path, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def _openssl(*args: str, stdin: str = "", timeout: float = 5.0) -> str:
    """Run openssl and return its output."""

    return _run("openssl", *args, stdin=stdin, timeout=timeout)


def server_chain(host: str, port: int = 443, timeout: float = 5.0) -> list[str]:
    """The certificate chain a server sends, leaf first.

    The host name is resolved the way the platform resolves it, so a name
    that /etc/hosts points at a local server reaches that server.
    """

    output = _openssl(
        "s_client",
        "-connect",
        f"{host}:{port}",
        "-servername",
        host,
        "-showcerts",
        timeout=timeout,
    )
    return PEM_CERTIFICATE.findall(output)


def issuer_common_name(pem: str) -> str:
    """The common name of the issuer of a certificate."""

    return _common_name(pem, "issuer")


def common_name(pem: str) -> str:
    """The common name of a certificate."""

    return _common_name(pem, "subject")


def _common_name(pem: str, field: str) -> str:
    line = next(
        (
            line
            for line in _openssl("x509", "-noout", f"-{field}", stdin=pem).splitlines()
            if line.strip().startswith(f"{field}=")
        ),
        "",
    )
    if "=" not in line:
        return ""
    value = line.split("=", 1)[1]
    index = value.rfind("CN=")
    if index < 0:
        return ""
    name = value[index + len("CN=") :]
    for stop in (",", "/"):
        name = name.split(stop)[0]
    return name.strip()


def keychain_certificates(common_name: str) -> list[str]:
    """The certificates in the Mac's keychain that carry a common name."""

    if not common_name:
        return []
    output = _run("security", "find-certificate", "-a", "-c", common_name, "-p")
    return PEM_CERTIFICATE.findall(output)


def verifies(ca_pem: str, leaf_pem: str) -> bool:
    """Whether *ca_pem* signed *leaf_pem*."""

    with tempfile.TemporaryDirectory() as directory:
        ca = Path(directory) / "ca.pem"
        leaf = Path(directory) / "leaf.pem"
        ca.write_text(ca_pem, encoding="utf-8")
        leaf.write_text(leaf_pem, encoding="utf-8")
        output = _openssl("verify", "-CAfile", str(ca), str(leaf))
    return output.strip().endswith(": OK")


def trust_anchor(host: str, port: int = 443, timeout: float = 5.0) -> str | None:
    """The certificate to install in a client's trust store for *host*.

    The chain the server sends comes first: a server that was given its
    intermediates sends them, and the topmost one signs the leaf. A root is
    never sent, so the certificate that signed the leaf is looked up in the
    Mac's keychain — the place that already trusts it, because the local
    site loads in a browser there.
    """

    chain = server_chain(host, port, timeout)
    if not chain:
        return None
    leaf = chain[0]
    candidates = list(chain[1:])
    candidates += keychain_certificates(issuer_common_name(leaf))
    for candidate in candidates:
        if verifies(candidate, leaf):
            return candidate
    return None


def local_cas(
    hosts: list[str], port: int = 443, timeout: float = 5.0
) -> dict[str, str]:
    """The CA to trust for each host whose server presents one.

    The hosts are asked at the same time: a server that does not answer must
    not hold up the ones that do.
    """

    if not hosts:
        return {}
    found: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(hosts))) as pool:
        anchors = pool.map(lambda host: trust_anchor(host, port, timeout), hosts)
        for host, anchor in zip(hosts, anchors):
            if anchor is not None:
                found[host] = anchor
    return found


def _write(directory: Path, host: str, pem: str) -> Path:
    path = directory / f"{_UNSAFE_NAME.sub('_', host)}.pem"
    path.write_text(pem if pem.endswith("\n") else pem + "\n", encoding="utf-8")
    return path


def _summary(hosts: list[str]) -> str:
    """What to tell about the hosts a CA covers."""

    if len(hosts) <= 3:
        return ", ".join(hosts)
    return f"{len(hosts)} hosts"


def main(argv: list[str]) -> int:
    """`python -m proxy_lab.certs --out-dir DIR host [host ...]`.

    Writes the CA of every host that serves one into *--out-dir* and prints
    one line per CA: "common name", path, and the hosts it covers. A host
    without a CA of its own is left out.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="where to write the CA files")
    parser.add_argument(
        "--port", type=int, default=443, help="port to ask (default: 443)"
    )
    parser.add_argument("hosts", nargs="+", help="host names to ask")
    args = parser.parse_args(argv)

    directory = Path(args.out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    covered: dict[str, list[str]] = {}
    for host, pem in local_cas(args.hosts, args.port).items():
        covered.setdefault(pem, []).append(host)

    for pem, hosts in covered.items():
        path = _write(directory, hosts[0], pem)
        print(f"{common_name(pem) or 'the CA'}\t{path}\t{_summary(hosts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
