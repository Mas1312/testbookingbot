"""Откуда пришёл владелец: метка из /start платформенного бота и сообщение оператору о новом бизнесе."""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import bot as bot_module  # noqa: E402
import bot_setup  # noqa: E402
import database  # noqa: E402
import notifications  # noqa: E402
import webhooks  # noqa: E402
from aiogram.filters import CommandObject  # noqa: E402
from test_billing import BillingBase, OPERATOR, PLATFORM_ID, TOKEN_C  # noqa: E402

NEWCOMER = 233737291


def run(coro):
    return asyncio.run(coro)


def user(user_id=NEWCOMER, username="artour", first_name="Artour", language_code="ru"):
    return SimpleNamespace(id=user_id, username=username, first_name=first_name, language_code=language_code)


class Base(BillingBase):
    """BillingBase: платформенный бизнес с владельцем-оператором, FakeSender вместо Telegram."""

    def setUp(self):
        super().setUp()
        self.registered, self.configured = [], []

        async def fake_register(business):
            self.registered.append(business["id"])

        async def fake_configure(business, platform=False):
            self.configured.append(business["id"])
            return []

        self._patches = [mock.patch.object(webhooks, "register_webhook", fake_register),
                         mock.patch.object(bot_setup, "configure_business_bot", fake_configure)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        super().tearDown()


class VisitorTest(Base):
    def test_first_visit_is_stored(self):
        database.touch_platform_visitor(NEWCOMER, "artour", "Artour", "ru", "insta")
        v = database.get_platform_visitor(NEWCOMER)
        self.assertEqual((v["username"], v["first_name"], v["language_code"], v["source"]), ("artour", "Artour", "ru", "insta"))

    def test_first_source_wins_but_profile_is_refreshed(self):
        database.touch_platform_visitor(NEWCOMER, "old", "Артур", "ru", "insta")
        database.touch_platform_visitor(NEWCOMER, "new", "Artour", "en", "friend")
        database.touch_platform_visitor(NEWCOMER, "new", "Artour", "en", None)
        v = database.get_platform_visitor(NEWCOMER)
        self.assertEqual((v["source"], v["username"], v["first_name"], v["language_code"]), ("insta", "new", "Artour", "en"))

    def test_source_can_be_filled_in_later_but_not_replaced(self):
        database.touch_platform_visitor(NEWCOMER, None, "A", None, None)
        self.assertIsNone(database.get_platform_visitor(NEWCOMER)["source"])
        database.touch_platform_visitor(NEWCOMER, None, "A", None, "2gis")
        database.touch_platform_visitor(NEWCOMER, None, "A", None, "other")
        self.assertEqual(database.get_platform_visitor(NEWCOMER)["source"], "2gis")

    def test_unknown_visitor(self):
        self.assertIsNone(database.get_platform_visitor(123))


class StartTest(Base):
    def start(self, args, business_id=PLATFORM_ID, user_=None):
        message = SimpleNamespace(from_user=user_ or user(), answers=[], bot=None)

        async def answer(text, **kw):
            message.answers.append(text)

        message.answer = answer
        run(bot_module.cmd_start(message, business_id, CommandObject(command="start", args=args)))
        return message

    def test_start_with_a_label_records_the_source(self):
        self.start("insta")
        self.assertEqual(database.get_platform_visitor(NEWCOMER)["source"], "insta")

    def test_start_without_a_label_records_the_visitor_only(self):
        self.start(None)
        v = database.get_platform_visitor(NEWCOMER)
        self.assertEqual((v["source"], v["first_name"]), (None, "Artour"))

    def test_garbage_labels_are_ignored(self):
        for args in ("<script>", "a b", "x" * 65, "метка", "svc_1; drop", "a" * 64 + "!"):
            with self.subTest(args=args):
                self.start(args, user_=user(NEWCOMER + len(args)))
                self.assertIsNone(database.get_platform_visitor(NEWCOMER + len(args))["source"])

    def test_valid_label_shapes(self):
        for i, args in enumerate(("insta", "2gis", "friend_anna", "tg-channel", "A" * 64)):
            self.start(args, user_=user(1000 + i))
            self.assertEqual(database.get_platform_visitor(1000 + i)["source"], args)

    def test_business_bots_do_not_track_visitors(self):
        biz = database.create_business(7, "Салон", "423456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lP")
        self.start("insta", business_id=biz["id"])
        self.assertIsNone(database.get_platform_visitor(NEWCOMER))


class ConnectTest(Base):
    def test_business_inherits_the_source_of_its_owner(self):
        database.touch_platform_visitor(NEWCOMER, "artour", "Artour", "ru", "insta")
        business = run(bot_setup.connect_business(NEWCOMER, "Барберстарс", TOKEN_C, "barberstars_bot"))
        self.assertEqual((business["source"], business["bot_username"]), ("insta", "barberstars_bot"))
        stored = database.get_business(business["id"])
        self.assertEqual((stored["source"], stored["bot_username"]), ("insta", "barberstars_bot"))

    def test_no_visitor_record_means_no_source(self):
        business = run(bot_setup.connect_business(NEWCOMER, "Барберстарс", TOKEN_C, "barberstars_bot"))
        self.assertIsNone(database.get_business(business["id"])["source"])


class OperatorMessageTest(Base):
    def setUp(self):
        super().setUp()
        self.operator_bot = self.sender
        notifications.get_bot = lambda business: self.operator_bot

    def connect(self, owner=NEWCOMER, name="Барберстарс"):
        return run(bot_setup.connect_business(owner, name, TOKEN_C, "barberstars_bot"))

    def test_operator_is_told_about_a_new_business(self):
        database.touch_platform_visitor(NEWCOMER, "artour", "Artour", "ru", "insta")
        business = self.connect()
        run(notifications.notify_operator_new_business(business))
        (chat, text, kwargs), = self.sender.sent
        self.assertEqual(chat, OPERATOR)
        for fragment in ("Новый бизнес", "Барберстарс", "@barberstars_bot", "Artour", "@artour", "insta",
                         f"tg://user?id={NEWCOMER}", "Пробный период"):
            self.assertIn(fragment, text)

    def test_without_a_label_the_message_says_so(self):
        business = self.connect()
        run(notifications.notify_operator_new_business(business))
        self.assertIn("не определён", self.sender.sent[0][1])

    def test_operators_own_tests_are_not_reported(self):
        business = self.connect(owner=OPERATOR)
        run(notifications.notify_operator_new_business(business))
        self.assertEqual(self.sender.sent, [])

    def test_html_in_names_is_escaped(self):
        database.touch_platform_visitor(NEWCOMER, "a_b", "<b>Артур</b>", "ru", "x_y")
        business = self.connect(name="<i>Салон</i> & Ко")
        run(notifications.notify_operator_new_business(business))
        text = self.sender.sent[0][1]
        self.assertNotIn("<i>", text)
        self.assertNotIn("<b>Артур", text)
        self.assertIn("&lt;i&gt;Салон&lt;/i&gt; &amp; Ко", text)

    def test_a_failing_send_never_breaks_the_connection(self):
        class Broken:
            async def send_message(self, *a, **k):
                raise RuntimeError("telegram недоступен")

        self.operator_bot = Broken()
        business = self.connect()
        run(notifications.notify_operator_new_business(business))   # не должно бросить
        self.assertTrue(database.get_business(business["id"]))


class FlowsTest(Base):
    """Оба пути подключения (токен от BotFather и управляемый бот) заканчиваются сообщением оператору."""

    def setUp(self):
        super().setUp()
        notifications.get_bot = lambda business: self.sender

    def test_token_flow(self):
        database.touch_platform_visitor(NEWCOMER, "artour", "Artour", "ru", "friend")

        class FakeCandidate:
            def __init__(self, token, **kw):
                self.session = SimpleNamespace(close=lambda: asyncio.sleep(0))

            async def get_me(self):
                return SimpleNamespace(username="barberstars_bot")

        class Msg:
            text = TOKEN_C
            from_user = user()

            async def delete(self):
                pass

            async def answer(self, text, **kw):
                pass

        class State:
            async def get_data(self):
                return {"business_name": "Барберстарс"}

            async def clear(self):
                pass

        with mock.patch.object(bot_module, "Bot", FakeCandidate):
            run(bot_module.process_bot_token(Msg(), State()))
        (chat, text, _), = self.sender.sent
        self.assertEqual(chat, OPERATOR)
        self.assertIn("friend", text)

    def test_managed_bot_flow(self):
        class PlatformBot:
            id = 900001

            def __init__(self, outer):
                self.outer = outer

            async def __call__(self, method, **kw):
                return TOKEN_C

            async def send_message(self, chat_id, text, **kw):
                self.outer.sender.sent.append((chat_id, text, kw))

        data = {"update_id": 1, "managed_bot": {
            "user": {"id": NEWCOMER, "first_name": "Artour", "username": "artour", "language_code": "ru"},
            "bot": {"id": 42, "is_bot": True, "first_name": "Барберстарс", "username": "barberstars_bot"}}}
        database.touch_platform_visitor(NEWCOMER, None, None, None, "2gis")
        run(bot_module.handle_managed_bot_update(PlatformBot(self), data))
        chats = [chat for chat, _, _ in self.sender.sent]
        self.assertIn(OPERATOR, chats)                   # оператор получил сообщение
        self.assertIn(NEWCOMER, chats)                   # владельцу пришло «Готово»
        operator_text = next(t for c, t, _ in self.sender.sent if c == OPERATOR)
        self.assertIn("2gis", operator_text)
        self.assertIn("@artour", operator_text)          # профиль взят из самого события managed_bot


class MigrationTest(Base):
    def test_schema(self):
        conn = database.get_connection()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = [r[1] for r in conn.execute("PRAGMA table_info(businesses)")]
        conn.close()
        self.assertIn("platform_visitors", tables)
        self.assertIn("source", columns)
        database.init_db()   # повторный запуск безопасен


if __name__ == "__main__":
    unittest.main()
