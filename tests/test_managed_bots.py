"""Подключение бота без BotFather: «управляемые боты» Telegram (managed_bots.py и обработчики в bot.py/server.py)."""
import asyncio
import os
import random
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bot as bot_module  # noqa: E402
import bot_setup  # noqa: E402
import database  # noqa: E402
import managed_bots  # noqa: E402
import server  # noqa: E402
import webhooks  # noqa: E402
from aiogram.client.session.aiohttp import AiohttpSession  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.types import User  # noqa: E402
from config import PLATFORM_BUSINESS_ID, TRIAL_DAYS  # noqa: E402

TOKEN = "777777777:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
CREATOR = 5150


def run(coro):
    return asyncio.run(coro)


class UsernameTest(unittest.TestCase):
    VALID = re.compile(r"^[a-z][a-z0-9_]{4,31}$")

    def test_suggestions_are_valid_telegram_usernames(self):
        names = ["Ламинирование ресниц", "Барбершоп у Ивана", "Цветы у Насти", "Nail Studio", "Анна", "!!!", "   ",
                 "Очень длинное название салона красоты и ногтевого сервиса на проспекте", "1 день", "Щётка"]
        for name in names:
            with self.subTest(name=name):
                username = managed_bots.suggest_username(name)
                self.assertRegex(username, self.VALID)
                self.assertTrue(username.endswith("bot"))
                self.assertLessEqual(len(username), 32)

    def test_transliteration_and_seed(self):
        a = managed_bots.suggest_username("Цветы у Насти", random.Random(1))
        b = managed_bots.suggest_username("Цветы у Насти", random.Random(1))
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("cvety_u_nasti_"))

    def test_link_format(self):
        link = managed_bots.newbot_link("teleslotapp_bot", "salon_123_bot", "Салон & Ко")
        self.assertTrue(link.startswith("https://t.me/newbot/teleslotapp_bot/salon_123_bot?name="))
        self.assertNotIn(" ", link)
        self.assertIn("%26", link)  # & не должен ломать параметры


class ParseTest(unittest.TestCase):
    def payload(self, key="bot"):
        return {"update_id": 1, "managed_bot": {"user": {"id": CREATOR, "first_name": "A"},
                                                key: {"id": 42, "is_bot": True, "first_name": "Салон", "username": "salon_bot"}}}

    def test_both_field_names_are_accepted(self):
        for key in ("bot", "bot_user"):
            self.assertEqual(managed_bots.parse_update(self.payload(key)), (CREATOR, 42, "salon_bot", "Салон"))

    def test_garbage_is_rejected(self):
        for data in ({}, {"managed_bot": None}, {"managed_bot": {"user": {"id": 1}}},
                     {"managed_bot": {"user": {"id": "x"}, "bot": {"id": 2}}}):
            self.assertIsNone(managed_bots.parse_update(data))


class FakeBot:
    def __init__(self, me=None, error=None, bot_id=900):
        self.id = bot_id
        self.token = "900:platform-token-" + "x" * 30
        self.me, self.error, self.get_me_calls = me, error, 0
        self.sent = []
        self.token_for_bot, self.token_error = TOKEN, None

    async def get_me(self):
        self.get_me_calls += 1
        if self.error:
            raise self.error
        return self.me

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))

    async def __call__(self, method, **kwargs):
        if self.token_error:
            raise self.token_error
        assert isinstance(method, managed_bots.GetManagedBotToken)
        return self.token_for_bot


class AvailabilityTest(unittest.TestCase):
    def setUp(self):
        managed_bots.reset_cache()

    def test_flag_true_false_and_missing(self):
        self.assertTrue(run(managed_bots.available(FakeBot(SimpleNamespace(can_manage_bots=True), bot_id=1))))
        self.assertFalse(run(managed_bots.available(FakeBot(SimpleNamespace(can_manage_bots=False), bot_id=2))))
        self.assertFalse(run(managed_bots.available(FakeBot(SimpleNamespace(), bot_id=3))))

    def test_real_aiogram_user_carries_the_extra_field(self):
        me = User.model_validate({"id": 1, "is_bot": True, "first_name": "TeleSlot", "can_manage_bots": True})
        self.assertTrue(run(managed_bots.available(FakeBot(me, bot_id=4))))
        plain = User.model_validate({"id": 1, "is_bot": True, "first_name": "TeleSlot"})
        self.assertFalse(run(managed_bots.available(FakeBot(plain, bot_id=5))))

    def test_error_is_not_cached_and_means_no(self):
        bot = FakeBot(error=RuntimeError("net"), bot_id=6)
        self.assertFalse(run(managed_bots.available(bot)))
        bot.error, bot.me = None, SimpleNamespace(can_manage_bots=True)
        self.assertTrue(run(managed_bots.available(bot)))

    def test_answer_is_cached(self):
        bot = FakeBot(SimpleNamespace(can_manage_bots=True), bot_id=7)
        run(managed_bots.available(bot))
        run(managed_bots.available(bot))
        self.assertEqual(bot.get_me_calls, 1)


