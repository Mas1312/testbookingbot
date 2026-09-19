"""Уведомления о заявках: владельцу — о новой заявке (с кнопками «Подтвердить / Отклонить»),
клиенту — о том, что заявка принята / подтверждена / отменена.

Модуль общий для server.py (создание заявки, смена статуса из админки Mini App) и bot.py
(нажатие кнопок в чате владельца) — поэтому смена статуса живёт здесь, а не в одном из них.

Любая ошибка отправки (владелец не нажимал /start в своём боте, клиент заблокировал бота и т.п.)
только логируется: заявка от этого не должна ни теряться, ни падать с 500."""
import html
import logging
from datetime import date as date_cls

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database
import webhooks

# Допустимые переходы статусов для кнопок в чате. Из админки Mini App можно выставить
# любой статус (там владелец видит полный список и сам отвечает за выбор).
CHAT_TRANSITIONS = {
    "confirmed": {"new"},
    "cancelled": {"new", "confirmed"},
}

STATUS_LINES = {
    "new": "🆕 Новая",
    "confirmed": "✅ Подтверждена",
    "done": "🏁 Выполнена",
    "cancelled": "❌ Отменена",
}

_extra_bots: dict[int, Bot] = {}


def get_bot(business: dict) -> Bot:
    """Bot нужного бизнеса. В режиме вебхуков — тот, что создан при регистрации вебхука;
    в локальном режиме (сервер и polling — разные процессы) вебхуков нет, поэтому
    создаём и кэшируем свой экземпляр по токену бизнеса."""
    bot = webhooks.bots_by_business.get(business["id"])
    if bot is None:
        bot = _extra_bots.get(business["id"])
    if bot is None:
        bot = Bot(token=business["bot_token"])
        _extra_bots[business["id"]] = bot
    return bot


def _e(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def _when(booking: dict) -> str:
    if not booking["date"]:
        return "разовый заказ"
    try:
        day = date_cls.fromisoformat(booking["date"]).strftime("%d.%m.%Y")
    except ValueError:
        day = booking["date"]
    return f"{day} в {booking['time']}"


def _title(booking: dict) -> str:
    qty = f" × {booking['quantity']}" if booking["quantity"] > 1 else ""
    return f"{_e(booking['service_name'])}{qty}"


def owner_text(booking: dict) -> str:
    client = _e(booking["client_name"] or "Без имени")
    if booking["client_tg_id"]:
        client = f'<a href="tg://user?id={int(booking["client_tg_id"])}">{client}</a>'
    lines = [
        f"<b>Заявка №{booking['id']}</b> — {STATUS_LINES.get(booking['status'], booking['status'])}",
        f"{_title(booking)}",
        f"📅 {_when(booking)}",
        f"👤 {client}",
        f"💰 {booking['price'] * booking['quantity']} ₽",
    ]
    if booking["master_name"]:
        lines.insert(3, f"🧑‍🎨 Мастер: {_e(booking['master_name'])}")
    if booking["comment"]:
        lines.append(f"💬 {_e(booking['comment'])}")
    return "\n".join(lines)


def owner_keyboard(booking: dict) -> InlineKeyboardMarkup | None:
    bid = booking["id"]
    if booking["status"] == "new":
        row = [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bk:confirmed:{bid}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bk:cancelled:{bid}"),
        ]
    elif booking["status"] == "confirmed":
        row = [InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"bk:cancelled:{bid}")]
    else:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[row])


def client_text(booking: dict, status: str) -> str:
    what = f"{_title(booking)}, {_when(booking)}"
    if booking["master_name"]:
        what += f" (мастер: {_e(booking['master_name'])})"
    if status == "new":
        return f"📨 Заявка отправлена: {what}.\nЖдём подтверждения — напишем сюда, как только ответят."
    if status == "confirmed":
        return f"✅ Запись подтверждена: {what}. Ждём вас!"
    if status == "cancelled":
        return f"❌ К сожалению, запись отменена: {what}.\nМожно выбрать другое время — откройте запись заново."
    return ""


async def _safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
        return True
    except Exception as exc:
        logging.warning("Не удалось отправить сообщение chat_id=%s: %s", chat_id, exc)
        return False


async def notify_new_booking(business_id: int, booking_id: int):
    """Вызывается после создания заявки: владельцу — карточка с кнопками, клиенту — «заявка принята»."""
    business = database.get_business(business_id)
    booking = database.get_booking(business_id, booking_id)
    if not business or not booking:
        return
    bot = get_bot(business)
    await _safe_send(bot, business["owner_tg_id"], owner_text(booking), reply_markup=owner_keyboard(booking))
    if booking["client_tg_id"] and booking["client_tg_id"] != business["owner_tg_id"]:
        await _safe_send(bot, booking["client_tg_id"], client_text(booking, "new"))


async def notify_client_status(business: dict, booking: dict, status: str):
    """Сообщает клиенту о подтверждении/отмене. Про другие статусы молчим."""
    if status not in ("confirmed", "cancelled") or not booking["client_tg_id"]:
        return
    await _safe_send(get_bot(business), booking["client_tg_id"], client_text(booking, status))


async def change_status(business: dict, booking_id: int, status: str, allowed_from: set[str] | None = None):
    """Меняет статус заявки и уведомляет клиента. Возвращает (booking, changed):
    booking — актуальная заявка (None, если такой нет у этого бизнеса), changed — менялся ли статус.
    allowed_from — из каких статусов переход допустим (для кнопок в чате); None — любой."""
    booking = database.get_booking(business["id"], booking_id)
    if not booking:
        return None, False
    if booking["status"] == status or (allowed_from is not None and booking["status"] not in allowed_from):
        return booking, False
    database.update_booking_status(business["id"], booking_id, status)
    booking["status"] = status
    await notify_client_status(business, booking, status)
    return booking, True
