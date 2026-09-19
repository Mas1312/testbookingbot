import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import database
import notifications
import webhooks
from config import BOT_TOKEN, WEBAPP_URL

logging.basicConfig(level=logging.INFO)

# dp — общий на все бизнесы: один и тот же набор обработчиков работает для каждого
# бота, а то, какому бизнесу принадлежит конкретное обновление, приходит через
# business_id (aiogram сам прокидывает его в хендлер — см. server.py/feed_update
# и start_polling ниже, которые передают business_id как workflow-данные).
# FSM-хранилище по умолчанию (MemoryStorage) — для прототипа достаточно; в облаке
# это значит, что диалог /newbusiness прервётся, если сервис перезапустится
# посреди него (Render иногда так делает при простое) — тогда просто начать заново.
dp = Dispatcher()

# Нужен для локального polling-режима (backend/bot.py, запущенный напрямую).
# В облаке (вебхуки) сервер создаёт свои Bot-инстансы под каждый бизнес сам,
# этот экземпляр там не используется.
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None


@dp.message(CommandStart())
async def cmd_start(message: Message, business_id: int):
    business = database.get_business(business_id)
    business_name = business["name"] if business else "Записи"
    webapp_url = f"{WEBAPP_URL}/?business_id={business_id}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Записаться", web_app=WebAppInfo(url=webapp_url))]
        ]
    )
    text = (
        f"Привет! Это бот записи «{business_name}».\n\n"
        "Нажми кнопку ниже, чтобы выбрать услугу и удобное время."
    )
    if business and message.from_user.id == business["owner_tg_id"]:
        text += "\n\n🔔 Вы владелец: новые заявки будут приходить сюда с кнопками «Подтвердить / Отклонить»."
    await message.answer(text, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("bk:"))
async def on_booking_button(callback: CallbackQuery, business_id: int):
    """Кнопки под карточкой заявки в чате владельца: bk:<новый статус>:<id заявки>."""
    business = database.get_business(business_id)
    # Владельца проверяем по id нажавшего: callback_data подделать может кто угодно,
    # а from_user Telegram подписывает сам.
    if not business or callback.from_user.id != business["owner_tg_id"]:
        await callback.answer("Доступно только владельцу", show_alert=True)
        return

    try:
        _, status, booking_id = callback.data.split(":")
        booking_id = int(booking_id)
    except ValueError:
        await callback.answer()
        return
    if status not in notifications.CHAT_TRANSITIONS:
        await callback.answer()
        return

    booking, changed = await notifications.change_status(
        business, booking_id, status, allowed_from=notifications.CHAT_TRANSITIONS[status]
    )
    if booking is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if changed:
        await callback.answer("Готово, клиент уведомлён" if booking["client_tg_id"] else "Готово")
    else:
        # Уже обработана (в админке или повторное нажатие) — просто освежаем карточку.
        await callback.answer("Статус уже изменён: " + notifications.STATUS_LINES.get(booking["status"], booking["status"]))

    if callback.message:
        try:
            await callback.message.edit_text(
                notifications.owner_text(booking),
                parse_mode="HTML",
                reply_markup=notifications.owner_keyboard(booking),
            )
        except Exception:
            logging.debug("Карточка заявки не изменилась", exc_info=True)


# ======================================================================
# Онбординг нового бизнеса: /newbusiness -> название -> токен своего бота
# ======================================================================

class NewBusinessStates(StatesGroup):
    waiting_name = State()
    waiting_token = State()


@dp.message(Command("newbusiness"))
async def cmd_newbusiness(message: Message, state: FSMContext):
    await state.set_state(NewBusinessStates.waiting_name)
    await message.answer(
        "Заведём новую запись для твоего бизнеса.\n\n"
        "Как он называется? Например: «Барбершоп у Ивана» или «Цветы у Насти».\n\n"
        "В любой момент можно отменить — /cancel"
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Ок, отменил.")


@dp.message(NewBusinessStates.waiting_name)
async def process_business_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Напиши текстом, или /cancel.")
        return

    await state.update_data(business_name=name)
    await state.set_state(NewBusinessStates.waiting_token)
    await message.answer(
        f"Принял: «{name}».\n\n"
        "Теперь нужен токен твоего собственного Telegram-бота — именно через него твои "
        "клиенты будут открывать запись.\n\n"
        "1. Открой @BotFather\n"
        "2. Отправь ему /newbot, придумай имя и username (должен заканчиваться на bot)\n"
        "3. Он пришлёт токен вида 123456789:ABC... — перешли его сюда\n\n"
        "Отмена — /cancel"
    )


@dp.message(NewBusinessStates.waiting_token)
async def process_bot_token(message: Message, state: FSMContext):
    token = (message.text or "").strip()

    if database.get_business_by_bot_token(token):
        await message.answer("Этот бот уже подключён к системе — пришли токен другого бота, или /cancel.")
        return

    candidate_bot = Bot(token=token)
    try:
        me = await candidate_bot.get_me()
    except Exception:
        await message.answer(
            "Не получилось проверить этот токен — похоже, он неверный или бот ещё не активирован. "
            "Проверь и пришли ещё раз, или /cancel."
        )
        return
    finally:
        await candidate_bot.session.close()

    data = await state.get_data()
    business_name = data["business_name"]
    owner_tg_id = message.from_user.id

    business = database.create_business(owner_tg_id, business_name, token)
    await state.clear()
    await webhooks.register_webhook(business)

    await message.answer(
        f"Готово! Бизнес «{business_name}» подключён к @{me.username}.\n\n"
        f"Напиши этому боту /start и нажми «Записаться» — откроется твоя Mini App. "
        "Там же, во вкладке «Управление», можно добавить услуги и настроить оформление "
        "(доступно только тебе — вход по этому Telegram-аккаунту).\n\n"
        "🔔 Важно: именно в этом боте нажми /start — иначе Telegram не даст ему присылать "
        "тебе уведомления о новых заявках."
    )


@dp.message()
async def fallback(message: Message):
    await message.answer("Нажми /start, чтобы открыть запись, или /newbusiness, чтобы завести свой бизнес.")


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
