"""Настройка бота владельца (меню-кнопка, команды, описания) и доступ к /newbusiness только у платформенного бота."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bot as bot_module  # noqa: E402
import bot_setup  # noqa: E402
from aiogram.types import MenuButtonWebApp  # noqa: E402
from config import PLATFORM_BUSINESS_ID  # noqa: E402

VALID_TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"


class FakeBot:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = {}

    async def _call(self, name, **kwargs):
        self.calls[name] = kwargs
        if name in self.fail:
            raise RuntimeError("boom")

    async def set_chat_menu_button(self, **kw):
        await self._call("menu_button", **kw)

    async def set_my_commands(self, commands, **kw):
        await self._call("commands", commands=commands)

    async def set_my_description(self, **kw):
        await self._call("description", **kw)

    async def set_my_short_description(self, **kw):
        await self._call("short_description", **kw)


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.answers = []
        self.markups = []
        self.from_user = type("U", (), {"id": 1})()

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        if "reply_markup" in kwargs and kwargs["reply_markup"] is not None:
            self.markups.append(kwargs["reply_markup"])


class FakeState:
    def __init__(self):
        self.state = None

    async def set_state(self, value):
        self.state = value


BUSINESS = {"id": 7, "name": "Маникюр у Анны", "bot_token": VALID_TOKEN}


class TokenTest(unittest.TestCase):
    def test_valid_token(self):
        self.assertTrue(bot_setup.looks_like_token(VALID_TOKEN))
        self.assertTrue(bot_setup.looks_like_token("  " + VALID_TOKEN + "\n"))

    def test_invalid_tokens(self):
        for text in ["", "привет", "123456789", "abc:def", "123456789:short", "@anna_nails_bot",
                     VALID_TOKEN + " лишнее"]:
            self.assertFalse(bot_setup.looks_like_token(text), text)


class ConfigureBotTest(unittest.TestCase):
    def test_all_steps_called(self):
        bot = FakeBot()
        failed = asyncio.run(bot_setup.configure_bot(bot, BUSINESS))
        self.assertEqual(failed, [])
        self.assertEqual(set(bot.calls), {"menu_button", "commands", "description", "short_description"})
        menu = bot.calls["menu_button"]["menu_button"]
        self.assertEqual(menu.text, "Записаться")
        self.assertTrue(menu.web_app.url.endswith("/?business_id=7"))

    def test_one_failing_step_does_not_stop_others(self):
        bot = FakeBot(fail={"menu_button"})
        failed = asyncio.run(bot_setup.configure_bot(bot, BUSINESS))
        self.assertEqual(failed, ["menu_button"])
        self.assertIn("commands", bot.calls)
        self.assertIn("short_description", bot.calls)

    def test_platform_gets_newbusiness_command_and_no_my(self):
        platform_names = [c.command for c in bot_setup.commands(platform=True)]
        client_names = [c.command for c in bot_setup.commands(platform=False)]
        self.assertIn("newbusiness", platform_names)
        self.assertIn("price", platform_names)
        self.assertNotIn("my", platform_names)  # у TeleSlot самого нет записей
        self.assertNotIn("newbusiness", client_names)
        self.assertIn("my", client_names)

    def test_telegram_length_limits(self):
        long_name = "Х" * 200
        self.assertLessEqual(len(bot_setup.short_description(long_name)), 120)
        self.assertLessEqual(len(bot_setup.description(long_name)), 512)
        self.assertLessEqual(len(bot_setup.platform_short_description(long_name)), 120)
        self.assertLessEqual(len(bot_setup.platform_description(long_name)), 512)

    def test_platform_menu_button_opens_cabinet_not_booking_app(self):
        """У TeleSlot нет услуг и клиентов, поэтому Mini App ЗАПИСИ он не показывает — но у него свой «Кабинет»."""
        bot = FakeBot()
        asyncio.run(bot_setup.configure_bot(bot, BUSINESS, platform=True))
        menu = bot.calls["menu_button"]["menu_button"]
        self.assertIsInstance(menu, MenuButtonWebApp)
        self.assertEqual(menu.text, "Кабинет")
        self.assertTrue(menu.web_app.url.endswith("/cabinet"))
        self.assertNotIn("business_id", menu.web_app.url)

    def test_platform_commands_include_cabinet(self):
        self.assertIn("cabinet", [c.command for c in bot_setup.commands(platform=True)])
        self.assertNotIn("cabinet", [c.command for c in bot_setup.commands(platform=False)])


class FakeCallback:
    def __init__(self, message):
        self.message = message
        self.from_user = type("U", (), {"id": 1})()
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class PlatformStartTest(unittest.TestCase):
    """TeleSlot сам по себе — не бот записи: /start у него не должен предлагать Mini App клиента."""

    def test_platform_start_has_no_booking_button(self):
        message = FakeMessage()
        asyncio.run(bot_module.cmd_start(message, business_id=PLATFORM_BUSINESS_ID))
        self.assertEqual(message.answers, [bot_setup.PLATFORM_START_TEXT])
        self.assertEqual(len(message.markups), 1)
        button = message.markups[0].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "start_newbusiness")
        self.assertIsNone(button.web_app)
        price_button = message.markups[0].inline_keyboard[0][1]
        self.assertEqual(price_button.callback_data, "show_price")
        cabinet = message.markups[0].inline_keyboard[1][0]
        self.assertEqual(cabinet.text, "Кабинет")
        self.assertTrue(cabinet.web_app.url.endswith("/cabinet"))  # кабинет, а не Mini App записи

    def test_cabinet_command_only_on_platform(self):
        message = FakeMessage()
        asyncio.run(bot_module.cmd_cabinet(message, business_id=PLATFORM_BUSINESS_ID))
        self.assertTrue(message.markups[0].inline_keyboard[0][0].web_app.url.endswith("/cabinet"))
        other = FakeMessage()
        asyncio.run(bot_module.cmd_cabinet(other, business_id=PLATFORM_BUSINESS_ID + 1))
        self.assertEqual(other.answers, [bot_setup.NOT_PLATFORM_TEXT])

    def test_start_newbusiness_button_starts_dialog_only_on_platform(self):
        message, state = FakeMessage(), FakeState()
        callback = FakeCallback(message)
        asyncio.run(bot_module.on_start_newbusiness_button(callback, state, business_id=PLATFORM_BUSINESS_ID))
        self.assertTrue(callback.answered)
        self.assertIsNotNone(state.state)
        self.assertTrue(message.answers)

    def test_start_newbusiness_button_ignored_on_client_bot(self):
        message, state = FakeMessage(), FakeState()
        callback = FakeCallback(message)
        asyncio.run(bot_module.on_start_newbusiness_button(callback, state, business_id=PLATFORM_BUSINESS_ID + 1))
        self.assertTrue(callback.answered)
        self.assertIsNone(state.state)
        self.assertEqual(message.answers, [])


class PlatformPriceTest(unittest.TestCase):
    """/price и связанные кнопки — только у платформенного бота. Без платёжного токена оплата ручная."""

    def setUp(self):
        import tempfile
        import billing
        import database
        self.database, self.billing = database, billing
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, billing.PAYMENT_PROVIDER_TOKEN)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        billing.PAYMENT_PROVIDER_TOKEN = ""

    def tearDown(self):
        self.database.DB_PATH, self.billing.PAYMENT_PROVIDER_TOKEN = self._orig
        self.tmp.cleanup()

    def test_price_command_only_on_platform(self):
        message = FakeMessage()
        asyncio.run(bot_module.cmd_price(message, business_id=PLATFORM_BUSINESS_ID))
        self.assertEqual(message.answers, [bot_setup.PLATFORM_PRICE_TEXT])
        button = message.markups[0].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "subscribe_request")

    def test_price_command_refused_on_client_bot(self):
        message = FakeMessage()
        asyncio.run(bot_module.cmd_price(message, business_id=PLATFORM_BUSINESS_ID + 1))
        self.assertEqual(message.answers, [bot_setup.NOT_PLATFORM_TEXT])

    def test_show_price_button(self):
        message = FakeMessage()
        callback = FakeCallback(message)
        asyncio.run(bot_module.on_show_price_button(callback, business_id=PLATFORM_BUSINESS_ID))
        self.assertTrue(callback.answered)
        self.assertEqual(message.answers, [bot_setup.PLATFORM_PRICE_TEXT])

    def test_subscribe_request_points_to_support_not_a_real_charge(self):
        message = FakeMessage()
        callback = FakeCallback(message)
        asyncio.run(bot_module.on_subscribe_request_button(callback, business_id=PLATFORM_BUSINESS_ID))
        self.assertTrue(callback.answered)
        self.assertEqual(message.answers, [bot_setup.PLATFORM_SUBSCRIBE_REPLY_TEXT])
        self.assertIn(bot_setup.SUPPORT_CONTACT, message.answers[0])


class PlatformGatingTest(unittest.TestCase):
    def test_newbusiness_in_client_bot_is_refused_quietly(self):
        message, state = FakeMessage(), FakeState()
        asyncio.run(bot_module.cmd_newbusiness(message, state, business_id=PLATFORM_BUSINESS_ID + 1))
        self.assertIsNone(state.state)
        self.assertEqual(message.answers, [bot_setup.NOT_PLATFORM_TEXT])
        self.assertNotIn("newbusiness", message.answers[0])

    def test_newbusiness_in_platform_bot_starts_dialog(self):
        message, state = FakeMessage(), FakeState()
        asyncio.run(bot_module.cmd_newbusiness(message, state, business_id=PLATFORM_BUSINESS_ID))
        self.assertIsNotNone(state.state)

    def test_fallback_advertises_newbusiness_only_on_platform(self):
        client, platform = FakeMessage(), FakeMessage()
        asyncio.run(bot_module.fallback(client, business_id=PLATFORM_BUSINESS_ID + 1))
        asyncio.run(bot_module.fallback(platform, business_id=PLATFORM_BUSINESS_ID))
        self.assertNotIn("/newbusiness", client.answers[0])
        self.assertIn("/newbusiness", platform.answers[0])


class FakeTokenMessage(FakeMessage):
    def __init__(self, text, user_id=555):
        super().__init__(text)
        self.from_user = type("U", (), {"id": user_id})()
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeStateWithData(FakeState):
    def __init__(self, data):
        super().__init__()
        self.data = data
        self.cleared = False

    async def get_data(self):
        return self.data

    async def clear(self):
        self.cleared = True


class OnboardingFlowTest(unittest.TestCase):
    """process_bot_token целиком, с подменой Telegram и временной БД."""

    def setUp(self):
        import tempfile
        import database
        import webhooks
        self.database, self.webhooks = database, webhooks
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, bot_module.Bot, webhooks.register_webhook, bot_setup.configure_business_bot)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        self.registered, self.configured = [], []

        class FakeCandidate:
            def __init__(self, token):
                self.token = token
                self.session = type("S", (), {"close": staticmethod(lambda: _done())})()

            async def get_me(self):
                return type("Me", (), {"username": "anna_nails_bot"})()

        async def _done():
            return None

        async def fake_register(business):
            self.registered.append(business["id"])

        async def fake_configure(business, platform=False):
            self.configured.append(business["id"])
            return []

        bot_module.Bot = FakeCandidate
        webhooks.register_webhook = fake_register
        bot_setup.configure_business_bot = fake_configure

    def tearDown(self):
        (self.database.DB_PATH, bot_module.Bot, self.webhooks.register_webhook,
         bot_setup.configure_business_bot) = self._orig
        self.tmp.cleanup()

    def test_full_flow_creates_business_and_configures_bot(self):
        message = FakeTokenMessage(VALID_TOKEN)
        state = FakeStateWithData({"business_name": "Маникюр у Анны"})
        asyncio.run(bot_module.process_bot_token(message, state))
        self.assertTrue(message.deleted, "сообщение с токеном должно удаляться")
        self.assertTrue(state.cleared)
        business = self.database.get_business_by_bot_token(VALID_TOKEN)
        self.assertEqual(business["bot_username"], "anna_nails_bot")
        self.assertEqual(business["owner_tg_id"], 555)
        self.assertEqual(self.registered, [business["id"]])
        self.assertEqual(self.configured, [business["id"]])
        self.assertIn("https://t.me/anna_nails_bot", message.answers[-1])
        # Пробный период стартует с подключения, и владельцу о нём сказано сразу.
        self.assertIsNotNone(business["trial_until"])
        self.assertIn("Пробный период", message.answers[-1])

    def test_not_a_token_is_rejected_without_deleting(self):
        message = FakeTokenMessage("привет")
        state = FakeStateWithData({"business_name": "X"})
        asyncio.run(bot_module.process_bot_token(message, state))
        self.assertFalse(message.deleted)
        self.assertEqual(message.answers, [bot_setup.NOT_A_TOKEN_TEXT])
        self.assertFalse(state.cleared)

    def test_duplicate_token_is_rejected(self):
        self.database.create_business(1, "Уже есть", VALID_TOKEN)
        message = FakeTokenMessage(VALID_TOKEN)
        state = FakeStateWithData({"business_name": "X"})
        asyncio.run(bot_module.process_bot_token(message, state))
        self.assertEqual(message.answers, [bot_setup.TOKEN_ALREADY_USED_TEXT])
        self.assertEqual(self.registered, [])


class TextsTest(unittest.TestCase):
    def test_done_text_has_link(self):
        text = bot_setup.done_text("Маникюр у Анны", "anna_nails_bot")
        self.assertIn("https://t.me/anna_nails_bot", text)
        self.assertIn("/start", text)
        self.assertIn(bot_setup.SUPPORT_CONTACT, text)

    def test_ask_token_text_mentions_botfather_steps(self):
        text = bot_setup.ask_token_text("Маникюр у Анны")
        for needle in ("@BotFather", "/newbot", "bot", "/cancel"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
