"""Юридические страницы (оферта, политика), ссылки на них и условие «оплата = согласие с офертой» в счёте."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import billing  # noqa: E402
import bot_setup  # noqa: E402
import server  # noqa: E402
from config import SUBSCRIPTION_DAYS, SUBSCRIPTION_PRICE_RUB  # noqa: E402

WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")
FULL_NAME = "Ведерников Михаил Станиславович"
INN = "540234051930"
EMAIL = "mihave39@gmail.com"
SUPPORT = "teleslotapp_support"


def read(name):
    with open(os.path.join(WEBAPP, name), encoding="utf-8") as f:
        return f.read()


class DocumentsTest(unittest.TestCase):
    def setUp(self):
        self.offer, self.policy = read("offer.html"), read("platform-privacy.html")

    def test_both_documents_identify_the_operator(self):
        for name, html in (("offer", self.offer), ("privacy", self.policy)):
            for needle in (FULL_NAME, INN, EMAIL, SUPPORT):
                self.assertIn(needle, html, f"{name}: нет «{needle}»")

    def test_no_placeholders_left(self):
        for html in (self.offer, self.policy):
            for marker in ("TODO", "ЗАПОЛНИТЬ", "{{", "}}", "XXX", "[ФИО", "[ИНН", "lorem"):
                self.assertNotIn(marker, html)

    def test_offer_matches_configured_price_and_period(self):
        self.assertIn(f"{SUBSCRIPTION_PRICE_RUB} рублей", self.offer)
        self.assertIn(f"{SUBSCRIPTION_DAYS} календарных дней", self.offer)

    def test_offer_states_acceptance_by_payment(self):
        for needle in ("Акцептом оферты является оплата", "самозанятый"):
            self.assertIn(needle, self.offer)

    def test_offer_has_no_refund_on_voluntary_cancellation_but_keeps_the_mandatory_cases(self):
        """Владелец решил не делать возвратов. Полностью исключить их договором нельзя (ст. 782 ГК РФ, защита
        потребителей), поэтому добровольный отказ — без возврата, а обязательные случаи оставлены явно."""
        self.assertIn("оплаченная сумма не возвращается", self.offer)
        self.assertNotIn("возвращается пропорционально", self.offer)
        for needle in ("по вине Исполнителя", "повторно списанные суммы", "возврат обязателен по закону"):
            self.assertIn(needle, self.offer)

    def test_terms_text_and_landing_do_not_promise_refunds(self):
        self.assertIn("не возвращается", bot_setup.PLATFORM_TERMS_TEXT)
        self.assertNotIn("Вопросы, возврат", bot_setup.PLATFORM_TERMS_TEXT)
        landing = read("landing.html")
        self.assertNotIn("Отменить можно в любой момент", landing)
        self.assertIn("Автопродления нет", landing)

    def test_documents_link_to_each_other(self):
        self.assertIn('href="/platform-privacy.html"', self.offer)
        self.assertIn('href="/offer.html"', self.policy)

    def test_policy_is_the_platform_one_not_the_client_template(self):
        self.assertIn("TeleSlot", self.policy)
        self.assertIn("Российской Федерации", self.policy)
        self.assertIn("ЮKassa", self.policy)

    def test_policy_does_not_promise_security_we_do_not_have_yet(self):
        """Токены ботов пока лежат в БД открытым текстом, а вход на сервер по паролю не отключён.
        Пока это так, политика не должна обещать обратного. Когда реализуем шифрование токенов и вход только
        по ключам — формулировки можно усилить и этот тест обновить."""
        self.assertNotIn("по ключам", self.policy)
        for match in re.finditer("токен", self.policy):
            window = self.policy[max(0, match.start() - 200): match.end() + 200]
            self.assertNotIn("шифр", window, window)

    def test_pages_are_responsive_and_not_blocked_from_indexing(self):
        for html in (self.offer, self.policy):
            self.assertIn("viewport", html)
            self.assertNotIn("noindex", html)


class LinksTest(unittest.TestCase):
    def test_landing_footer_links_platform_documents_not_client_template(self):
        landing = read("landing.html")
        self.assertIn('href="/offer.html"', landing)
        self.assertIn('href="/platform-privacy.html"', landing)
        self.assertNotIn('href="/privacy.html"', landing)  # это шаблон политики для клиентов бизнеса

    def test_cabinet_lists_both_documents_when_pages_exist(self):
        self.assertEqual([name for name, _ in server.CABINET_DOCS], ["offer.html", "platform-privacy.html"])
        for name, _ in server.CABINET_DOCS:
            self.assertTrue(os.path.exists(os.path.join(WEBAPP, name)), name)

    def test_terms_text_links_documents(self):
        self.assertIn(bot_setup.OFFER_URL, bot_setup.PLATFORM_TERMS_TEXT)
        self.assertIn(bot_setup.PLATFORM_PRIVACY_URL, bot_setup.PLATFORM_TERMS_TEXT)
        self.assertTrue(bot_setup.OFFER_URL.endswith("/offer.html"))


class InvoiceConsentTest(unittest.TestCase):
    def test_description_contains_offer_link_for_normal_and_huge_names(self):
        for name in ("Маникюр у Анны", "Х" * 200, "Ы" * 5000, ""):
            text = billing.invoice_description(name)
            self.assertLessEqual(len(text), billing.INVOICE_DESCRIPTION_LIMIT, name[:10])
            self.assertIn(bot_setup.OFFER_URL, text)
            self.assertIn("согласие с офертой", text)

    def test_short_name_is_kept_intact_and_long_name_is_cut_with_ellipsis(self):
        self.assertIn("«Маникюр у Анны»", billing.invoice_description("Маникюр у Анны"))
        cut = billing.invoice_description("Х" * 300)
        self.assertIn("…", cut)
        self.assertTrue(cut.startswith("«ХХ"))

    def test_invoice_kwargs_use_that_description(self):
        kwargs = billing.invoice_kwargs({"id": 1, "name": "Салон"})
        self.assertIn(bot_setup.OFFER_URL, kwargs["description"])


if __name__ == "__main__":
    unittest.main()
