import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import bot_setup
import database
import notifications
import webhooks
from config import BOT_TOKEN, PLATFORM_BUSINESS_ID, WEBAPP_URL

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
    if business_id == PLATFORM_BUSINESS_ID:
        # TeleSlot сам по себе не бот записи — это инструмент для подключения и настройки
        # СВОИХ ботов, поэтому у него нет кнопки «Записаться» и Mini App клиента: только
        # приглашение начать /newbusiness. У бизнес-ботов ниже — обычный клиентский /start.
        await message.answer(
            bot_setup.PLATFORM_START_TEXT,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Подключить бизнес", callback_data="start_newbusiness")]
            ]),
        )
        return

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
        "Нажми кнопку ниже, чтобы выбрать услугу и удобное время. Свои записи — /my."
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


@dp.message(Command("my"))
async def cmd_my(message: Message, business_id: int):
    """Предстоящие записи клиента в чате — каждая с кнопкой отмены."""
    business = database.get_business(business_id)
    if not business:
        return
    upcoming = [
        b for b in database.get_client_bookings(business_id, message.from_user.id)
        if database.can_cancel_booking(b, business["timezone"])
    ]
    if not upcoming:
        await message.answer("У вас нет предстоящих записей. Нажмите /start, чтобы записаться.")
        return
    upcoming.sort(key=lambda b: (b["date"] or "9999", b["time"] or ""))
    for booking in upcoming[:5]:
        await message.answer(
            notifications.client_text(booking, booking["status"]),
            parse_mode="HTML",
            reply_markup=notifications.client_keyboard(booking),
        )


@dp.callback_query(F.data.startswith("cx:"))
async def on_client_cancel_button(callback: CallbackQuery, business_id: int):
    """Отмена записи клиентом из чата: cx:ask|yes|no:<id заявки>. Отмена в два шага —
    чтобы случайное касание кнопки под напоминанием не стоило клиенту записи."""
    business = database.get_business(business_id)
    try:
        _, action, booking_id = callback.data.split(":")
        booking_id = int(booking_id)
    except ValueError:
        await callback.answer()
        return
    booking = database.get_booking(business_id, booking_id) if business else None
    # Кнопки видит только сам клиент, но callback_data подделать может кто угодно.
    if not booking or booking["client_tg_id"] != callback.from_user.id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    async def set_markup(markup):
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=markup)
            except Exception:
                logging.debug("Не удалось обновить кнопки", exc_info=True)

    if action == "ask":
        await set_markup(notifications.client_confirm_cancel_keyboard(booking_id))
        await callback.answer("Отменить запись?")
    elif action == "no":
        await set_markup(notifications.client_keyboard(booking))
        await callback.answer()
    elif action == "yes":
        booking, result = await notifications.cancel_by_client(business, booking_id, callback.from_user.id)
        if result == "ok":
            await callback.answer("Запись отменена")
            if callback.message:
                try:
                    await callback.message.edit_text(
                        notifications.client_text(booking, "cancelled_by_client"), parse_mode="HTML"
                    )
                except Exception:
                    logging.debug("Не удалось обновить сообщение", exc_info=True)
        else:
            await callback.answer("Эту запись уже нельзя отменить", show_alert=True)
            await set_markup(None)
    else:
        await callback.answer()


# ======================================================================
# Онбординг нового бизнеса: /newbusiness -> название -> токен своего бота
# ======================================================================

class NewBusinessStates(StatesGroup):
    waiting_name = State()
    waiting_token = State()


async def start_newbusiness_dialog(answer, state: FSMContext):
    """Общее начало диалога подключения бизнеса — вызывается и из команды /newbusiness,
    и из кнопки «Подключить бизнес» под /start в TeleSlot. answer — message.answer или
    callback.message.answer (сигнатура одна и та же)."""
    await state.set_state(NewBusinessStates.waiting_name)
    await answer(
        "Заведём онлайн-запись для вашего бизнеса.\n\n"
        "Как он называется? Например: «Барбершоп у Ивана» или «Цветы у Насти».\n\n"
        "В любой момент можно отменить — /cancel"
    )


@dp.message(Command("newbusiness"))
async def cmd_newbusiness(message: Message, state: FSMContext, business_id: int):
    if business_id != PLATFORM_BUSINESS_ID:
        # Клиент чужого бота не должен случайно завести «свой бизнес» (и не должен видеть, что так можно).
        await message.answer(bot_setup.NOT_PLATFORM_TEXT)
        return
    await start_newbusiness_dialog(message.answer, state)


@dp.callback_query(F.data == "start_newbusiness")
async def on_start_newbusiness_button(callback: CallbackQuery, state: FSMContext, business_id: int):
    """Кнопка «Подключить бизнес» под /start в TeleSlot — тот же диалог, что и /newbusiness."""
    await callback.answer()
    if business_id != PLATFORM_BUSINESS_ID or not callback.message:
        return
    await start_newbusiness_dialog(callback.message.answer, state)


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

    await state.update_data(business_name=name[:80])
    await state.set_state(NewBusinessStates.waiting_token)
    await message.answer(bot_setup.ask_token_text(name[:80]), disable_web_page_preview=True)


@dp.message(NewBusinessStates.waiting_token)
async def process_bot_token(message: Message, state: FSMContext):
    token = (message.text or "").strip()

    if not bot_setup.looks_like_token(token):
        await message.answer(bot_setup.NOT_A_TOKEN_TEXT)
        return
    # Токен — ключ от бота: убираем его из истории чата (боту это разрешено в личных чатах, сообщения
    # моложе 48 часов). Не получилось — не страшно, идём дальше.
    try:
        await message.delete()
    except Exception:
        pass

    if database.get_business_by_bot_token(token):
        await message.answer(bot_setup.TOKEN_ALREADY_USED_TEXT)
        return

    candidate_bot = Bot(token=token)
    try:
        me = await candidate_bot.get_me()
    except Exception:
        await message.answer(bot_setup.TOKEN_REJECTED_TEXT)
        return
    finally:
        await candidate_bot.session.close()

    data = await state.get_data()
    business_name = data["business_name"]
    owner_tg_id = message.from_user.id

    business = database.create_business(owner_tg_id, business_name, token)
    database.set_bot_username(business["id"], me.username)
    await state.clear()
    await webhooks.register_webhook(business)
    await bot_setup.configure_business_bot(business)

    await message.answer(bot_setup.done_text(business_name, me.username), disable_web_page_preview=True)


@dp.message()
async def fallback(message: Message, business_id: int):
    if business_id == PLATFORM_BUSINESS_ID:
        await message.answer("Нажмите /start, чтобы открыть запись, или /newbusiness, чтобы завести свой бизнес.")
    else:
        await message.answer(bot_setup.NOT_PLATFORM_TEXT)


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
