import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import database
from config import BOT_TOKEN, WEBAPP_URL

logging.basicConfig(level=logging.INFO)

# dp — общий на все бизнесы: один и тот же набор обработчиков работает для каждого
# бота, а то, какому бизнесу принадлежит конкретное обновление, приходит через
# business_id (aiogram сам прокидывает его в хендлер — см. server.py/feed_update
# и start_polling ниже, которые передают business_id как workflow-данные).
dp = Dispatcher()

# Нужен для локального polling-режима (backend/bot.py, запущенный напрямую).
# В облаке (вебхуки) сервер создаёт свои Bot-инстансы под каждый бизнес сам,
# этот экземпляр там не используется.
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None


@dp.message(CommandStart())
async def cmd_start(message: Message, business_id: int):
    business = database.get_business(business_id)
    business_name = business["name"] if business else "Записи"
    webapp_url = f"{WEBAPP_URL}/?biz={business_id}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Записаться", web_app=WebAppInfo(url=webapp_url))]
        ]
    )
    await message.answer(
        f"Привет! Это бот записи «{business_name}».\n\n"
        "Нажми кнопку ниже, чтобы выбрать услугу и удобное время.",
        reply_markup=keyboard,
    )


@dp.message()
async def fallback(message: Message):
    await message.answer("Нажми /start, чтобы открыть запись.")


async def main():
    """Локальная разработка: один бот из .env, обычный polling.
    В облаке (Render, USE_WEBHOOK=true) этот файл как скрипт не запускается —
    вебхуки поднимает server.py сразу для всех бизнесов из БД."""
    database.init_db()
    business = database.get_or_create_business_from_env(BOT_TOKEN, *_env_owner_and_name())
    logging.info("Локальный бот обслуживает business_id=%s (%s)", business["id"], business["name"])
    await dp.start_polling(bot, business_id=business["id"])


def _env_owner_and_name():
    from config import OWNER_TG_ID, BUSINESS_NAME
    return OWNER_TG_ID, BUSINESS_NAME


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN не задан. Скопируй .env.example в .env и вставь туда токен от @BotFather."
        )
    asyncio.run(main())
