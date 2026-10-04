"""Кабинет TeleSlot (Mini App платформенного бота): авторизация по подписи Telegram, данные, оплата по ссылке."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import billing  # noqa: E402
import database  # noqa: E402
import notifications  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PLATFORM_ID = server.PLATFORM_BUSINESS_ID
OPERATOR = 900
OWNER = 4242
STRANGER = 777
NOW = datetime(2026, 11, 1, 12, 0, 0)


def sign_init_data(user_id: int, bot_token: str, auth_date: int | None = None) -> str:
    """Настоящая подпись Telegram WebApp initData (алгоритм из документации Telegram)."""
    pairs = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAHtest",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": digest})


class FakeBot:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def create_invoice_link(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return "https://t.me/$invoice-link"


class CabinetBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK, billing.PAYMENT_PROVIDER_TOKEN,
                      notifications.get_bot, server.webapp_dir)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        server.DEV_SKIP_INITDATA_CHECK = False  # проверяем настоящую подпись, а не дев-фолбэк
        billing.PAYMENT_PROVIDER_TOKEN = ""
        server.webapp_dir = self.tmp.name  # без offer.html и т.п.: ссылок на документы быть не должно
        while True:
            count = len(database.get_all_businesses())
            is_platform = count + 1 == PLATFORM_ID
            business = database.create_business(OPERATOR if is_platform else 1, "TeleSlot",
                                                f"{100 + count}:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3l{count:02d}")
            if business["id"] == PLATFORM_ID:
                break
        self.platform = business
        self.client = database.create_business(OWNER, "Маникюр у Анны", "555555555:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")
        database.set_bot_username(self.client["id"], "anna_nails_bot")
        self.bot = FakeBot()
        notifications.get_bot = lambda business: self.bot

    def tearDown(self):
        (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK, billing.PAYMENT_PROVIDER_TOKEN,
         notifications.get_bot, server.webapp_dir) = self._orig
        self.tmp.cleanup()

    def signed(self, user_id=OWNER, token=None, auth_date=None):
        return sign_init_data(user_id, token or self.platform["bot_token"], auth_date)


class AuthTest(CabinetBase):
    def test_valid_signature_returns_own_businesses(self):
        data = server.cabinet_me(init_data=self.signed())
        self.assertEqual(data["user_id"], OWNER)
        self.assertEqual([b["id"] for b in data["businesses"]], [self.client["id"]])

    def test_signature_from_a_client_bot_is_rejected(self):
        """initData из бота бизнеса подписан токеном ЭТОГО бота — кабинет TeleSlot его принимать не должен."""
        forged = sign_init_data(OWNER, self.client["bot_token"])
        with self.assertRaises(HTTPException) as ctx:
            server.cabinet_me(init_data=forged)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_tampered_missing_and_expired_data_rejected(self):
        good = self.signed()
        for bad in [good.replace("Test", "Evil"), "", "garbage",
                    self.signed(auth_date=int(time.time()) - 3 * 24 * 3600)]:
            with self.assertRaises(HTTPException) as ctx:
                server.cabinet_me(init_data=bad)
            self.assertEqual(ctx.exception.status_code, 403, bad[:30])

    def test_dev_fallback_only_when_flag_enabled(self):
        with self.assertRaises(HTTPException):
            server.cabinet_me(init_data="", owner_tg_id=OWNER)
        server.DEV_SKIP_INITDATA_CHECK = True
        self.assertEqual(server.cabinet_me(init_data="", owner_tg_id=OWNER)["user_id"], OWNER)

    def test_stranger_sees_nothing_of_others(self):
        data = server.cabinet_me(init_data=self.signed(STRANGER))
        self.assertEqual(data["businesses"], [])
        self.assertEqual(data["history"], [])

    def test_platform_business_is_never_listed(self):
        data = server.cabinet_me(init_data=self.signed(OPERATOR))
        self.assertNotIn(PLATFORM_ID, [b["id"] for b in data["businesses"]])


class PayloadTest(CabinetBase):
    def test_status_pilot_active_expiring_expired(self):
        b = dict(self.client)
        self.assertEqual(billing.subscription_status(b, NOW), ("pilot", None))
        b["paid_until"] = (NOW + timedelta(days=20)).strftime(database.DB_TIME_FORMAT)
        self.assertEqual(billing.subscription_status(b, NOW), ("active", 20))
        b["paid_until"] = (NOW + timedelta(days=2, hours=3)).strftime(database.DB_TIME_FORMAT)
        self.assertEqual(billing.subscription_status(b, NOW), ("expiring", 3))
        b["paid_until"] = (NOW - timedelta(seconds=1)).strftime(database.DB_TIME_FORMAT)
        self.assertEqual(billing.subscription_status(b, NOW), ("expired", 0))

    def test_history_and_link_in_payload(self):
        database.record_payment(self.client["id"], OWNER, 99000, "RUB", "sub:2", "c1", "y1", 30, now=NOW)
        data = billing.cabinet_payload(OWNER, now=NOW)
        item = data["businesses"][0]
        self.assertEqual(item["link"], "https://t.me/anna_nails_bot")
        self.assertEqual((item["status"], item["days_left"]), ("active", 30))  # оплатили и смотрим в один и тот же NOW
        self.assertEqual(data["history"][0]["amount_rub"], 990)
        self.assertEqual(data["history"][0]["business_name"], "Маникюр у Анны")

    def test_docs_listed_only_if_pages_exist(self):
        self.assertEqual(server.cabinet_me(init_data=self.signed())["docs"], [])
        with open(os.path.join(self.tmp.name, "offer.html"), "w", encoding="utf-8") as f:
            f.write("<html></html>")
        docs = server.cabinet_me(init_data=self.signed())["docs"]
        self.assertEqual([d["url"] for d in docs], ["/offer.html"])


class InvoiceTest(CabinetBase):
    def make(self, business_id=None, user=OWNER, token=None):
        return server.CabinetInvoiceRequest(business_id=business_id or self.client["id"], init_data=self.signed(user, token))

    def test_payments_disabled_gives_409(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.cabinet_invoice(self.make()))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.bot.calls, [])

    def test_creates_link_with_receipt_and_email_flags(self):
        billing.PAYMENT_PROVIDER_TOKEN = "381764678:TEST:12345"
        result = asyncio.run(server.cabinet_invoice(self.make()))
        self.assertEqual(result, {"link": "https://t.me/$invoice-link"})
        kwargs = self.bot.calls[0]
        self.assertEqual(kwargs["payload"], f"sub:{self.client['id']}")
        self.assertTrue(kwargs["need_email"] and kwargs["send_email_to_provider"])
        self.assertIn("receipt", json.loads(kwargs["provider_data"]))

    def test_cannot_pay_for_someone_elses_or_platform_business(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        for request in (self.make(user=STRANGER), self.make(business_id=PLATFORM_ID, user=OPERATOR),
                        self.make(business_id=9999)):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(server.cabinet_invoice(request))
            self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.bot.calls, [])

    def test_unauthenticated_and_foreign_signature_rejected(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        for request in (server.CabinetInvoiceRequest(business_id=self.client["id"]),
                        self.make(token=self.client["bot_token"])):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(server.cabinet_invoice(request))
            self.assertEqual(ctx.exception.status_code, 403)

    def test_telegram_failure_becomes_502(self):
        billing.PAYMENT_PROVIDER_TOKEN = "t"
        self.bot.error = RuntimeError("boom")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.cabinet_invoice(self.make()))
        self.assertEqual(ctx.exception.status_code, 502)


class ManageFromCabinetTest(CabinetBase):
    """Управление бизнесом из кабинета: админка открывается в контексте платформенного бота, и initData там
    подписан токеном ПЛАТФОРМЫ. Сервер должен принять эту подпись у владельца — и только у владельца."""

    def schedule_for(self, init_data):
        return server.admin_get_schedule(self.client["id"], init_data=init_data)

    def test_owner_with_platform_signature_can_manage(self):
        self.assertIn("weekly", self.schedule_for(self.signed(OWNER)))

    def test_owner_with_own_business_bot_signature_still_works(self):
        self.assertIn("weekly", self.schedule_for(sign_init_data(OWNER, self.client["bot_token"])))

    def test_non_owner_with_valid_platform_signature_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self.schedule_for(self.signed(STRANGER))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_signature_from_unrelated_bot_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self.schedule_for(sign_init_data(OWNER, "999999999:AAEhBOweik6ad9r_QXMENQjcrTu-NotOurBot"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_or_garbage_signature_is_refused(self):
        for bad in ("", "garbage"):
            with self.assertRaises(HTTPException) as ctx:
                self.schedule_for(bad)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_platform_owner_cannot_manage_other_peoples_business(self):
        """Оператор платформы — не владелец чужого бизнеса: подпись платформы сама по себе прав не даёт."""
        with self.assertRaises(HTTPException) as ctx:
            self.schedule_for(self.signed(OPERATOR))
        self.assertEqual(ctx.exception.status_code, 403)


class BusinessStatsTest(CabinetBase):
    def test_counts_new_and_todays_bookings(self):
        service = database.create_service(self.client["id"], "Маникюр", 1500, 60, "slot")
        tz = database.get_business(self.client["id"])["timezone"]
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo(tz)).date().isoformat()
        tomorrow = (datetime.now(ZoneInfo(tz)).date() + timedelta(days=1)).isoformat()
        database.create_booking(self.client["id"], service, "Маникюр", 1500, today, "10:00", "А", 1)
        database.create_booking(self.client["id"], service, "Маникюр", 1500, tomorrow, "10:00", "Б", 2)
        cancelled = database.create_booking(self.client["id"], service, "Маникюр", 1500, today, "12:00", "В", 3)
        database.update_booking_status(self.client["id"], cancelled, "cancelled")
        item = billing.cabinet_payload(OWNER)["businesses"][0]
        self.assertEqual(item["new_bookings"], 2)     # отменённая не «новая»
        self.assertEqual(item["today_bookings"], 1)   # сегодняшняя неотменённая

    def test_no_bookings_gives_zeroes(self):
        item = billing.cabinet_payload(OWNER)["businesses"][0]
        self.assertEqual((item["new_bookings"], item["today_bookings"]), (0, 0))

    def test_stats_never_mix_businesses(self):
        other = database.create_business(OWNER, "Второй", "666666666:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")
        service = database.create_service(other["id"], "Услуга", 100, 30, "slot")
        tz = database.get_business(other["id"])["timezone"]
        from zoneinfo import ZoneInfo
        database.create_booking(other["id"], service, "Услуга", 100, datetime.now(ZoneInfo(tz)).date().isoformat(),
                                "10:00", "Г", 4)
        by_id = {b["id"]: b for b in billing.cabinet_payload(OWNER)["businesses"]}
        self.assertEqual(by_id[self.client["id"]]["new_bookings"], 0)
        self.assertEqual(by_id[other["id"]]["new_bookings"], 1)


class PageTest(unittest.TestCase):
    def test_cabinet_route_serves_cabinet_html(self):
        orig = server.webapp_dir
        server.webapp_dir = os.path.join(os.path.dirname(__file__), "..", "webapp")
        try:
            self.assertTrue(server.cabinet_page().path.endswith("cabinet.html"))
        finally:
            server.webapp_dir = orig

    def test_cabinet_js_never_uses_innerhtml(self):
        """Названия бизнесов вводят пользователи: в кабинете всё идёт через textContent."""
        path = os.path.join(os.path.dirname(__file__), "..", "webapp", "cabinet.js")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("insertAdjacentHTML", source)


if __name__ == "__main__":
    unittest.main()
