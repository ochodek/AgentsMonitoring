"""Audit 2026-09-08: the dashboard must not depend on a third-party script and must refuse to
listen off-loopback without auth."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentsmon import dashboard  # noqa: E402


class Hardening(unittest.TestCase):
    def test_page_loads_tailwind_from_the_package_not_a_cdn(self):
        self.assertIn('src="/static/tailwind.js"', dashboard.PAGE)
        self.assertNotIn("cdn.tailwindcss.com", dashboard.PAGE)
        self.assertGreater(len(dashboard._STATIC_TAILWIND), 100_000)
        self.assertTrue(dashboard._STATIC_TAILWIND.lstrip().startswith(b"(()=>") or b"tailwind" in dashboard._STATIC_TAILWIND[:2000].lower())

    def test_off_loopback_without_auth_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            dashboard.require_auth_or_die("100.75.92.58", {"dashboard": {}})
        with self.assertRaises(SystemExit):
            dashboard.require_auth_or_die("0.0.0.0", {"dashboard": {"auth": {"user": "x"}}})

    def test_loopback_or_configured_auth_is_fine(self):
        dashboard.require_auth_or_die("127.0.0.1", {"dashboard": {}})
        dashboard.require_auth_or_die("100.75.92.58", {"dashboard": {"auth": {"user": "lana", "pwhash": "ab" * 32}}})


if __name__ == "__main__":
    unittest.main()
