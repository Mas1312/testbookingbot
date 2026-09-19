"""Уведомления о заявках: владельцу — о новой заявке (с кнопками «Подтвердить / Отклонить»),
клиенту — о том, что заявка принята / подтверждена / отменена.

Модуль общий для server.py (создание заявки, смена статуса из админки Mini App) и bot.py
(нажатие кнопок в чате владельца) — поэтому смена статуса живёт здесь, а не в одном из них.

Любая ошибка отправки (владелец не нажимал /start в своём боте, клиент заблокировал бота и т.п.)
только логируется: заявка от этого не должна ни теряться, ни падать с 500."""
import html
import logging
from datetime import date as date_cls, datetime, timedelta, timezone as dt_timezone

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
    # В чате Telegram эмодзи уместны (в Mini App вместо них SVG-иконки).
    lines = [
        f"<b>Заявка №{booking['id']}</b> — {STATUS_LINES.get(booking['status'], booking['status'])}",
        _title(booking),
        f"📅 {_when(booking)}",
    ]
    if booking["master_name"]:
        lines.append(f"🧑‍🎨 Мастер: {_e(booking['master_name'])}")
    lines.append(f"👤 {client}")
    if booking["client_phone"]:
        lines.append(f"📞 {_e(booking['client_phone'])}")
    lines.append(f"💰 {booking['price'] * booking['quantity']} ₽")
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
    if status == "cancelled_by_client":
        return f"❌ Вы отменили запись: {what}."
    return ""


def client_keyboard(booking: dict) -> InlineKeyboardMarkup | None:
    """Кнопка отмены под сообщениями клиенту (двухшаговая, см. хендлер cx: в bot.py)."""
    if booking["status"] not in ("new", "confirmed"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"cx:ask:{booking['id']}"),
    ]])


def client_confirm_cancel_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, отменить", callback_data=f"cx:yes:{booking_id}"),
        InlineKeyboardButton(text="Нет", callback_data=f"cx:no:{booking_id}"),
    ]])


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
        await _safe_send(bot, booking["client_tg_id"], client_text(booking, "new"),
                         reply_markup=client_keyboard(booking))


async def notify_client_status(business: dict, booking: dict, status: str):
    """Сообщает клиенту о подтверждении/отмене. Про другие статусы молчим."""
    if status not in ("confirmed", "cancelled") or not booking["client_tg_id"]:
        return
    await _safe_send(get_bot(business), booking["client_tg_id"], client_text(booking, status),
                     reply_markup=client_keyboard(booking))


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


async def cancel_by_client(business: dict, booking_id: int, client_tg_id: int):
    """Отмена записи самим клиентом («Мои записи» или кнопка в чате). Освобождает слот и
    сообщает владельцу. Возвращает (booking, result): result — "ok" | "not_found" | "not_cancellable".
    Чужая заявка неотличима от несуществующей — не подсказываем, что такой id есть."""
    booking = database.get_booking(business["id"], booking_id)
    if not booking or booking["client_tg_id"] != client_tg_id:
        return None, "not_found"
    if not database.can_cancel_booking(booking, business["timezone"]):
        return booking, "not_cancellable"
    database.update_booking_status(business["id"], booking_id, "cancelled")
    booking["status"] = "cancelled"
    await _safe_send(
        get_bot(business), business["owner_tg_id"],
        "❌ <b>Клиент отменил запись</b>\n" + owner_text(booking),
    )
    return booking, "ok"


# ---------- Напоминания ----------

REMINDER_INTERVAL_SECONDS = 60


def reminder_text(booking: dict, timezone: str) -> str:
    today = database._now_in_business_tz(timezone).date()
    try:
        days = (date_cls.fromisoformat(booking["date"]) - today).days
    except ValueError:
        days = None
    day = {0: "сегодня", 1: "завтра"}.get(days, _when(booking).split(" в ")[0])
    text = f"⏰ Напоминаем о записи {day} в {booking['time']}: {_title(booking)}"
    if booking["master_name"]:
        text += f" (мастер: {_e(booking['master_name'])})"
    return text + "."


def _parse_created_at(value: str | None) -> datetime | None:
    """created_at в SQLite — UTC без пояса ('YYYY-MM-DD HH:MM:SS')."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


async def send_due_reminders(now: datetime | None = None) -> int:
    """Шлёт клиентам напоминания за сутки и за 2 часа до записи. Возвращает число отправленных.

    Правила:
      - Флаги ставим ДО отправки: если клиент заблокировал бота, не долбим его каждую минуту.
      - Если подошли оба окна сразу (сервер спал), шлём одно — «ближайшее» (за 2 часа).
      - Напоминание не шлём, если клиент записался уже внутри этого окна (записался за 5 часов —
        «за сутки» не нужно; за 90 минут — не нужно и «за 2 часа»).
      - Время в тексте абсолютное («завтра в 14:00»), поэтому опоздавшее напоминание не врёт."""
    now = now or datetime.now(dt_timezone.utc)
    sent = 0
    for row in database.get_reminder_candidates():
        start = database.booking_start(row["date"], row["time"], row["timezone"])
        if start is None:
            continue
        left = start - now
        due = []
        if not row["reminded_24h"] and left <= timedelta(hours=24):
            due.append("24h")
        if not row["reminded_2h"] and left <= timedelta(hours=2):
            due.append("2h")
        if not due:
            continue
        database.mark_reminded(row["id"], due)
        if left <= timedelta(0):
            continue  # запись уже началась/прошла

        window = timedelta(hours=2) if "2h" in due else timedelta(hours=24)
        created = _parse_created_at(row["created_at"])
        if created is not None and start - created < window:
            continue

        business = database.get_business(row["business_id"])
        booking = database.get_booking(row["business_id"], row["id"])
        if not business or not booking:
            continue
        if await _safe_send(get_bot(business), booking["client_tg_id"],
                            reminder_text(booking, business["timezone"]),
                            reply_markup=client_keyboard(booking)):
            sent += 1
    return sent
