import unittest

from proxy_lab.pac import build_pac


class PacTests(unittest.TestCase):
    def test_only_dev_hosts_are_proxied(self) -> None:
        pac = build_pac((".example.com",), ["Dev.Local"], "127.0.0.1:8080")
        self.assertIn('"*.example.com"', pac)
        self.assertIn('"dev.local"', pac)
        self.assertIn('"PROXY 127.0.0.1:8080; DIRECT"', pac)
        self.assertTrue(pac.rstrip().endswith('return "DIRECT";\n}'))


if __name__ == "__main__":
    unittest.main()
