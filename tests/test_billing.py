"""Подписка TeleSlot: платёжная логика (billing.py, database.record_payment) и обработчики платформенного бота."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import billing  # noqa: E402
import bot as bot_module  # noqa: E402
import bot_setup  # noqa: E402
import database  # noqa: E402
import notifications  # noqa: E402

PLATFORM_ID = billing.PLATFORM_BUSINESS_ID
OPERATOR = 900
OWNER = 4242
STRANGER = 777
TOKEN_A = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
TOKEN_B = "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN"
TOKEN_C = "323456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lO"
NOW = datetime(2026, 11, 1, 12, 0, 0)


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    def __init__(self, user_id=OWNER, payment=None):
        self.from_user = FakeUser(user_id)
        self.answers, self.markups, self.invoices = [], [], []
        self.bot = FakeSender()
        self.successful_payment = payment

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        if kwargs.get("reply_markup") is not None:
            self.markups.append(kwargs["reply_markup"])

    async def answer_invoice(self, **kwargs):
        self.invoices.append(kwargs)


class FakeCallback:
    def __init__(self, data="", user_id=OWNER):
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = FakeMessage(user_id)
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class FakeQuery:
    def __init__(self, payload, user_id=OWNER, total=99000, currency="RUB"):
        self.invoice_payload, self.from_user = payload, FakeUser(user_id)
        self.total_amount, self.currency = total, currency
        self.result = None

    async def answer(self, ok, error_message=None):
        self.result = (ok, error_message)


class FakePayment:
    def __init__(self, payload, charge="tg-charge-1", total=99000, currency="RUB"):
        self.invoice_payload, self.total_amount, self.currency = payload, total, currency
        self.telegram_payment_charge_id = charge
        self.provider_payment_charge_id = "yk-" + charge


class BillingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, billing.PAYMENT_PROVIDER_TOKEN, notifications.get_bot)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        # Бизнес с id=PLATFORM_ID — платформенный (владелец — оператор). Если PLATFORM_BUSINESS_ID в окружении
        # не 1 (на сервере он 4), id до него занимают чужие «заполнители»: их владелец не оператор и не OWNER.
        while True:
            count = len(database.get_all_businesses())
            is_platform = count + 1 == PLATFORM_ID
            business = database.create_business(OPERATOR if is_platform else 1, "TeleSlot", TOKEN_A + str(count))
            if business["id"] == PLATFORM_ID:
                break
        self.client = database.create_business(OWNER, "Маникюр у Анны", TOKEN_B)
        self.sender = FakeSender()
        notifications.get_bot = lambda business: self.sender

    def tearDown(self):
        database.DB_PATH, billing.PAYMENT_PROVIDER_TOKEN, notifications.get_bot = self._orig
        self.tmp.cleanup()

    def pay(self, charge="tg-charge-1", when=NOW, business=None, days=30):
        b = business or self.client
        return database.record_payment(b["id"], OWNER, 99000, "RUB", billing.make_payload(b["id"]),
                                       charge, "yk-" + charge, days, now=when)


class PayloadTest(unittest.TestCase):
    def test_roundtrip_and_garbage(self):
        self.assertEqual(billing.parse_payload(billing.make_payload(12)), 12)
        for bad in ["", "sub:", "sub:abc", "other:5", None]:
            self.assertIsNone(billing.parse_payload(bad))


class InvoiceTest(BillingBase):
    def test_invoice_has_receipt_and_email_flags(self):
        billing.PAYMENT_PROVIDER_TOKEN = "381764678:TEST:12345"
        kwargs = billing.invoice_kwargs(self.client)
        self.assertEqual(kwargs["currency"], "RUB")
        self.assertEqual(kwargs["prices"][0].amount, billing.amount_kopecks())
        self.assertTrue(kwargs["need_email"] and kwargs["send_email_to_provider"])
        self.assertEqual(kwargs["payload"], f"sub:{self.client['id']}")
        item = json.loads(kwargs["provider_data"])["receipt"]["items"][0]
        self.assertEqual(item["amount"]["currency"], "RUB")
        self.assertEqual(item["payment_subject"], "service")
        self.assertLessEqual(len(item["description"]), 128)

    def test_long_business_name_does_not_break_limits(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        long = dict(self.client, name="Х" * 200)
        kwargs = billing.invoice_kwargs(long)
        self.assertLessEqual(len(kwargs["description"]), 255)
        self.assertLessEqual(len(json.loads(kwargs["provider_data"])["receipt"]["items"][0]["description"]), 128)


class PreCheckoutTest(BillingBase):
    def ok(self, payload=None, user=OWNER, total=None, currency="RUB"):
        return billing.validate_pre_checkout(payload or billing.make_payload(self.client["id"]), user,
                                             billing.amount_kopecks() if total is None else total, currency)

    def test_valid(self):
        self.assertEqual(self.ok(), (True, None))

    def test_only_owner_can_pay(self):
        ok, error = self.ok(user=STRANGER)
        self.assertFalse(ok)
        self.assertIn("владелец", error)

    def test_wrong_amount_or_currency_rejected(self):
        self.assertFalse(self.ok(total=1)[0])
        self.assertFalse(self.ok(currency="USD")[0])

    def test_unknown_garbage_and_platform_business_rejected(self):
        self.assertFalse(self.ok(payload="sub:9999")[0])
        self.assertFalse(self.ok(payload="junk")[0])
        self.assertFalse(self.ok(payload=billing.make_payload(PLATFORM_ID), user=OPERATOR)[0])


class RecordPaymentTest(BillingBase):
    def test_first_payment_starts_from_now(self):
        paid_until, is_new = self.pay()
        self.assertTrue(is_new)
        self.assertEqual(paid_until, (NOW + timedelta(days=30)).strftime(database.DB_TIME_FORMAT))
        self.assertEqual(database.get_business(self.client["id"])["paid_until"], paid_until)

    def test_early_renewal_adds_to_current_period(self):
        self.pay("c1", NOW)
        paid_until, _ = self.pay("c2", NOW + timedelta(days=10))  # оплатили заранее — 20 дней не сгорают
        self.assertEqual(paid_until, (NOW + timedelta(days=60)).strftime(database.DB_TIME_FORMAT))

    def test_late_renewal_starts_from_now_not_from_expired_date(self):
        self.pay("c1", NOW)
        later = NOW + timedelta(days=45)
        paid_until, _ = self.pay("c2", later)
        self.assertEqual(paid_until, (later + timedelta(days=30)).strftime(database.DB_TIME_FORMAT))

    def test_duplicate_charge_is_idempotent(self):
        first, new1 = self.pay("same")
        second, new2 = self.pay("same", NOW + timedelta(days=1))
        self.assertTrue(new1)
        self.assertFalse(new2)
        self.assertEqual(first, second)
        conn = database.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_unknown_business_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            database.record_payment(9999, OWNER, 99000, "RUB", "sub:9999", "x", None, 30, now=NOW)
        conn = database.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class ApplyPaymentTest(BillingBase):
    def test_apply_known_and_unknown_payload(self):
        result = billing.apply_successful_payment(OWNER, FakePayment(billing.make_payload(self.client["id"])), now=NOW)
        self.assertTrue(result["is_new"])
        self.assertEqual(result["amount_rub"], billing.SUBSCRIPTION_PRICE_RUB)
        self.assertIsNone(billing.apply_successful_payment(OWNER, FakePayment("sub:9999", "other")))


class StatusTextTest(BillingBase):
    def test_price_text_shows_pilot_then_paid_until(self):
        text = billing.price_text_for(OWNER)
        self.assertIn("пилот", text)
        self.pay(when=datetime.utcnow())
        self.assertIn("оплачено до", billing.price_text_for(OWNER))

    def test_price_text_for_user_without_business_is_plain(self):
        self.assertEqual(billing.price_text_for(STRANGER), bot_setup.PLATFORM_PRICE_TEXT)

    def test_platform_business_not_listed_for_operator(self):
        self.assertEqual(billing.price_text_for(OPERATOR), bot_setup.PLATFORM_PRICE_TEXT)


class ReminderTest(BillingBase):
    def run_reminders(self, when):
        return asyncio.run(billing.send_subscription_reminders(now=when))

    def test_reminder_only_inside_three_day_window_and_only_once(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        self.pay("c1", NOW)  # paid_until = NOW + 30d
        self.assertEqual(self.run_reminders(NOW + timedelta(days=10)), 0)
        self.assertEqual(self.run_reminders(NOW + timedelta(days=28)), 1)
        self.assertEqual(self.run_reminders(NOW + timedelta(days=28, hours=1)), 0)  # дубль не шлём
        chat_id, text, kwargs = self.sender.sent[0]
        self.assertEqual(chat_id, OWNER)
        self.assertIn("заканчивается", text)
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data, f"pay:{self.client['id']}")

    def test_expired_reminder_sent_once_and_renewal_rearms(self):
        self.pay("c1", NOW)
        after = NOW + timedelta(days=31)
        self.assertEqual(self.run_reminders(after), 1)
        self.assertIn("закончилась", self.sender.sent[-1][1])
        self.assertEqual(self.run_reminders(after + timedelta(days=1)), 0)
        self.pay("c2", after)  # продлили -> новый срок -> новые напоминания возможны
        self.assertEqual(self.run_reminders(after + timedelta(days=28)), 1)

    def test_html_in_business_name_is_escaped(self):
        conn = database.get_connection()
        conn.execute("UPDATE businesses SET name = ? WHERE id = ?", ("<b>Салон</b>", self.client["id"]))
        conn.commit()
        conn.close()
        self.pay("c1", NOW)
        self.run_reminders(NOW + timedelta(days=29))
        self.assertIn("&lt;b&gt;Салон&lt;/b&gt;", self.sender.sent[0][1])

    def test_no_payments_no_reminders(self):
        self.assertEqual(self.run_reminders(NOW), 0)
        self.assertEqual(self.sender.sent, [])


def run(coro):
    return asyncio.run(coro)


class BotHandlersTest(BillingBase):
    def test_subscribe_without_provider_token_points_to_support(self):
        billing.PAYMENT_PROVIDER_TOKEN = ""
        cb = FakeCallback("subscribe_request")
        run(bot_module.on_subscribe_request_button(cb, business_id=PLATFORM_ID))
        self.assertEqual(cb.message.answers, [bot_setup.PLATFORM_SUBSCRIBE_REPLY_TEXT])
        self.assertEqual(cb.message.invoices, [])

    def test_subscribe_without_business_asks_to_connect_first(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        cb = FakeCallback("subscribe_request", user_id=STRANGER)
        run(bot_module.on_subscribe_request_button(cb, business_id=PLATFORM_ID))
        self.assertIn("/newbusiness", cb.message.answers[0])
        self.assertEqual(cb.message.invoices, [])

    def test_subscribe_with_one_business_sends_invoice(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        cb = FakeCallback("subscribe_request")
        run(bot_module.on_subscribe_request_button(cb, business_id=PLATFORM_ID))
        self.assertEqual(len(cb.message.invoices), 1)
        self.assertEqual(cb.message.invoices[0]["payload"], f"sub:{self.client['id']}")

    def test_subscribe_with_two_businesses_asks_which(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        second = database.create_business(OWNER, "Барбершоп", TOKEN_C)
        cb = FakeCallback("subscribe_request")
        run(bot_module.on_subscribe_request_button(cb, business_id=PLATFORM_ID))
        self.assertEqual(cb.message.invoices, [])
        buttons = [row[0].callback_data for row in cb.message.markups[0].inline_keyboard]
        self.assertEqual(buttons, [f"pay:{self.client['id']}", f"pay:{second['id']}"])

    def test_pay_button_checks_ownership(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        mine = FakeCallback(f"pay:{self.client['id']}")
        run(bot_module.on_pay_button(mine, business_id=PLATFORM_ID))
        self.assertEqual(len(mine.message.invoices), 1)
        theirs = FakeCallback(f"pay:{self.client['id']}", user_id=STRANGER)
        run(bot_module.on_pay_button(theirs, business_id=PLATFORM_ID))
        self.assertEqual(theirs.message.invoices, [])
        garbage = FakeCallback("pay:xyz")
        run(bot_module.on_pay_button(garbage, business_id=PLATFORM_ID))
        self.assertEqual(garbage.message.invoices, [])

    def test_pay_button_ignored_outside_platform_bot(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        cb = FakeCallback(f"pay:{self.client['id']}")
        run(bot_module.on_pay_button(cb, business_id=self.client["id"]))
        self.assertEqual(cb.message.invoices, [])

    def test_pre_checkout_answers(self):
        good = FakeQuery(billing.make_payload(self.client["id"]))
        run(bot_module.on_pre_checkout(good, business_id=PLATFORM_ID))
        self.assertEqual(good.result, (True, None))
        bad = FakeQuery(billing.make_payload(self.client["id"]), user_id=STRANGER)
        run(bot_module.on_pre_checkout(bad, business_id=PLATFORM_ID))
        self.assertFalse(bad.result[0])
        wrong_bot = FakeQuery(billing.make_payload(self.client["id"]))
        run(bot_module.on_pre_checkout(wrong_bot, business_id=self.client["id"]))
        self.assertFalse(wrong_bot.result[0])

    def test_expired_pre_checkout_does_not_raise(self):
        # Telegram отвечает «query is too old»: хендлер не должен падать, иначе вебхук отдаст 500 и запрос придёт снова.
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import AnswerPreCheckoutQuery

        class Expired(FakeQuery):
            async def answer(self, ok, error_message=None):
                raise TelegramBadRequest(method=AnswerPreCheckoutQuery(pre_checkout_query_id="x", ok=True),
                                         message="query is too old")

        run(bot_module.on_pre_checkout(Expired(billing.make_payload(self.client["id"])), business_id=PLATFORM_ID))

    def test_successful_payment_extends_confirms_and_notifies_operator_once(self):
        payment = FakePayment(billing.make_payload(self.client["id"]), "tg-1")
        message = FakeMessage(OWNER, payment)
        run(bot_module.on_successful_payment(message, business_id=PLATFORM_ID))
        self.assertIn("Оплата получена", message.answers[0])
        self.assertIsNotNone(database.get_business(self.client["id"])["paid_until"])
        self.assertEqual([chat for chat, _, _ in message.bot.sent], [OPERATOR])
        # тот же платёж доставлен повторно — ни второго сообщения, ни второго продления
        paid_until = database.get_business(self.client["id"])["paid_until"]
        again = FakeMessage(OWNER, FakePayment(billing.make_payload(self.client["id"]), "tg-1"))
        run(bot_module.on_successful_payment(again, business_id=PLATFORM_ID))
        self.assertEqual(again.answers, [])
        self.assertEqual(database.get_business(self.client["id"])["paid_until"], paid_until)

    def test_successful_payment_with_unknown_business_tells_to_contact_support(self):
        message = FakeMessage(OWNER, FakePayment("sub:9999", "tg-x"))
        run(bot_module.on_successful_payment(message, business_id=PLATFORM_ID))
        self.assertIn(bot_setup.SUPPORT_CONTACT, message.answers[0])

    def test_terms_and_support_only_on_platform(self):
        msg = FakeMessage()
        run(bot_module.cmd_terms(msg, business_id=PLATFORM_ID))
        run(bot_module.cmd_support(msg, business_id=PLATFORM_ID))
        self.assertEqual(msg.answers[0], bot_setup.PLATFORM_TERMS_TEXT)
        self.assertIn(bot_setup.SUPPORT_CONTACT, msg.answers[1])
        other = FakeMessage()
        run(bot_module.cmd_terms(other, business_id=self.client["id"]))
        self.assertEqual(other.answers, [bot_setup.NOT_PLATFORM_TEXT])


class WebhookConfigTest(unittest.TestCase):
    def test_pre_checkout_updates_are_allowed(self):
        import webhooks
        self.assertIn("pre_checkout_query", webhooks.ALLOWED_UPDATES)
        self.assertIn("message", webhooks.ALLOWED_UPDATES)  # successful_payment приходит внутри message


if __name__ == "__main__":
    unittest.main()
