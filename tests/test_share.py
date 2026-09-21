"""Ссылка и QR для клиентов: модуль share и админские эндпоинты (доступ, отправка, обновление бота)."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
import share  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
OWNER = 4242


class FakeSender:
    def __init__(self, error=None):
        self.error, self.sent = error, []

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        if self.error:
            raise self.error
        self.sent.append((chat_id, caption))


class ShareBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK, server.notifications.get_bot,
                      share.ensure_bot_username, server.bot_setup.configure_business_bot)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        self.business = database.create_business(OWNER, "Маникюр у Анны", TOKEN)
        self.bid = self.business["id"]
        database.set_bot_username(self.bid, "anna_nails_bot")
        server.DEV_SKIP_INITDATA_CHECK = True  # проверяем логику owner_tg_id-фолбэка так же, как остальные админ-тесты

    def tearDown(self):
        (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK, server.notifications.get_bot,
         share.ensure_bot_username, server.bot_setup.configure_business_bot) = self._orig
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.run(coro)


class QrTest(unittest.TestCase):
    def test_png(self):
        png = share.make_qr_png("https://t.me/anna_nails_bot")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 200)


class EnsureUsernameTest(ShareBase):
    def test_returns_stored_username_without_telegram(self):
        business = database.get_business(self.bid)
        self.assertEqual(self.run_async(share.ensure_bot_username(business)), "anna_nails_bot")

    def test_fetches_and_stores_when_missing(self):
        database.set_bot_username(self.bid, None)

        class FakeBot:
            def __init__(self, token):
                pass

            async def get_me(self):
                return type("Me", (), {"username": "fetched_bot"})()

            session = type("S", (), {"close": staticmethod(lambda: asyncio.sleep(0))})()

        orig = share.Bot
        share.Bot = FakeBot
        try:
            username = self.run_async(share.ensure_bot_username(database.get_business(self.bid)))
        finally:
            share.Bot = orig
        self.assertEqual(username, "fetched_bot")
        self.assertEqual(database.get_business(self.bid)["bot_username"], "fetched_bot")


class AdminEndpointsTest(ShareBase):
    def test_share_for_owner(self):
        data = self.run_async(server.admin_share(self.bid, init_data="", owner_tg_id=OWNER))
        self.assertEqual(data["link"], "https://t.me/anna_nails_bot")
        self.assertEqual(data["qr_url"], f"/api/qr?business_id={self.bid}")

    def test_share_forbidden_for_stranger_and_anonymous(self):
        with self.assertRaises(HTTPException) as ctx:
            self.run_async(server.admin_share(self.bid, init_data="", owner_tg_id=OWNER + 1))
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:
            self.run_async(server.admin_share(self.bid, init_data="", owner_tg_id=None))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_public_qr_is_png(self):
        response = self.run_async(server.api_qr(self.bid))
        self.assertEqual(response.media_type, "image/png")
        self.assertTrue(response.body.startswith(b"\x89PNG"))

    def test_qr_unknown_business_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self.run_async(server.api_qr(9999))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_send_qr_to_owner(self):
        sender = FakeSender()
        server.notifications.get_bot = lambda business: sender
        payload = server.OwnerActionRequest(business_id=self.bid, owner_tg_id=OWNER)
        self.assertEqual(self.run_async(server.admin_share_send(payload)), {"ok": True})
        chat_id, caption = sender.sent[0]
        self.assertEqual(chat_id, OWNER)
        self.assertIn("https://t.me/anna_nails_bot", caption)

    def test_send_qr_when_owner_never_pressed_start(self):
        server.notifications.get_bot = lambda business: FakeSender(
            TelegramForbiddenError(SendMessage(chat_id=1, text="x"), "bot can't initiate conversation"))
        payload = server.OwnerActionRequest(business_id=self.bid, owner_tg_id=OWNER)
        with self.assertRaises(HTTPException) as ctx:
            self.run_async(server.admin_share_send(payload))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("/start", ctx.exception.detail)

    def test_send_qr_forbidden_for_stranger(self):
        payload = server.OwnerActionRequest(business_id=self.bid, owner_tg_id=OWNER + 1)
        with self.assertRaises(HTTPException) as ctx:
            self.run_async(server.admin_share_send(payload))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_bot_refresh_reports_failed_steps(self):
        calls = []

        async def fake_configure(business, platform=False):
            calls.append((business["id"], platform))
            return ["menu_button"]

        server.bot_setup.configure_business_bot = fake_configure
        payload = server.OwnerActionRequest(business_id=self.bid, owner_tg_id=OWNER)
        result = self.run_async(server.admin_bot_refresh(payload))
        self.assertEqual(result, {"ok": False, "failed": ["menu_button"]})
        self.assertEqual(calls, [(self.bid, self.bid == server.PLATFORM_BUSINESS_ID)])


if __name__ == "__main__":
    unittest.main()
