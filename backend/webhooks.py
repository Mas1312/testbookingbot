"""Регистрация вебхуков ботов у Telegram — вынесено отдельным модулем, чтобы им могли
пользоваться и server.py (поднимает вебхуки всех бизнесов при старте), и bot.py
(регистрирует вебхук сразу же, как только через /newbusiness завели новый бизнес),
без циклического импорта друг друга."""
import asyncio
import hashlib
import logging

from aiogram import Bot

from config import USE_WEBHOOK, WEBAPP_URL

# business_id -> aiogram.Bot, создаются один раз и переиспользуются на каждый
# входящий вебхук-запрос (чтобы не открывать новую aiohttp-сессию на запрос).
bots_by_business: dict[int, Bot] = {}


def webhook_secret_for(bot_token: str) -> str:
    """Детерминированный секрет для проверки X-Telegram-Bot-Api-Secret-Token,
    свой у каждого бота, ничего дополнительно хранить/генерировать не нужно."""
    return hashlib.sha256(bot_token.encode()).hexdigest()[:32]


async def register_webhook(business: dict):
    """Регистрирует вебхук для одного бизнеса. Вне облака (USE_WEBHOOK=false,
    локальная разработка) ничего не делает — там бот работает через polling."""
    if not USE_WEBHOOK:
        return
    bot_instance = Bot(token=business["bot_token"])
    bots_by_business[business["id"]] = bot_instance
    try:
        async with asyncio.timeout(15):
            await bot_instance.set_webhook(
                url=f"{WEBAPP_URL}/webhook/{business['id']}",
                secret_token=webhook_secret_for(business["bot_token"]),
                drop_pending_updates=True,
            )
    except Exception:
        logging.exception("Не удалось зарегистрировать вебхук для business_id=%s", business["id"])


async def register_all_webhooks(businesses: list[dict]):
    for business in businesses:
        await register_webhook(business)