class MethodTest(unittest.TestCase):
    def test_custom_method_goes_out_as_getManagedBotToken(self):
        from aiogram import Bot
        seen = []

        async def fake_request(self, bot, method, timeout=None):
            seen.append((method.__api_method__, method.model_dump()))
            return TOKEN

        async def go():
            bot = Bot(token="900:platform-token-" + "x" * 30)
            try:
                return await bot(managed_bots.GetManagedBotToken(user_id=42))
            finally:
                await bot.session.close()

        with mock.patch.object(AiohttpSession, "make_request", fake_request):
            self.assertEqual(run(go()), TOKEN)
        self.assertEqual(seen, [("getManagedBotToken", {"user_id": 42})])


class DbBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        self.registered, self.configured = [], []

        async def fake_register(business):
            self.registered.append(business["id"])

        async def fake_configure(business, platform=False):
            self.configured.append(business["id"])
            return []

        self._patches = [
            mock.patch.object(webhooks, "register_webhook", fake_register),
            mock.patch.object(bot_setup, "configure_business_bot", fake_configure),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        database.DB_PATH = self._orig_path
        self.tmp.cleanup()


class PendingTest(DbBase):
    def test_save_pop_and_replace(self):
        database.save_pending_managed_bot(CREATOR, "Первое", "a_bot")
        database.save_pending_managed_bot(CREATOR, "Второе", "b_bot")
        self.assertEqual(database.pop_pending_managed_bot(CREATOR), "Второе")
        self.assertIsNone(database.pop_pending_managed_bot(CREATOR))  # забрали один раз

    def test_stale_entry_is_ignored(self):
        database.save_pending_managed_bot(CREATOR, "Старое", None)
        old = (datetime.utcnow() - timedelta(hours=30)).strftime(database.DB_TIME_FORMAT)
        conn = database.get_connection()
        conn.execute("UPDATE pending_managed_bots SET created_at = ?", (old,))
        conn.commit()
        conn.close()
        self.assertIsNone(database.pop_pending_managed_bot(CREATOR))

    def test_lookup_by_username_ignores_case(self):
        b = database.create_business(1, "Салон", TOKEN)
        database.set_bot_username(b["id"], "Salon_Bot")
        self.assertEqual(database.get_business_by_bot_username("salon_bot")["id"], b["id"])
        self.assertIsNone(database.get_business_by_bot_username("other_bot"))


class HandlerTest(DbBase):
    def update(self, username="salon_123_bot", first_name="Салон"):
        return {"update_id": 1, "managed_bot": {
            "user": {"id": CREATOR, "first_name": "Анна"},
            "bot": {"id": 42, "is_bot": True, "first_name": first_name, "username": username}}}

    def handle(self, bot, data=None):
        run(bot_module.handle_managed_bot_update(bot, data or self.update()))

    def test_creates_business_for_the_creator(self):
        database.save_pending_managed_bot(CREATOR, "Ламинирование ресниц", "salon_123_bot")
        bot = FakeBot()
        self.handle(bot)
        business = database.get_business_by_bot_token(TOKEN)
        self.assertEqual((business["owner_tg_id"], business["name"], business["bot_username"]),
                         (CREATOR, "Ламинирование ресниц", "salon_123_bot"))
        self.assertIsNotNone(business["trial_until"])
        self.assertEqual(self.registered, [business["id"]])
        self.assertEqual(self.configured, [business["id"]])
        (chat_id, text), = bot.sent
        self.assertEqual(chat_id, CREATOR)
        self.assertIn("https://t.me/salon_123_bot", text)
        self.assertIn("Ламинирование ресниц", text)
        self.assertIn("Пробный период", text)

    def test_name_falls_back_to_the_bot_name(self):
        self.handle(FakeBot())
        self.assertEqual(database.get_business_by_bot_token(TOKEN)["name"], "Салон")

    def test_redelivery_does_not_create_a_second_business(self):
        bot = FakeBot()
        self.handle(bot)
        self.handle(bot)
        self.assertEqual(len(database.get_all_businesses()), 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(self.registered), 1)

    def test_already_connected_bot_is_skipped_before_asking_for_a_token(self):
        b = database.create_business(7, "Уже есть", "888888888:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")
        database.set_bot_username(b["id"], "Salon_123_Bot")
        bot = FakeBot()
        bot.token_error = AssertionError("токен запрашивать не нужно")
        self.handle(bot)
        self.assertEqual(len(database.get_all_businesses()), 1)
        self.assertEqual(bot.sent, [])

    def test_token_failure_tells_the_owner_and_creates_nothing(self):
        bot = FakeBot()
        bot.token_error = RuntimeError("telegram недоступен")
        self.handle(bot)
        self.assertEqual(database.get_all_businesses(), [])
        (chat_id, text), = bot.sent
        self.assertEqual(chat_id, CREATOR)
        self.assertIn("/newbusiness", text)

    def test_unknown_format_is_ignored(self):
        bot = FakeBot()
        self.handle(bot, {"update_id": 1, "managed_bot": {}})
        self.assertEqual((database.get_all_businesses(), bot.sent), ([], []))

    def test_dialog_state_is_cleared(self):
        bot = FakeBot()
        key = StorageKey(bot_id=bot.id, chat_id=CREATOR, user_id=CREATOR)
        ctx = FSMContext(storage=bot_module.dp.storage, key=key)

        async def go():
            await ctx.set_state(bot_module.NewBusinessStates.waiting_token)
            await bot_module.handle_managed_bot_update(bot, self.update())
            return await ctx.get_state()

        self.assertIsNone(run(go()))

    def test_html_in_name_is_escaped_in_the_message(self):
        database.save_pending_managed_bot(CREATOR, "<b>Салон</b> & Ко", None)
        bot = FakeBot()
        self.handle(bot)
        self.assertNotIn("<b>", bot.sent[0][1])
        self.assertIn("&lt;b&gt;", bot.sent[0][1])


class FakeMessage:
    def __init__(self, text, bot):
        self.text, self.bot = text, bot
        self.from_user = SimpleNamespace(id=CREATOR)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeState:
    def __init__(self, data=None):
        self.data, self.state = data or {}, None

    async def update_data(self, **kw):
        self.data.update(kw)

    async def set_state(self, state):
        self.state = state

    async def get_data(self):
        return self.data


class OnboardingStepTest(DbBase):
    def setUp(self):
        super().setUp()
        managed_bots.reset_cache()
        self._patch_platform = mock.patch.object(
            bot_module.database, "get_business",
            lambda bid: {"id": bid, "bot_username": "teleslotapp_bot"} if bid == PLATFORM_BUSINESS_ID else None)
        self._patch_platform.start()

    def tearDown(self):
        self._patch_platform.stop()
        super().tearDown()

    def step(self, can_manage):
        bot = FakeBot(SimpleNamespace(can_manage_bots=can_manage), bot_id=901 + int(can_manage))
        message, state = FakeMessage("Ламинирование ресниц", bot), FakeState()
        run(bot_module.process_business_name(message, state))
        return message, state

    def test_offers_automatic_creation_when_available(self):
        message, state = self.step(True)
        text, kwargs = message.answers[0]
        self.assertEqual(text, bot_setup.managed_offer_text("Ламинирование ресниц"))
        buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
        url_button = next(b for b in buttons if b.url)
        self.assertTrue(url_button.url.startswith("https://t.me/newbot/teleslotapp_bot/"))
        self.assertIn(("have_token",), [(b.callback_data,) for b in buttons])
        self.assertEqual(state.data["business_name"], "Ламинирование ресниц")
        self.assertEqual(state.state, bot_module.NewBusinessStates.waiting_token)  # токен, присланный сразу, тоже примут
        # название запомнено: событие о новом боте придёт без него
        self.assertEqual(database.pop_pending_managed_bot(CREATOR), "Ламинирование ресниц")

    def test_username_in_the_link_is_the_suggested_one(self):
        message, _ = self.step(True)
        url = next(b.url for row in message.answers[0][1]["reply_markup"].inline_keyboard for b in row if b.url)
        username = url.split("/newbot/teleslotapp_bot/")[1].split("?")[0]
        self.assertRegex(username, r"^[a-z][a-z0-9_]{4,31}$")
        self.assertTrue(username.endswith("bot"))

    def test_old_flow_when_not_available(self):
        message, state = self.step(False)
        text, kwargs = message.answers[0]
        self.assertEqual(text, bot_setup.ask_token_text("Ламинирование ресниц"))
        self.assertNotIn("reply_markup", kwargs)
        self.assertIsNone(database.pop_pending_managed_bot(CREATOR))
        self.assertEqual(state.state, bot_module.NewBusinessStates.waiting_token)

    def test_have_token_button_shows_the_old_instructions(self):
        sent = []

        class Msg:
            async def answer(self, text, **kw):
                sent.append(text)

        class Cb:
            message = Msg()

            async def answer(self):
                pass

        run(bot_module.on_have_token_button(Cb(), FakeState({"business_name": "Салон"})))
        self.assertEqual(sent, [bot_setup.ask_token_text("Салон")])


class ServiceMessageTest(unittest.TestCase):
    def test_bot_created_service_message_is_not_taken_for_a_token(self):
        """Telegram присылает в чат «создал бота ...» (managed_bot_created): раньше бот отвечал «это не похоже на токен»."""
        from aiogram.types import Message
        message = Message.model_validate({
            "message_id": 1, "date": 0, "chat": {"id": CREATOR, "type": "private"},
            "from": {"id": CREATOR, "is_bot": False, "first_name": "Анна"},
            "managed_bot_created": {"bot": {"id": 42, "is_bot": True, "first_name": "Салон"}},
        })
        self.assertIsNone(message.text)
        self.assertTrue(getattr(message, "managed_bot_created", None))
        answers, state = [], FakeState()

        async def answer(text, **kw):
            answers.append(text)

        with mock.patch.object(Message, "answer", lambda self, text, **kw: answer(text, **kw)):
            run(bot_module.process_bot_token(message, state))
        self.assertEqual(answers, [])

    def test_an_ordinary_non_token_message_still_gets_the_hint(self):
        message = FakeMessage("привет", FakeBot())
        message.delete = None
        run(bot_module.process_bot_token(message, FakeState({"business_name": "X"})))
        self.assertEqual(message.answers[0][0], bot_setup.NOT_A_TOKEN_TEXT)


class RoutingTest(unittest.TestCase):
    class Req:
        def __init__(self, data, secret):
            self.headers, self._data = {"X-Telegram-Bot-Api-Secret-Token": secret}, data

        async def json(self):
            return self._data

    def setUp(self):
        self.bot = FakeBot()
        self.data = {"update_id": 1, "managed_bot": {"user": {"id": CREATOR}, "bot": {"id": 42}}}
        self._saved = dict(webhooks.bots_by_business)
        webhooks.bots_by_business.clear()
        webhooks.bots_by_business[PLATFORM_BUSINESS_ID] = self.bot
        webhooks.bots_by_business[PLATFORM_BUSINESS_ID + 1] = self.bot
        self.secret = webhooks.webhook_secret_for(self.bot.token)

    def tearDown(self):
        webhooks.bots_by_business.clear()
        webhooks.bots_by_business.update(self._saved)

    def post(self, business_id):
        return run(server.telegram_webhook(business_id, self.Req(self.data, self.secret)))

    def test_platform_bot_update_goes_to_the_managed_handler_not_to_aiogram(self):
        calls = []

        async def handler(bot, data):
            calls.append(data)

        with mock.patch.object(bot_module, "handle_managed_bot_update", handler), \
                mock.patch.object(server.tg_dp, "feed_update", side_effect=AssertionError("не должно")):
            self.assertEqual(self.post(PLATFORM_BUSINESS_ID), {"ok": True})
        self.assertEqual(len(calls), 1)

    def test_other_businesses_ignore_it(self):
        with mock.patch.object(bot_module, "handle_managed_bot_update",
                               side_effect=AssertionError("не должно")), \
                mock.patch.object(server.tg_dp, "feed_update", side_effect=AssertionError("не должно")):
            self.assertEqual(self.post(PLATFORM_BUSINESS_ID + 1), {"ok": True})

    def test_handler_crash_does_not_become_a_500(self):
        async def handler(bot, data):
            raise RuntimeError("boom")

        with mock.patch.object(bot_module, "handle_managed_bot_update", handler):
            self.assertEqual(self.post(PLATFORM_BUSINESS_ID), {"ok": True})


class WebhookUpdatesTest(unittest.TestCase):
    def test_only_the_platform_bot_asks_for_managed_bot_updates(self):
        self.assertIn("managed_bot", webhooks.allowed_updates_for(PLATFORM_BUSINESS_ID))
        self.assertNotIn("managed_bot", webhooks.allowed_updates_for(PLATFORM_BUSINESS_ID + 1))
        self.assertEqual(webhooks.allowed_updates_for(PLATFORM_BUSINESS_ID + 1), webhooks.ALLOWED_UPDATES)


if __name__ == "__main__":
    unittest.main()
