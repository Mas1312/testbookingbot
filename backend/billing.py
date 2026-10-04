"""Подписка TeleSlot: оплата внутри платформенного бота через Telegram Payments (ЮKassa).

Схема: владелец бизнеса в @teleslotapp_bot жмёт «Оформить подписку» -> получает счёт на SUBSCRIPTION_PRICE_RUB ->
Telegram спрашивает у нас подтверждение (pre_checkout_query, ответить надо за 10 секунд) -> после оплаты приходит
successful_payment, мы продлеваем `businesses.paid_until` на SUBSCRIPTION_DAYS дней и пишем платёж в `payments`.

Рекуррентных списаний у ЮKassa через Telegram нет, поэтому каждый период владелец платит заново; за 3 дня до конца
и в день окончания бот напоминает. Блокировки бизнеса за неоплату пока НЕТ: это только учёт и напоминания.

Чек: ЮKassa через Telegram требует блок receipt в provider_data и email плательщика (их собирает сам Telegram)."""
import html
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

import bot_setup
import database
from config import PAYMENT_PROVIDER_TOKEN, PLATFORM_BUSINESS_ID, SUBSCRIPTION_DAYS, SUBSCRIPTION_PRICE_RUB

PAYLOAD_PREFIX = "sub:"
CURRENCY = "RUB"
DISPLAY_TZ = ZoneInfo("Europe/Moscow")
REMIND_BEFORE = timedelta(days=3)


def payments_enabled() -> bool:
    return bool(PAYMENT_PROVIDER_TOKEN)


def amount_kopecks() -> int:
    return SUBSCRIPTION_PRICE_RUB * 100


def make_payload(business_id: int) -> str:
    return f"{PAYLOAD_PREFIX}{business_id}"


def parse_payload(payload: str) -> int | None:
    if not payload or not payload.startswith(PAYLOAD_PREFIX):
        return None
    try:
        return int(payload[len(PAYLOAD_PREFIX):])
    except ValueError:
        return None


def format_paid_until(db_value: str) -> str:
    """'2026-11-03 10:15:00' (UTC) -> '03.11.2026' по московскому времени."""
    dt = datetime.strptime(db_value, database.DB_TIME_FORMAT).replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y")


def receipt_for(business_name: str) -> dict:
    """Данные для чека (54-ФЗ / «Мой налог» у самозанятого): одна позиция — услуга без НДС."""
    return {
        "items": [{
            "description": f"Подписка TeleSlot, {SUBSCRIPTION_DAYS} дн.: {business_name}"[:128],
            "quantity": "1.00",
            "amount": {"value": f"{SUBSCRIPTION_PRICE_RUB:.2f}", "currency": CURRENCY},
            "vat_code": 1,
            "payment_mode": "full_payment",
            "payment_subject": "service",
        }]
    }


def invoice_kwargs(business: dict) -> dict:
    """Параметры для Message.answer_invoice. need_email + send_email_to_provider — чтобы чек ушёл покупателю."""
    return dict(
        title="Подписка TeleSlot",
        description=f"«{business['name']}»: доступ к сервису на {SUBSCRIPTION_DAYS} дней"[:255],
        payload=make_payload(business["id"]),
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=[LabeledPrice(label=f"{SUBSCRIPTION_DAYS} дней", amount=amount_kopecks())],
        need_email=True,
        send_email_to_provider=True,
        provider_data=json.dumps({"receipt": receipt_for(business["name"])}, ensure_ascii=False),
    )


def validate_pre_checkout(payload: str, user_id: int, total_amount: int, currency: str) -> tuple[bool, str | None]:
    """Решение по pre_checkout_query. Telegram даёт 10 секунд, поэтому только быстрые проверки по БД."""
    business_id = parse_payload(payload)
    business = database.get_business(business_id) if business_id is not None else None
    if not business or business["id"] == PLATFORM_BUSINESS_ID:
        return False, "Бизнес не найден. Откройте /price и оформите подписку заново."
    if business["owner_tg_id"] != user_id:
        return False, "Оплатить подписку может только владелец бизнеса."
    if currency != CURRENCY or total_amount != amount_kopecks():
        return False, "Сумма счёта устарела. Откройте /price и оформите подписку заново."
    return True, None


