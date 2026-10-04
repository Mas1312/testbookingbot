"""Пробный период (3 дня) и приостановка записи: статусы, пауза, напоминания, тексты."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import billing  # noqa: E402
import bot_setup  # noqa: E402
import database  # noqa: E402
import server  # noqa: E402
from config import TRIAL_DAYS  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_billing import BillingBase, NOW, OWNER, PLATFORM_ID  # noqa: E402

WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")


def fmt(dt: datetime) -> str:
    return dt.strftime(database.DB_TIME_FORMAT)


class _NoBackground:
    def add_task(self, *args, **kwargs):
        pass


class TrialBase(BillingBase):
    def set_trial(self, business, end: datetime | None):
        conn = database.get_connection()
        conn.execute("UPDATE businesses SET trial_until = ? WHERE id = ?", (fmt(end) if end else None, business["id"]))
        conn.commit()
        conn.close()
        return database.get_business(business["id"])

    def fresh(self, business=None):
        return database.get_business((business or self.client)["id"])


class CreationTest(TrialBase):
    def test_default_is_three_days_unless_overridden(self):
        if "TRIAL_DAYS" not in os.environ:
            self.assertEqual(TRIAL_DAYS, 3)

    def test_trial_starts_at_connection(self):
        b = database.create_business(OWNER, "С пробным", "777777777:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM", trial_days=3)
        end = datetime.strptime(b["trial_until"], database.DB_TIME_FORMAT)
        delta = end - datetime.utcnow()
        self.assertLess(abs(delta - timedelta(days=3)), timedelta(minutes=1))

    def test_no_trial_means_free_pilot(self):
        self.assertIsNone(self.client["trial_until"])
        self.assertEqual(billing.subscription_status(self.fresh())[0], "pilot")
        self.assertFalse(billing.is_suspended(self.fresh()))


class StatusTest(TrialBase):
    def test_trial_then_expired(self):
        b = self.set_trial(self.client, NOW + timedelta(days=3))
        self.assertEqual(billing.subscription_status(b, NOW), ("trial", 3))
        self.assertEqual(billing.subscription_status(b, NOW + timedelta(days=2, hours=1)), ("trial", 1))
        self.assertEqual(billing.subscription_status(b, NOW + timedelta(days=3)), ("trial_expired", 0))

    def test_payment_takes_precedence_over_trial(self):
        b = self.set_trial(self.client, NOW - timedelta(days=1))  # пробный уже закончился
        self.pay("c1", NOW)
        b = self.fresh()
        self.assertEqual(billing.subscription_status(b, NOW)[0], "active")
        self.assertFalse(billing.is_suspended(b, NOW))

    def test_expired_payment_suspends_even_if_trial_would_still_run(self):
        self.set_trial(self.client, NOW + timedelta(days=10))
        self.pay("c1", NOW - timedelta(days=40))
        b = self.fresh()
        self.assertEqual(billing.subscription_status(b, NOW)[0], "expired")
        self.assertTrue(billing.is_suspended(b, NOW))


class SuspensionTest(TrialBase):
    def test_who_is_suspended(self):
        active_trial = self.set_trial(self.client, NOW + timedelta(days=1))
        self.assertFalse(billing.is_suspended(active_trial, NOW))
        ended = self.set_trial(self.client, NOW - timedelta(seconds=1))
        self.assertTrue(billing.is_suspended(ended, NOW))

    def test_platform_business_is_never_suspended(self):
        platform = self.set_trial(database.get_business(PLATFORM_ID), NOW - timedelta(days=30))
        self.assertFalse(billing.is_suspended(platform, NOW))

    def test_payment_lifts_suspension(self):
        self.set_trial(self.client, datetime.utcnow() - timedelta(days=1))
        self.assertTrue(billing.is_suspended(self.fresh()))
        self.pay("c1", datetime.utcnow())
        self.assertFalse(billing.is_suspended(self.fresh()))


class BookingGateTest(TrialBase):
    def setUp(self):
        super().setUp()
        self.service = database.create_service(self.client["id"], "Букет", 1000, 0, "order")

    def book(self, business=None):
        b = business or self.client
        return server.api_book(server.BookingRequest(business_id=b["id"], service_id=self.service, client_name="Анна",
                                                     client_tg_id=5), background_tasks=_NoBackground())

    def test_suspended_business_refuses_new_bookings_without_talking_about_money(self):
        self.set_trial(self.client, datetime.utcnow() - timedelta(minutes=1))
        with self.assertRaises(HTTPException) as ctx:
            self.book()
        self.assertEqual(ctx.exception.status_code, 403)
        for word in ("оплат", "подписк", "пробн", "990"):
            self.assertNotIn(word, ctx.exception.detail.lower())

    def test_running_trial_and_free_pilot_can_book(self):
        self.assertTrue(self.book()["ok"])  # пилот без пробного периода
        self.set_trial(self.client, datetime.utcnow() + timedelta(days=2))
        self.assertTrue(self.book()["ok"])

    def test_booking_works_again_right_after_payment(self):
        self.set_trial(self.client, datetime.utcnow() - timedelta(days=1))
        with self.assertRaises(HTTPException):
            self.book()
        self.pay("c1", datetime.utcnow())
        self.assertTrue(self.book()["ok"])

    def test_other_businesses_are_not_affected(self):
        self.set_trial(self.client, datetime.utcnow() - timedelta(days=1))
        other = database.create_business(OWNER, "Другой", "888888888:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")
        self.service = database.create_service(other["id"], "Услуга", 100, 0, "order")
        self.assertTrue(self.book(other)["ok"])

    def test_config_exposes_suspension_and_platform_link(self):
        database.set_bot_username(PLATFORM_ID, "teleslotapp_bot")
        self.assertFalse(server.api_config(self.client["id"])["suspended"])
        self.set_trial(self.client, datetime.utcnow() - timedelta(days=1))
        config = server.api_config(self.client["id"])
        self.assertTrue(config["suspended"])
        self.assertEqual(config["platform_link"], "https://t.me/teleslotapp_bot")


class ReminderTest(TrialBase):
    def run_reminders(self, when):
        return asyncio.run(billing.send_subscription_reminders(now=when))

    def test_trial_reminder_comes_one_day_before_and_once(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        end = NOW + timedelta(days=3)
        self.set_trial(self.client, end)
        self.assertEqual(self.run_reminders(NOW), 0)                      # только подключили — молчим
        self.assertEqual(self.run_reminders(end - timedelta(days=2)), 0)  # за 2 дня рано
        self.assertEqual(self.run_reminders(end - timedelta(hours=20)), 1)
        self.assertEqual(self.run_reminders(end - timedelta(hours=10)), 0)  # дубль не шлём
        _, text, kwargs = self.sender.sent[0]
        self.assertIn("Пробный период", text)
        self.assertIn("приостановится", text)
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].text, "Оплатить")

    def test_expired_trial_reminder_says_booking_is_suspended_once(self):
        end = NOW + timedelta(days=3)
        self.set_trial(self.client, end)
        self.assertEqual(self.run_reminders(end + timedelta(minutes=5)), 1)
        text = self.sender.sent[-1][1]
        self.assertIn("закончился", text)
        self.assertIn("приостановлена", text)
        self.assertEqual(self.run_reminders(end + timedelta(days=1)), 0)

    def test_payment_during_trial_switches_to_paid_schedule(self):
        end = NOW + timedelta(days=3)
        self.set_trial(self.client, end)
        self.pay("c1", NOW)  # paid_until = NOW + 30d
        self.assertEqual(self.run_reminders(end - timedelta(hours=5)), 0)       # пробный уже не важен
        self.assertEqual(self.run_reminders(NOW + timedelta(days=28)), 1)       # окно оплаты — за 3 дня
        self.assertIn("Подписка", self.sender.sent[-1][1])

    def test_free_pilot_gets_no_reminders(self):
        self.assertEqual(self.run_reminders(NOW + timedelta(days=100)), 0)

    def test_paid_expiry_text_mentions_suspension(self):
        self.pay("c1", NOW)
        self.run_reminders(NOW + timedelta(days=31))
        self.assertIn("приостановлена", self.sender.sent[-1][1])
        self.assertIn("закончилась", self.sender.sent[-1][1])

    def test_html_in_name_is_escaped_in_trial_reminder(self):
        conn = database.get_connection()
        conn.execute("UPDATE businesses SET name = ? WHERE id = ?", ("<i>Салон</i>", self.client["id"]))
        conn.commit()
        conn.close()
        self.set_trial(self.client, NOW + timedelta(hours=5))
        self.run_reminders(NOW)
        self.assertIn("&lt;i&gt;Салон&lt;/i&gt;", self.sender.sent[0][1])


class CabinetPayloadTest(TrialBase):
    def item(self, now):
        return billing.cabinet_payload(OWNER, now=now)["businesses"][0]

    def test_trial_fields(self):
        self.set_trial(self.client, NOW + timedelta(days=2))
        item = self.item(NOW)
        self.assertEqual((item["status"], item["days_left"], item["suspended"]), ("trial", 2, False))
        self.assertTrue(item["trial_until"])
        self.assertEqual(billing.cabinet_payload(OWNER, now=NOW)["trial_days"], TRIAL_DAYS)

    def test_trial_expired_is_marked_suspended(self):
        self.set_trial(self.client, NOW - timedelta(hours=1))
        item = self.item(NOW)
        self.assertEqual((item["status"], item["suspended"]), ("trial_expired", True))

    def test_price_text_lines_for_each_state(self):
        self.set_trial(self.client, NOW + timedelta(days=2))
        self.assertIn("пробный период до", billing.subscription_line(self.fresh(), NOW))
        self.set_trial(self.client, NOW - timedelta(days=1))
        self.assertIn("запись приостановлена", billing.subscription_line(self.fresh(), NOW))


class TextsTest(unittest.TestCase):
    def test_days_word(self):
        words = {1: "день", 2: "дня", 3: "дня", 4: "дня", 5: "дней", 11: "дней", 12: "дней", 14: "дней",
                 21: "день", 22: "дня", 25: "дней", 30: "дней"}
        for n, word in words.items():
            self.assertEqual(bot_setup.days_word(n), word, n)

    def test_done_text_announces_trial_and_suspension(self):
        text = bot_setup.done_text("Салон", "salon_bot", trial_days=3)
        self.assertIn("Пробный период: 3 дня бесплатно", text)
        self.assertIn("приостановится", text)
        self.assertIn("/price", text)

    def test_done_text_without_trial_has_no_trial_line(self):
        self.assertNotIn("Пробный период", bot_setup.done_text("Салон", "salon_bot"))

    def test_platform_texts_mention_the_trial(self):
        for text in (bot_setup.PLATFORM_START_TEXT, bot_setup.PLATFORM_PRICE_TEXT, bot_setup.PLATFORM_TERMS_TEXT):
            self.assertIn(bot_setup.TRIAL_PHRASE, text)
        self.assertIn("приостанов", bot_setup.PLATFORM_PRICE_TEXT)
        self.assertIn("приостанавливается", bot_setup.PLATFORM_TERMS_TEXT)

    def test_offer_and_landing_match_the_configured_trial(self):
        """Страницы пишутся вручную, поэтому проверяем, что срок в них совпадает с настройкой."""
        with open(os.path.join(WEBAPP, "offer.html"), encoding="utf-8") as f:
            offer = f.read()
        with open(os.path.join(WEBAPP, "landing.html"), encoding="utf-8") as f:
            landing = f.read()
        self.assertIn(f"{TRIAL_DAYS} календарных", offer)
        self.assertIn("приостанавливает предоставление Сервиса", offer)
        self.assertIn(f"{TRIAL_DAYS} {bot_setup.days_word(TRIAL_DAYS)} бесплатно", landing)
        self.assertIn("приостановится", landing)

    def test_offer_keeps_a_basis_for_the_free_pilot_of_earlier_businesses(self):
        with open(os.path.join(WEBAPP, "offer.html"), encoding="utf-8") as f:
            offer = f.read()
        self.assertIn("бесплатный доступ на иных условиях", offer)
        self.assertIn("не менее чем за 7 дней", offer)


if __name__ == "__main__":
    unittest.main()
