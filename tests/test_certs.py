import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from proxy_lab import certs


LEAF = "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----"
CA = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"


class ServerChainTests(unittest.TestCase):
    def test_the_chain_is_read_from_what_the_server_sends(self) -> None:
        output = f"CONNECTED\n--- Certificate chain\n{LEAF}{CA}\n"
        with mock.patch.object(certs, "_openssl", return_value=output):
            self.assertEqual(certs.server_chain("dev.local"), [LEAF, CA])

    def test_a_server_that_answers_nothing_has_no_chain(self) -> None:
        with mock.patch.object(certs, "_openssl", return_value=""):
            self.assertEqual(certs.server_chain("dev.local"), [])


class IssuerTests(unittest.TestCase):
    def test_the_common_name_is_read_from_the_openssl_format(self) -> None:
        output = "issuer=C=DE, ST=Berlin, O=CHECK24, CN=check24-dev.de\n"
        with mock.patch.object(certs, "_openssl", return_value=output):
            self.assertEqual(certs.issuer_common_name(LEAF), "check24-dev.de")

    def test_the_common_name_is_read_from_the_libressl_format(self) -> None:
        output = "issuer=/C=DE/ST=Berlin/O=CHECK24/CN=check24-dev.de\n"
        with mock.patch.object(certs, "_openssl", return_value=output):
            self.assertEqual(certs.issuer_common_name(LEAF), "check24-dev.de")

    def test_a_certificate_without_a_common_name_has_none(self) -> None:
        output = "issuer=C=DE, ST=Berlin, O=CHECK24\n"
        with mock.patch.object(certs, "_openssl", return_value=output):
            self.assertEqual(certs.issuer_common_name(LEAF), "")


class VerifiesTests(unittest.TestCase):
    def test_openssl_confirms_the_signature(self) -> None:
        with mock.patch.object(certs, "_openssl", return_value="leaf.pem: OK\n"):
            self.assertTrue(certs.verifies(CA, LEAF))

    def test_openssl_refuses_the_signature(self) -> None:
        with mock.patch.object(certs, "_openssl", return_value="leaf.pem: FAIL\n"):
            self.assertFalse(certs.verifies(CA, LEAF))


class TrustAnchorTests(unittest.TestCase):
    def test_the_chain_ends_with_the_certificate_that_signed_the_leaf(self) -> None:
        with mock.patch.object(certs, "server_chain", return_value=[LEAF, CA]), mock.patch.object(
            certs, "keychain_certificates", return_value=[]
        ), mock.patch.object(certs, "verifies", return_value=True):
            self.assertEqual(certs.trust_anchor("dev.local"), CA)

    def test_a_root_comes_from_the_keychain_the_mac_already_trusts(self) -> None:
        # A root is never sent by a server, so the chain is the leaf alone.
        with mock.patch.object(certs, "server_chain", return_value=[LEAF]), mock.patch.object(
            certs, "keychain_certificates", return_value=[CA]
        ), mock.patch.object(certs, "verifies", return_value=True):
            self.assertEqual(certs.trust_anchor("dev.local"), CA)

    def test_a_certificate_that_signed_nothing_is_not_trusted(self) -> None:
        with mock.patch.object(certs, "server_chain", return_value=[LEAF]), mock.patch.object(
            certs, "keychain_certificates", return_value=[CA]
        ), mock.patch.object(certs, "verifies", return_value=False):
            self.assertIsNone(certs.trust_anchor("dev.local"))

    def test_a_server_that_serves_nothing_has_no_anchor(self) -> None:
        with mock.patch.object(certs, "server_chain", return_value=[]):
            self.assertIsNone(certs.trust_anchor("dev.local"))


class MainTests(unittest.TestCase):
    def test_the_ca_is_written_where_the_caller_asked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(certs, "local_cas", return_value={"dev.local": CA}), mock.patch.object(
                certs, "_openssl", return_value="subject=C=DE, O=CHECK24, CN=check24-dev.de\n"
            ):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(certs.main(["--out-dir", directory, "dev.local"]), 0)
            written = (Path(directory) / "dev.local.pem").read_text(encoding="utf-8")
            self.assertEqual(written, CA + "\n")
            self.assertEqual(
                output.getvalue().strip(),
                f"check24-dev.de\t{Path(directory) / 'dev.local.pem'}\tdev.local",
            )

    def test_one_ca_is_written_once_for_all_the_hosts_it_covers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hosts = ["one.local", "two.local", "three.local", "four.local"]
            with mock.patch.object(
                certs, "local_cas", return_value=dict.fromkeys(hosts, CA)
            ), mock.patch.object(certs, "_openssl", return_value="subject=CN=check24-dev.de\n"):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(certs.main(["--out-dir", directory, *hosts]), 0)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertIn("4 hosts", output.getvalue())

    def test_a_host_without_a_ca_is_left_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(certs, "local_cas", return_value={}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(certs.main(["--out-dir", directory, "dev.local"]), 0)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
