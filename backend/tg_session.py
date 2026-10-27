"""HTTP-сессия aiogram для нестабильной связи с api.telegram.org (с сервера новые соединения то проходят,
то нет): короткий таймаут на установку соединения, быстрые повторы, только IPv4 (IPv6 до Telegram с сервера
недоступен) и долгоживущие keep-alive соединения, чтобы реже открывать новые. Без этого вебхук ждал по минуте
(таймаут aiogram по умолчанию 60 с) и отдавал Telegram 500."""
import asyncio
import socket

from aiohttp import ClientTimeout
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

CONNECT_TIMEOUT = 3   # секунд на установку соединения: в «плохие окна» оно не проходит вообще, ждать нечего
TOTAL_TIMEOUT = 10    # секунд на одну попытку целиком
ATTEMPTS = 4


class RetrySession(AiohttpSession):
    def __init__(self, **kwargs):
        kwargs.setdefault("timeout", TOTAL_TIMEOUT)
        super().__init__(**kwargs)
        self._connector_init["family"] = socket.AF_INET
        self._connector_init["keepalive_timeout"] = 120

    async def make_request(self, bot, method, timeout=None):
        if timeout is None:
            timeout = ClientTimeout(total=TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, sock_connect=CONNECT_TIMEOUT)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await super().make_request(bot, method, timeout=timeout)
            except TelegramNetworkError:
                if attempt == ATTEMPTS:
                    raise
                await asyncio.sleep(0.3 * attempt)
