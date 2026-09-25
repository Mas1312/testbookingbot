"""Корень домена служит и ботам (Mini App), и обычным браузерным визитам (посадочная страница)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import server  # noqa: E402


class RootPageTest(unittest.TestCase):
    def test_no_business_id_serves_landing_page(self):
        response = server.root_page(business_id=None)
        self.assertTrue(response.path.endswith("landing.html"), response.path)

    def test_business_id_serves_the_mini_app(self):
        response = server.root_page(business_id=1)
        self.assertTrue(response.path.endswith("index.html"), response.path)

    def test_landing_page_file_exists_and_mentions_platform_bot(self):
        path = os.path.join(os.path.dirname(__file__), "..", "webapp", "landing.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("teleslotapp_bot", html)
        self.assertIn("TeleSlot", html)
        self.assertIn("teleslotapp_support", html)


if __name__ == "__main__":
    unittest.main()
