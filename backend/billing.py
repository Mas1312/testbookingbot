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
from config import (PAYMENT_PROVIDER_TOKEN, PLATFORM_BUSINESS_ID, SUBSCRIPTION_DAYS, SUBSCRIPTION_PRICE_RUB,
                    TRIAL_DAYS)

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


INVOICE_DESCRIPTION_LIMIT = 255  # лимит Telegram на описание счёта


def invoice_description(business_name: str) -> str:
    """Описание счёта. Оплата — это акцепт оферты (см. оферту, п. 1.3), поэтому ссылка на неё стоит прямо в счёте.
    Под лимит Telegram подрезаем название бизнеса, а не хвост со ссылкой."""
    tail = f"»: доступ на {SUBSCRIPTION_DAYS} дней. Оплата означает согласие с офертой: {bot_setup.OFFER_URL}"
    room = INVOICE_DESCRIPTION_LIMIT - len(tail) - 1  # 1 — открывающая «
    name = business_name if len(business_name) <= room else business_name[: max(room - 1, 0)] + "…"
    return f"«{name}{tail}"


def invoice_kwargs(business: dict) -> dict:
    """Параметры для Message.answer_invoice. need_email + send_email_to_provider — чтобы чек ушёл покупателю."""
    return dict(
        title="Подписка TeleSlot",
        description=invoice_description(business["name"]),
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


SUSPENDED_STATUSES = {"expired", "trial_expired"}


def _days_left(remaining: timedelta) -> int:
    return remaining.days + (1 if remaining.seconds else 0)


def subscription_status(business: dict, now: datetime | None = None) -> tuple[str, int | None]:
    """(статус, осталось дней или None).

    Есть оплата (paid_until): 'active' | 'expiring' (осталось <= 3 дней) | 'expired'.
    Оплаты нет, но был пробный период (trial_until): 'trial' | 'trial_expired'.
    Ни того ни другого: 'pilot' — бесплатный пилот без срока (бизнесы, заведённые до пробного периода)."""
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    if business.get("paid_until"):
        remaining = datetime.strptime(business["paid_until"], database.DB_TIME_FORMAT) - now
        if remaining <= timedelta(0):
            return "expired", 0
        return ("expiring" if remaining <= REMIND_BEFORE else "active"), _days_left(remaining)
    if business.get("trial_until"):
        remaining = datetime.strptime(business["trial_until"], database.DB_TIME_FORMAT) - now
        if remaining <= timedelta(0):
            return "trial_expired", 0
        return "trial", _days_left(remaining)
    return "pilot", None


def is_suspended(business: dict, now: datetime | None = None) -> bool:
    """Запись для клиентов приостановлена: пробный период или оплаченный срок закончились. Платформенный бот
    и бесплатный пилот (без trial_until и paid_until) никогда не приостанавливаются."""
    if business.get("id") == PLATFORM_BUSINESS_ID:
        return False
    return subscription_status(business, now)[0] in SUSPENDED_STATUSES


def cabinet_payload(owner_tg_id: int, now: datetime | None = None) -> dict:
    """Данные для кабинета TeleSlot (Mini App платформенного бота): бизнесы владельца, их подписка, история оплат."""
    items = []
    for b in database.get_businesses_by_owner(owner_tg_id, exclude_id=PLATFORM_BUSINESS_ID):
        status, days_left = subscription_status(b, now)
        username = b.get("bot_username")
        items.append({
            "id": b["id"], "name": b["name"], "bot_username": username,
            "link": bot_setup.bot_link(username) if username else None,
            "status": status, "days_left": days_left, "suspended": status in SUSPENDED_STATUSES,
            "paid_until": format_paid_until(b["paid_until"]) if b.get("paid_until") else None,
            "trial_until": format_paid_until(b["trial_until"]) if b.get("trial_until") else None,
            **database.get_business_stats(b),
        })
    history = [{
        "business_name": p["business_name"], "amount_rub": p["amount"] / 100,
        "paid_at": format_paid_until(p["created_at"]), "period_to": format_paid_until(p["period_to"]),
    } for p in database.get_payments_by_owner(owner_tg_id)]
    return {
        "businesses": items, "history": history, "payments_enabled": payments_enabled(),
        "price_rub": SUBSCRIPTION_PRICE_RUB, "days": SUBSCRIPTION_DAYS, "trial_days": TRIAL_DAYS,
        "support": bot_setup.SUPPORT_CONTACT,
    }


def subscription_line(business: dict, now: datetime | None = None) -> str:
    status, _ = subscription_status(business, now)
    name = business["name"]
    if status in ("active", "expiring"):
        return f"«{name}»: оплачено до {format_paid_until(business['paid_until'])}"
    if status == "expired":
        return f"«{name}»: срок закончился {format_paid_until(business['paid_until'])}, запись приостановлена"
    if status == "trial":
        return f"«{name}»: пробный период до {format_paid_until(business['trial_until'])}"
    if status == "trial_expired":
        return f"«{name}»: пробный период закончился, запись приостановлена"
    return f"«{name}»: пилот, оплата пока не требуется"


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


TRIAL_REMIND_BEFORE = timedelta(days=1)  # пробный период короткий (3 дня): предупреждаем за сутки, а не за 3 дня


def reminder_text(business: dict, kind: str, end: str | None = None, trial: bool = False) -> str:
    """Уходит через _safe_send (parse_mode=HTML), поэтому название бизнеса экранируем.
    kind: '3d' — скоро конец, '0d' — уже закончилось (запись приостановлена). end — конец срока (по умолчанию paid_until)."""
    date = format_paid_until(end or business["paid_until"])
    name = html.escape(business["name"])
    if trial:
        if kind == "3d":
            return (f"Пробный период «{name}» заканчивается {date}. После этого запись для клиентов "
                    "приостановится. Оформить подписку:")
        return (f"Пробный период «{name}» закончился: запись для клиентов приостановлена. "
                "После оплаты она сразу заработает снова:")
    if kind == "3d":
        return (f"Подписка на «{name}» заканчивается {date}. После окончания запись для клиентов "
                "приостановится. Продлить можно в один шаг:")
    return (f"Подписка на «{name}» закончилась {date}: запись для клиентов приостановлена. "
            "После оплаты она сразу заработает снова:")


async def send_subscription_reminders(now: datetime | None = None) -> int:
    """Напоминает владельцу в платформенном боте (по разу на срок): за 3 дня до конца оплаченного периода,
    за сутки до конца пробного и после окончания любого из них. Отметку «напомнили» ставим после первой
    попытки даже при неудаче: заблокировавшему бота не надо слать повторы каждую минуту.
    Возвращает число отправленных."""
    import notifications  # здесь, чтобы не создавать цикл импортов на уровне модуля

    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    platform = database.get_business(PLATFORM_BUSINESS_ID)
    if not platform:
        return 0
    sent = 0
    for business in database.get_subscription_reminder_candidates(exclude_id=PLATFORM_BUSINESS_ID):
        # Оплата главнее пробного периода: если она есть, срок и окно напоминаний — по ней.
        trial = not business.get("paid_until")
        end = business["trial_until"] if trial else business["paid_until"]
        remaining = datetime.strptime(end, database.DB_TIME_FORMAT) - now
        if remaining <= timedelta(0):
            kind, already = "0d", business.get("remind0_for")
        elif remaining <= (TRIAL_REMIND_BEFORE if trial else REMIND_BEFORE):
            kind, already = "3d", business.get("remind3_for")
        else:
            continue
        if already == end:
            continue
        database.mark_subscription_reminded(business["id"], kind, end)
        ok = await notifications._safe_send(
            notifications.get_bot(platform), business["owner_tg_id"],
            reminder_text(business, kind, end=end, trial=trial),
            reply_markup=pay_keyboard(business, "Оплатить" if trial else "Продлить") if payments_enabled() else None,
        )
        sent += 1 if ok else 0
    return sent