def subscription_line(business: dict) -> str:
    if business.get("paid_until"):
        return f"«{business['name']}»: оплачено до {format_paid_until(business['paid_until'])}"
    return f"«{business['name']}»: пилот, оплата пока не требуется"


def price_text_for(owner_tg_id: int) -> str:
    """Тариф + статус бизнесов владельца (если он уже что-то подключал)."""
    text = bot_setup.PLATFORM_PRICE_TEXT
    businesses = database.get_businesses_by_owner(owner_tg_id, exclude_id=PLATFORM_BUSINESS_ID)
    if businesses:
        text += "\n\nВаши бизнесы:\n" + "\n".join(subscription_line(b) for b in businesses)
    return text


def pay_keyboard(business: dict, label: str = "Оплатить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"pay:{business['id']}")
    ]])


def choose_business_keyboard(businesses: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{b['name']}"[:60], callback_data=f"pay:{b['id']}")] for b in businesses
    ])


def apply_successful_payment(payer_tg_id: int, payment, now: datetime | None = None) -> dict | None:
    """Учитывает успешный платёж Telegram (aiogram SuccessfulPayment). None — если payload не наш."""
    business_id = parse_payload(payment.invoice_payload)
    business = database.get_business(business_id) if business_id is not None else None
    if not business:
        logging.error("Оплата с неизвестным payload %r от %s (charge %s)",
                      payment.invoice_payload, payer_tg_id, payment.telegram_payment_charge_id)
        return None
    paid_until, is_new = database.record_payment(
        business["id"], payer_tg_id, payment.total_amount, payment.currency, payment.invoice_payload,
        payment.telegram_payment_charge_id, payment.provider_payment_charge_id, SUBSCRIPTION_DAYS, now=now,
    )
    return {"business": business, "paid_until": paid_until, "is_new": is_new,
            "amount_rub": payment.total_amount / 100}


def payment_confirmation_text(business: dict, paid_until: str) -> str:
    return (
        f"Оплата получена. «{business['name']}» оплачен до {format_paid_until(paid_until)}.\n"
        "Чек придёт на указанную при оплате почту. Спасибо!"
    )


def operator_payment_text(result: dict, payer_tg_id: int) -> str:
    """Уходит через _safe_send (parse_mode=HTML), поэтому название бизнеса экранируем."""
    b = result["business"]
    return (f"Оплата {result['amount_rub']:.0f} ₽: бизнес №{b['id']} «{html.escape(b['name'])}» "
            f"(владелец {payer_tg_id}), оплачено до {format_paid_until(result['paid_until'])}.")


def reminder_text(business: dict, kind: str) -> str:
    """Уходит через _safe_send (parse_mode=HTML), поэтому название бизнеса экранируем."""
    date = format_paid_until(business["paid_until"])
    name = html.escape(business["name"])
    if kind == "3d":
        return f"Подписка на «{name}» заканчивается {date}. Продлить можно в один шаг:"
    return f"Подписка на «{name}» закончилась {date}. Продлить:"


async def send_subscription_reminders(now: datetime | None = None) -> int:
    """За 3 дня до конца и после окончания напоминает владельцу в платформенном боте (по разу на срок).
    Отметку «напомнили» ставим после первой попытки даже при неудаче: заблокировавшему бота не надо слать
    повторы каждую минуту. Возвращает число отправленных."""
    import notifications  # здесь, чтобы не создавать цикл импортов на уровне модуля

    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    platform = database.get_business(PLATFORM_BUSINESS_ID)
    if not platform:
        return 0
    sent = 0
    for business in database.get_subscription_reminder_candidates(exclude_id=PLATFORM_BUSINESS_ID):
        paid_until = business["paid_until"]
        remaining = datetime.strptime(paid_until, database.DB_TIME_FORMAT) - now
        if remaining <= timedelta(0):
            kind, already = "0d", business.get("remind0_for")
        elif remaining <= REMIND_BEFORE:
            kind, already = "3d", business.get("remind3_for")
        else:
            continue
        if already == paid_until:
            continue
        database.mark_subscription_reminded(business["id"], kind, paid_until)
        ok = await notifications._safe_send(
            notifications.get_bot(platform), business["owner_tg_id"], reminder_text(business, kind),
            reply_markup=pay_keyboard(business, "Продлить") if payments_enabled() else None,
        )
        sent += 1 if ok else 0
    return sent
