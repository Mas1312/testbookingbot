"""Повторы отправки в notifications._safe_send: временные сбои повторяем, окончательные — нет."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

import notifications  # noqa: E402


class FakeBot:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    async def send_message(self, chat_id, text, **kwargs):
        self.calls += 1
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err


METHOD = SendMessage(chat_id=1, text="x")


class SafeSendTest(unittest.TestCase):
    def setUp(self):
        self._delay = notifications.SEND_RETRY_DELAY
        notifications.SEND_RETRY_DELAY = 0

    def tearDown(self):
        notifications.SEND_RETRY_DELAY = self._delay

    def run_send(self, bot):
        return asyncio.run(notifications._safe_send(bot, 1, "hi"))

    def test_success_first_try(self):
        bot = FakeBot([])
        self.assertTrue(self.run_send(bot))
        self.assertEqual(bot.calls, 1)

    def test_network_error_is_retried_then_succeeds(self):
        bot = FakeBot([TelegramNetworkError(METHOD, "timeout"), TelegramNetworkError(METHOD, "timeout"), None])
        self.assertTrue(self.run_send(bot))
        self.assertEqual(bot.calls, 3)

    def test_gives_up_after_max_attempts(self):
        bot = FakeBot([TelegramNetworkError(METHOD, "timeout")] * 5)
        self.assertFalse(self.run_send(bot))
        self.assertEqual(bot.calls, notifications.SEND_ATTEMPTS)

    def test_retry_after_is_retried(self):
        bot = FakeBot([TelegramRetryAfter(METHOD, "flood", 0), None])
        self.assertTrue(self.run_send(bot))
        self.assertEqual(bot.calls, 2)

    def test_forbidden_is_not_retried(self):
        bot = FakeBot([TelegramForbiddenError(METHOD, "bot was blocked by the user")])
        self.assertFalse(self.run_send(bot))
        self.assertEqual(bot.calls, 1)


if __name__ == "__main__":
    unittest.main()
