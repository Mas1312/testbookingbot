"""Корень домена служит и ботам (Mini App), и обычным браузерным визитам (посадочная страница)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bot as bot_module  # noqa: E402
import bot_setup  # noqa: E402
import server  # noqa: E402
from config import SUBSCRIPTION_DAYS, SUBSCRIPTION_PRICE_RUB, TRIAL_DAYS  # noqa: E402

WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")


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


class LandingContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(WEBAPP, "landing.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_every_image_exists_is_webp_and_light(self):
        sources = re.findall(r'<img[^>]+src="(/landing/[^"]+)"', self.html)
        self.assertGreaterEqual(len(sources), 8)
        total = 0
        for src in sources:
            path = os.path.join(WEBAPP, src.lstrip("/"))
            self.assertTrue(os.path.exists(path), src)
            with open(path, "rb") as f:
                head = f.read(12)
            self.assertEqual((head[:4], head[8:12]), (b"RIFF", b"WEBP"), src)
            size = os.path.getsize(path)
            self.assertLess(size, 150_000, f"{src}: {size} байт")
            total += size
        self.assertLess(total, 1_000_000)

    def test_every_image_has_alt_text_and_size(self):
        images = re.findall(r"<img\b[^>]*>", self.html)
        self.assertTrue(images)
        for tag in images:
            alt = re.search(r'alt="([^"]*)"', tag)
            self.assertTrue(alt and len(alt.group(1)) >= 10, tag)
            self.assertIn("width=", tag)
            self.assertIn("height=", tag)

    def test_below_the_fold_images_are_lazy_and_the_hero_ones_are_not(self):
        images = re.findall(r"<img\b[^>]*>", self.html)
        hero = [t for t in images if "fetchpriority" in t]
        lazy = [t for t in images if 'loading="lazy"' in t]
        self.assertEqual(len(hero), 2)
        self.assertEqual(len(hero) + len(lazy), len(images))

    def test_social_preview_image_exists(self):
        self.assertIn("https://teleslotapp.com/landing/og.jpg", self.html)
        self.assertTrue(os.path.exists(os.path.join(WEBAPP, "landing", "og.jpg")))

    def test_every_bot_link_carries_the_site_source_label(self):
        links = re.findall(r'href="(https://t\.me/teleslotapp_bot[^"]*)"', self.html)
        self.assertGreaterEqual(len(links), 4)
        for link in links:
            self.assertTrue(link.endswith("?start=site"), link)
        self.assertRegex("site", bot_module.SOURCE_RE.pattern)   # метку бот действительно запомнит

    def test_price_and_trial_match_the_service_settings(self):
        self.assertIn(f"{SUBSCRIPTION_PRICE_RUB} ₽", self.html)
        self.assertIn(f"{SUBSCRIPTION_DAYS} дней", self.html)
        self.assertIn(f"{TRIAL_DAYS} {bot_setup.days_word(TRIAL_DAYS)} бесплатно", self.html)
        per_day = round(SUBSCRIPTION_PRICE_RUB / SUBSCRIPTION_DAYS)
        self.assertIn(f"около {per_day} рублей в день", self.html)

    def test_no_external_scripts_or_styles_and_no_trackers(self):
        self.assertNotRegex(self.html, r"<script[^>]+src=")
        self.assertNotRegex(self.html, r'<link[^>]+rel="stylesheet"')
        for host in ("googletagmanager", "google-analytics", "mc.yandex", "facebook.net", "cdn."):
            self.assertNotIn(host, self.html)

    def test_it_does_not_promise_things_the_product_cannot_do(self):
        lowered = self.html.lower()
        for promise in ("онлайн-оплата услуг", "принимайте оплату", "рассылки клиентам", "интеграция с crm", "whatsapp", "гарантир"):
            self.assertNotIn(promise, lowered)
        self.assertIn("пока нет", lowered)   # про оплату услуг сказано честно

    def test_main_sections_are_present(self):
        for anchor in ('id="features"', 'id="start"', 'id="pricing"', 'id="faq"'):
            self.assertIn(anchor, self.html)
        self.assertGreaterEqual(self.html.count("<details>"), 6)

    def test_no_emoji_in_the_page(self):
        self.assertIsNone(re.search("[🌀-🫿☀-➿]", self.html))


class LandingServedTest(unittest.TestCase):
    def test_images_are_reachable_through_the_static_mount(self):
        names = [n for n in os.listdir(os.path.join(WEBAPP, "landing")) if n.endswith((".webp", ".jpg"))]
        self.assertGreaterEqual(len(names), 9)


if __name__ == "__main__":
    unittest.main()
