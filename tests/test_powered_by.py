"""Ссылка «Работает на TeleSlot» внизу страницы записи: только у бесплатных бизнесов, с меткой источника."""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bot as bot_module  # noqa: E402
import database  # noqa: E402
import server  # noqa: E402
from test_trial import TrialBase, fmt  # noqa: E402
from test_billing import PLATFORM_ID  # noqa: E402

WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")


class PoweredByLinkTest(TrialBase):
    def config(self, business):
        return server.api_config(business_id=business["id"])

    def test_free_business_gets_link_with_its_own_label(self):
        link = self.config(self.client)["powered_by_link"]
        self.assertEqual(link, f"https://t.me/teleslotapp_bot?start=ref_{self.client['id']}")

    def test_label_is_accepted_by_the_platform_bot(self):
        link = self.config(self.client)["powered_by_link"]
        label = link.split("start=", 1)[1]
        self.assertTrue(bot_module.SOURCE_RE.match(label), label)

    def test_paid_business_has_no_link(self):
        conn = database.get_connection()
        conn.execute("UPDATE businesses SET paid_until = ? WHERE id = ?",
                     (fmt(datetime.utcnow() + timedelta(days=20)), self.client["id"]))
        conn.commit()
        conn.close()
        self.assertIsNone(self.config(database.get_business(self.client["id"]))["powered_by_link"])

    def test_trial_business_still_has_link(self):
        business = self.set_trial(self.client, datetime.utcnow() + timedelta(days=3))
        self.assertIsNotNone(self.config(business)["powered_by_link"])

    def test_platform_business_has_no_link(self):
        self.assertIsNone(server.powered_by_link(database.get_business(PLATFORM_ID)))


class PoweredByFrontendTest(unittest.TestCase):
    def test_link_is_in_the_markup_and_wired_up(self):
        html = open(os.path.join(WEBAPP, "index.html"), encoding="utf-8").read()
        js = open(os.path.join(WEBAPP, "app.js"), encoding="utf-8").read()
        self.assertIn('id="powered-by"', html)
        self.assertIn("setupPoweredBy", js)
        self.assertIn("powered_by_link", js)
        self.assertNotRegex(re.search(r'id="powered-by".*', html).group(0), r"[\U0001F300-\U0001FAFF]")


if __name__ == "__main__":
    unittest.main()
