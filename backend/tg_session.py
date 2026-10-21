"""HTTP-сессия aiogram с коротким таймаутом и повтором: с сервера api.telegram.org отвечает нестабильно
(часть запросов зависает), а с таймаутом по умолчанию (60 с) вебхук ждал минуту и отдавал Telegram 500."""
import asyncio

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

REQUEST_TIMEOUT = 10  # секунд на одну попытку
ATTEMPTS = 3


class RetrySession(AiohttpSession):
    def __init__(self, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        super().__init__(**kwargs)

    async def make_request(self, bot, method, timeout=None):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await super().make_request(bot, method, timeout=timeout)
            except TelegramNetworkError:
                if attempt == ATTEMPTS:
                    raise
                await asyncio.sleep(0.5 * attempt)
