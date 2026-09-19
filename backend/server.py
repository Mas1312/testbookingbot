import asyncio
import base64
import binascii
import logging
import os
import re
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from aiogram.types import Update

import database
import notifications
import webhooks
from config import SERVER_PORT, BUSINESS_NAME, OWNER_TG_ID, USE_WEBHOOK, BOT_TOKEN, DEV_SKIP_INITDATA_CHECK
from bot import dp as tg_dp
from telegram_auth import verify_init_data

app = FastAPI(title="Booking Mini App API")

# CORS открыт для простоты локальной разработки.
# Для продакшена сузить allow_origins до своего домена.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Запрещаем браузеру кэшировать index.html/app.js/style.css без проверки —
    иначе после каждого обновления файлов старая версия «зависает» в кэше вкладки."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class BookingRequest(BaseModel):
    business_id: int
    service_id: int
    master_id: int | None = None     # обязателен, если у бизнеса включён выбор мастера
    date: str | None = None
    time: str | None = None
    quantity: int = 1
    comment: str | None = None
    client_name: str = "Клиент"
    init_data: str = ""              # подпись Telegram WebApp — из неё берём настоящий id клиента
    client_tg_id: int | None = None  # дев-фолбэк вне Telegram, см. DEV_SKIP_INITDATA_CHECK
    client_phone: str | None = Field(default=None, max_length=40)
    consent: bool = False            # согласие на обработку ПДн (обязательно, если указан телефон)


class CancelRequest(BaseModel):
    business_id: int
    init_data: str = ""              # подпись Telegram WebApp — из неё берём id клиента
    client_tg_id: int | None = None  # дев-фолбэк вне Telegram, см. DEV_SKIP_INITDATA_CHECK


class ServiceRequest(BaseModel):
    business_id: int
    name: str
    price: int = Field(ge=0)
    duration_min: int = Field(default=0, ge=0)
    type: str = "slot"        # 'slot' или 'order'
    is_active: bool = True
    description: str | None = Field(default=None, max_length=600)
    image_id: int | None = None      # картинка из /api/admin/media
    init_data: str = ""              # подпись Telegram WebApp — см. check_owner
    owner_tg_id: int | None = None   # дев-фолбэк вне Telegram, см. DEV_SKIP_INITDATA_CHECK


class StatusRequest(BaseModel):
    business_id: int
    status: str               # 'new' | 'confirmed' | 'done' | 'cancelled'
    init_data: str = ""
    owner_tg_id: int | None = None


HEX_COLOR = r"^#[0-9a-fA-F]{6}$"  # цвета попадают в CSS всех клиентов — принимаем только #RRGGBB


class ThemeRequest(BaseModel):
    business_id: int
    bg_color: str = Field(pattern=HEX_COLOR)
    surface_color: str = Field(pattern=HEX_COLOR)
    text_color: str = Field(pattern=HEX_COLOR)
    hint_color: str = Field(pattern=HEX_COLOR)
    primary_color: str = Field(pattern=HEX_COLOR)
    primary_text_color: str = Field(pattern=HEX_COLOR)
    danger_color: str = Field(pattern=HEX_COLOR)
    success_color: str = Field(pattern=HEX_COLOR)
    radius: int = Field(ge=0, le=60)
    bg_mode: Literal["color", "gradient", "image"] = "color"
    bg_color2: str = Field(default="#FFFFFF", pattern=HEX_COLOR)
    bg_angle: int = Field(default=160, ge=0, le=360)
    bg_image_id: int | None = None
    bg_overlay: int = Field(default=60, ge=0, le=90)      # % «вуали» цвета фона поверх картинки
    primary_color2: str | None = Field(default=None, pattern=HEX_COLOR)  # задан — кнопки градиентом
    card_style: Literal["shadow", "flat", "outline"] = "shadow"
    font: Literal["sans", "serif", "rounded"] = "sans"
    init_data: str = ""
    owner_tg_id: int | None = None


class SavedThemeRequest(ThemeRequest):
    name: str = Field(min_length=1, max_length=40)


class MediaUploadRequest(BaseModel):
    business_id: int
    data_url: str = Field(max_length=1_000_000)   # data:image/...;base64,... (~730 КБ картинки)
    init_data: str = ""
    owner_tg_id: int | None = None


class LogoRequest(BaseModel):
    business_id: int
    media_id: int | None = None
    init_data: str = ""
    owner_tg_id: int | None = None


class BusinessSettingsRequest(BaseModel):
    business_id: int
    collect_phone: Literal["off", "optional", "required"]
    privacy_url: str | None = Field(default=None, max_length=300)
    init_data: str = ""
    owner_tg_id: int | None = None


class MasterRequest(BaseModel):
    business_id: int
    name: str = Field(min_length=1, max_length=80)
    service_ids: list[int] = []      # пусто = мастер ведёт все позиции по расписанию
    is_active: bool = True
    init_data: str = ""
    owner_tg_id: int | None = None


class MastersSettingsRequest(BaseModel):
    business_id: int
    use_masters: bool
    init_data: str = ""
    owner_tg_id: int | None = None


class ScheduleRequest(BaseModel):
    business_id: int
    timezone: str
    work_start_hour: int = Field(ge=0, le=23)
    work_end_hour: int = Field(ge=1, le=24)
    slot_step_minutes: int = Field(ge=5, le=240)
    days_ahead: int = Field(ge=1, le=60)
    init_data: str = ""
    owner_tg_id: int | None = None


def resolve_business(business_id: int | None):
    """Определяет, какой бизнес обслуживать. Если business_id не передан (например,
    открыли сервер напрямую в браузере без ?business_id=... из ссылки бота) — используем
    бизнес по умолчанию (тот, что привязан к BOT_TOKEN из .env), чтобы старые ссылки
    и локальная разработка без параметра продолжали работать."""
    resolved_id = business_id if business_id is not None else DEFAULT_BUSINESS_ID
    business = database.get_business(resolved_id) if resolved_id else None
    if not business:
        raise HTTPException(status_code=404, detail="Бизнес не найден")
    return business


def check_owner(business: dict, init_data: str, owner_tg_id_fallback: int | None = None):
    """Подтверждает, что запрос реально пришёл от владельца бизнеса.

    Источник правды — подпись Telegram WebApp initData (HMAC под токеном ИМЕННО этого
    бизнеса, см. telegram_auth.verify_init_data): её нельзя подделать, не зная токен бота.
    Присланный клиентом owner_tg_id больше НИКОГДА не считается доказательством сам по
    себе — только как дев-фолбэк вне настоящего Telegram, и только если явно включено
    DEV_SKIP_INITDATA_CHECK (см. config.py; в проде должно быть выключено)."""
    verified_user_id = verify_init_data(init_data, business["bot_token"])
    if verified_user_id is None:
        if DEV_SKIP_INITDATA_CHECK and owner_tg_id_fallback:
            verified_user_id = owner_tg_id_fallback
        else:
            raise HTTPException(status_code=403, detail="Не удалось подтвердить владельца")
    if verified_user_id != business["owner_tg_id"]:
        raise HTTPException(status_code=403, detail="Доступно только владельцу")


def resolve_client(business: dict, init_data: str, client_tg_id_fallback: int | None = None) -> int:
    """Telegram id клиента из подписанного initData (как check_owner — для владельца).
    Присланному id верим только в дев-режиме."""
    user_id = verify_init_data(init_data, business["bot_token"])
    if user_id is None and DEV_SKIP_INITDATA_CHECK and client_tg_id_fallback:
        user_id = client_tg_id_fallback
    if user_id is None:
        raise HTTPException(status_code=403, detail="Откройте запись через Telegram, чтобы увидеть свои записи")
    return user_id


def media_url(media_id: int | None) -> str | None:
    return f"/api/media/{media_id}" if media_id else None


def theme_with_urls(theme: dict) -> dict:
    return {**theme, "bg_image_url": media_url(theme.get("bg_image_id"))}


def with_image_urls(services: list[dict]) -> list[dict]:
    return [{**s, "image_url": media_url(s.get("image_id"))} for s in services]


def require_own_media(business_id: int, media_id: int | None):
    if media_id is not None and not database.media_belongs(business_id, media_id):
        raise HTTPException(status_code=400, detail="Картинка не найдена — загрузите её заново")


def clean_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def normalize_phone(raw: str) -> str | None:
    """Приводит номер к виду +7XXXXXXXXXX (для РФ/СНГ) или +<цифры> (для остальных).
    Возвращает None, если это не похоже на телефон."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if 10 <= len(digits) <= 15 and raw.strip().startswith("+"):
        return "+" + digits
    return None


def resolve_phone(business: dict, booking) -> tuple[str | None, str | None]:
    """Телефон и момент согласия для заявки. Если владелец сбор телефона не включал,
    присланный номер молча игнорируем — лишних персональных данных не храним."""
    mode = business["collect_phone"]
    if mode == "off":
        return None, None
    raw = (booking.client_phone or "").strip()
    if not raw:
        if mode == "required":
            raise HTTPException(status_code=400, detail="Укажите номер телефона")
        return None, None
    phone = normalize_phone(raw)
    if not phone:
        raise HTTPException(status_code=400, detail="Проверьте номер телефона")
    if not booking.consent:
        raise HTTPException(status_code=400, detail="Нужно согласие на обработку персональных данных")
    return phone, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


MAX_MEDIA_BYTES = 700_000


def sniff_image_mime(data: bytes) -> str | None:
    """Тип по «магическим» байтам — присланному клиентом mime не верим. SVG не принимаем
    намеренно: он может содержать скрипты."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_master(business: dict, service: dict, master_id: int | None):
    """Мастер для записи на позицию по расписанию. None — если выбор мастера у бизнеса
    выключен или позиция без расписания (разовый заказ); иначе мастер обязателен, должен
    принадлежать этому бизнесу, быть активным и вести эту позицию."""
    if not business["use_masters"] or service["type"] != "slot":
        return None
    if master_id is None:
        raise HTTPException(status_code=400, detail="Выберите мастера")
    master = database.get_master(business["id"], master_id)
    if not master or not master["is_active"] or not database.master_does_service(master, service["id"]):
        raise HTTPException(status_code=404, detail="Мастер недоступен для этой услуги")
    return master


# Бизнес по умолчанию — тот, что привязан к BOT_TOKEN из .env. Используется, когда
# запрос пришёл без явного business_id (см. resolve_business). Резолвится один раз
# при старте, но какой бизнес обслуживать конкретный запрос — решает resolve_business,
# а не эта переменная напрямую.
DEFAULT_BUSINESS_ID: int | None = None


@app.on_event("startup")
def on_startup():
    global DEFAULT_BUSINESS_ID
    database.init_db()
    business = database.get_or_create_business_from_env(BOT_TOKEN, OWNER_TG_ID, BUSINESS_NAME)
    DEFAULT_BUSINESS_ID = business["id"]


async def reminder_loop():
    """Раз в минуту проверяет, кому пора отправить напоминание о записи. Ошибка одной
    итерации не должна убивать цикл — иначе напоминания молча прекратятся до рестарта."""
    tick = 0
    while True:
        try:
            await notifications.send_due_reminders()
            if tick % 60 == 0:  # раз в час: убираем загруженные, но не применённые картинки
                database.delete_orphan_media()
        except Exception:
            logging.exception("Сбой цикла напоминаний")
        tick += 1
        await asyncio.sleep(notifications.REMINDER_INTERVAL_SECONDS)


@app.on_event("startup")
async def on_startup_reminders():
    # Ссылку держим в app.state — иначе сборщик мусора может прибить фоновую задачу.
    app.state.reminder_task = asyncio.create_task(reminder_loop())


@app.on_event("startup")
async def on_startup_webhooks():
    """В облаке (USE_WEBHOOK=true) поднимаем вебхук для каждого бизнеса из БД —
    так не нужен отдельный always-on процесс на бизнес для polling, что важно для
    бесплатных хостингов вроде Render, где живёт только один web-сервис.

    Важно: регистрация вебхуков идёт в фоне, а не блокирует старт (см. asyncio.create_task
    ниже). Если бы мы делали await прямо в startup-хендлере, а Telegram API в этот момент
    подвис или ответил медленно — сервер не успел бы открыть порт вовремя, Render счёл бы
    деплой мёртвым (именно так уже падал один из деплоев: "Timed Out... no open ports
    detected"), хотя к самому коду это отношения не имело.

    Новые бизнесы, заведённые позже через /newbusiness (см. bot.py), регистрируют свой
    вебхук сразу сами — им не нужно ждать следующего рестарта сервера."""
    if not USE_WEBHOOK:
        return
    asyncio.create_task(webhooks.register_all_webhooks(database.get_all_businesses()))


@app.post("/webhook/{business_id}")
async def telegram_webhook(business_id: int, request: Request):
    bot_instance = webhooks.bots_by_business.get(business_id)
    if not bot_instance:
        raise HTTPException(status_code=404, detail="Неизвестный бизнес")
    expected_secret = webhooks.webhook_secret_for(bot_instance.token)
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected_secret:
        raise HTTPException(status_code=403, detail="Неверный секрет вебхука")
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot_instance})
    await tg_dp.feed_update(bot_instance, update, business_id=business_id)
    return {"ok": True}


# ---------- Публичное API (для клиентов) ----------

@app.get("/api/config")
def api_config(business_id: int | None = None):
    business = resolve_business(business_id)
    return {
        "business_id": business["id"],
        "business_name": business["name"],
        "owner_tg_id": business["owner_tg_id"],
        "use_masters": bool(business["use_masters"]),
        "theme": theme_with_urls(database.get_theme(business["id"])),
        "logo_url": media_url(business["logo_media_id"]),
        "collect_phone": business["collect_phone"],
        "privacy_url": business["privacy_url"],
    }


@app.get("/api/services")
def api_services(business_id: int | None = None):
    business = resolve_business(business_id)
    return with_image_urls(database.get_services(business["id"], active_only=True))


@app.get("/api/dates")
def api_dates(business_id: int | None = None):
    business = resolve_business(business_id)
    return database.get_available_dates(business["id"])


@app.get("/api/masters")
def api_masters(service_id: int, business_id: int | None = None):
    """Мастера, к которым можно записаться на позицию. Пусто — выбор мастера выключен."""
    business = resolve_business(business_id)
    if not business["use_masters"]:
        return []
    service = database.get_service(business["id"], service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return [
        {"id": m["id"], "name": m["name"]}
        for m in database.get_masters_for_service(business["id"], service_id)
    ]


@app.get("/api/slots")
def api_slots(service_id: int, date: str, business_id: int | None = None, master_id: int | None = None):
    business = resolve_business(business_id)
    service = database.get_service(business["id"], service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    master = resolve_master(business, service, master_id)
    return database.get_available_slots(business["id"], service_id, date, master["id"] if master else None)


@app.post("/api/book")
def api_book(booking: BookingRequest, background_tasks: BackgroundTasks):
    business = resolve_business(booking.business_id)
    service = database.get_service(business["id"], booking.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Позиция не найдена")

    master = resolve_master(business, service, booking.master_id)

    if service["type"] == "slot":
        if not booking.date or not booking.time:
            raise HTTPException(status_code=400, detail="Для этой позиции нужно выбрать дату и время")
        free_slots = database.get_available_slots(
            business["id"], booking.service_id, booking.date, master["id"] if master else None
        )
        if booking.time not in free_slots:
            raise HTTPException(status_code=409, detail="Это время уже занято, выберите другое")
        date, time = booking.date, booking.time
    else:
        # Позиция без расписания — дата/время не нужны, это разовый заказ
        date, time = None, None

    # id клиента берём только из подписанного initData: присланному в теле client_tg_id
    # верить нельзя — иначе можно заставить бота слать сообщения любому пользователю.
    client_tg_id = verify_init_data(booking.init_data, business["bot_token"])
    if client_tg_id is None and DEV_SKIP_INITDATA_CHECK:
        client_tg_id = booking.client_tg_id

    client_phone, consent_at = resolve_phone(business, booking)

    booking_id = database.create_booking(
        business["id"], booking.service_id, service["name"], service["price"], date, time,
        booking.client_name, client_tg_id,
        quantity=max(1, booking.quantity), comment=booking.comment,
        master_id=master["id"] if master else None, master_name=master["name"] if master else None,
        client_phone=client_phone, consent_at=consent_at,
    )
    background_tasks.add_task(notifications.notify_new_booking, business["id"], booking_id)
    return {
        "ok": True,
        "booking_id": booking_id,
        "service_name": service["name"],
        "master_name": master["name"] if master else None,
        "price": service["price"],
        "quantity": max(1, booking.quantity),
        "date": date,
        "time": time,
    }


@app.get("/api/media/{media_id}")
def api_media(media_id: int):
    """Картинки (логотип, фото позиций, фон) — публичные: их видят все клиенты бизнеса.
    id не переиспользуются (новая загрузка = новый id), поэтому кэшируем навсегда."""
    media = database.get_media(media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Картинка не найдена")
    return Response(
        content=media["data"], media_type=media["mime"],
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/my-bookings")
def api_my_bookings(business_id: int, init_data: str = "", client_tg_id: int | None = None):
    """Записи текущего клиента: сначала предстоящие (их можно отменить), затем история."""
    business = resolve_business(business_id)
    tg_id = resolve_client(business, init_data, client_tg_id)
    result = []
    for b in database.get_client_bookings(business["id"], tg_id):
        result.append({
            "id": b["id"],
            "service_name": b["service_name"],
            "master_name": b["master_name"],
            "date": b["date"],
            "time": b["time"],
            "quantity": b["quantity"],
            "price": b["price"],
            "status": b["status"],
            "can_cancel": database.can_cancel_booking(b, business["timezone"]),
        })
    upcoming = sorted((r for r in result if r["can_cancel"]), key=lambda r: (r["date"] or "9999", r["time"] or ""))
    history = [r for r in result if not r["can_cancel"]]
    return upcoming + history


@app.post("/api/my-bookings/{booking_id}/cancel")
async def api_cancel_my_booking(booking_id: int, payload: CancelRequest):
    business = resolve_business(payload.business_id)
    tg_id = resolve_client(business, payload.init_data, payload.client_tg_id)
    _, result = await notifications.cancel_by_client(business, booking_id, tg_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if result == "not_cancellable":
        raise HTTPException(status_code=409, detail="Эту запись уже нельзя отменить")
    return {"ok": True}


# ---------- Админ-API (только для владельца, проверяется owner_tg_id) ----------

@app.get("/api/admin/services")
def admin_list_services(business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    return with_image_urls(database.get_services(business["id"], active_only=False))


@app.post("/api/admin/services")
def admin_create_service(payload: ServiceRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    require_own_media(business["id"], payload.image_id)
    new_id = database.create_service(
        business["id"], payload.name, payload.price, payload.duration_min, payload.type,
        clean_text(payload.description), payload.image_id,
    )
    return {"ok": True, "id": new_id}


@app.put("/api/admin/services/{service_id}")
def admin_update_service(service_id: int, payload: ServiceRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    if not database.get_service(business["id"], service_id):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    require_own_media(business["id"], payload.image_id)
    database.update_service(
        business["id"], service_id, payload.name, payload.price, payload.duration_min, payload.type, payload.is_active,
        clean_text(payload.description), payload.image_id,
    )
    return {"ok": True}


@app.delete("/api/admin/services/{service_id}")
def admin_delete_service(service_id: int, business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    if not database.get_service(business["id"], service_id):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    database.delete_service(business["id"], service_id)
    return {"ok": True}


@app.get("/api/admin/masters")
def admin_list_masters(business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    return {
        "use_masters": bool(business["use_masters"]),
        "masters": database.get_masters(business["id"], active_only=False),
    }


def _disable_masters_if_none_left(business: dict):
    """Если у бизнеса включён выбор мастера, а активных мастеров не осталось (удалили или
    скрыли последнего), клиент упёрся бы в пустой экран — выключаем выбор автоматически."""
    if business["use_masters"] and not database.get_masters(business["id"]):
        database.set_use_masters(business["id"], False)


def _validated_service_ids(business_id: int, service_ids: list[int]) -> list[int]:
    """Оставляем только позиции по расписанию этого бизнеса. Если владелец что-то выбрал,
    а подходящих нет — это ошибка (иначе пустой список молча превратился бы в «все позиции»)."""
    valid = {s["id"] for s in database.get_services(business_id, active_only=False) if s["type"] == "slot"}
    result = [sid for sid in service_ids if sid in valid]
    if service_ids and not result:
        raise HTTPException(status_code=400, detail="Выбраны несуществующие позиции")
    return result


@app.post("/api/admin/masters")
def admin_create_master(payload: MasterRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите имя мастера")
    service_ids = _validated_service_ids(business["id"], payload.service_ids)
    new_id = database.create_master(business["id"], name, service_ids, payload.is_active)
    return {"ok": True, "id": new_id}


@app.put("/api/admin/masters/{master_id}")
def admin_update_master(master_id: int, payload: MasterRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    if not database.get_master(business["id"], master_id):
        raise HTTPException(status_code=404, detail="Мастер не найден")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите имя мастера")
    service_ids = _validated_service_ids(business["id"], payload.service_ids)
    database.update_master(business["id"], master_id, name, service_ids, payload.is_active)
    _disable_masters_if_none_left(business)
    return {"ok": True}


@app.delete("/api/admin/masters/{master_id}")
def admin_delete_master(master_id: int, business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    if not database.get_master(business["id"], master_id):
        raise HTTPException(status_code=404, detail="Мастер не найден")
    database.delete_master(business["id"], master_id)
    _disable_masters_if_none_left(business)
    return {"ok": True}


@app.put("/api/admin/masters-settings")
def admin_masters_settings(payload: MastersSettingsRequest):
    """Включает/выключает шаг «выбор мастера» у клиентов."""
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    if payload.use_masters and not database.get_masters(business["id"]):
        raise HTTPException(status_code=400, detail="Сначала добавьте хотя бы одного мастера")
    database.set_use_masters(business["id"], payload.use_masters)
    return {"ok": True, "use_masters": payload.use_masters}


@app.get("/api/admin/bookings")
def admin_list_bookings(business_id: int, init_data: str = "", owner_tg_id: int | None = None, status: str | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    return database.get_all_bookings(business["id"], status=status)


@app.patch("/api/admin/bookings/{booking_id}")
async def admin_update_booking(booking_id: int, payload: StatusRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    if payload.status not in ("new", "confirmed", "done", "cancelled"):
        raise HTTPException(status_code=400, detail="Неверный статус")
    booking, _ = await notifications.change_status(business, booking_id, payload.status)
    if booking is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return {"ok": True}


def _theme_dict(business: dict, payload: ThemeRequest) -> dict:
    """Поля темы из запроса, проверенные на принадлежность картинки этому бизнесу."""
    theme = payload.model_dump(include=set(database.THEME_FIELDS))
    require_own_media(business["id"], theme["bg_image_id"])
    if theme["bg_mode"] == "image" and theme["bg_image_id"] is None:
        raise HTTPException(status_code=400, detail="Для фона-картинки загрузите картинку")
    return theme


@app.put("/api/admin/theme")
def admin_update_theme(payload: ThemeRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    database.update_theme(business["id"], _theme_dict(business, payload))
    return {"ok": True, "theme": theme_with_urls(database.get_theme(business["id"]))}


@app.get("/api/admin/themes/saved")
def admin_saved_themes(business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    return [
        {**t, "theme": theme_with_urls(t["theme"])} for t in database.get_saved_themes(business["id"])
    ]


@app.post("/api/admin/themes/saved")
def admin_save_theme(payload: SavedThemeRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    if len(database.get_saved_themes(business["id"])) >= database.MAX_SAVED_THEMES:
        raise HTTPException(status_code=400, detail="Можно сохранить не больше 20 вариантов — удалите ненужные")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Введите название варианта")
    new_id = database.create_saved_theme(business["id"], name, _theme_dict(business, payload))
    return {"ok": True, "id": new_id}


@app.delete("/api/admin/themes/saved/{theme_id}")
def admin_delete_saved_theme(theme_id: int, business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    database.delete_saved_theme(business["id"], theme_id)
    return {"ok": True}


@app.post("/api/admin/media")
def admin_upload_media(payload: MediaUploadRequest):
    """Загрузка картинки владельцем. Браузер заранее уменьшает её и присылает как data URL;
    сервер всё равно проверяет тип по содержимому и размер."""
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    match = re.match(r"^data:image/[a-z+.-]+;base64,(.+)$", payload.data_url, re.DOTALL)
    if not match:
        raise HTTPException(status_code=400, detail="Неверный формат картинки")
    try:
        data = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Не удалось прочитать картинку")
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(status_code=413, detail="Картинка слишком большая")
    mime = sniff_image_mime(data)
    if not mime:
        raise HTTPException(status_code=400, detail="Поддерживаются только JPEG, PNG и WebP")
    if database.count_media(business["id"]) >= database.MAX_MEDIA_PER_BUSINESS:
        raise HTTPException(status_code=400, detail="Достигнут лимит загруженных картинок")
    media_id = database.create_media(business["id"], mime, data)
    return {"ok": True, "id": media_id, "url": media_url(media_id)}


@app.put("/api/admin/logo")
def admin_set_logo(payload: LogoRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    require_own_media(business["id"], payload.media_id)
    database.set_logo(business["id"], payload.media_id)
    return {"ok": True, "logo_url": media_url(payload.media_id)}


@app.get("/api/admin/settings")
def admin_get_settings(business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    return {"collect_phone": business["collect_phone"], "privacy_url": business["privacy_url"]}


@app.put("/api/admin/settings")
def admin_update_settings(payload: BusinessSettingsRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)
    privacy_url = clean_text(payload.privacy_url)
    if privacy_url and not re.match(r"^https?://\S+$", privacy_url):
        raise HTTPException(status_code=400, detail="Ссылка на политику должна начинаться с http:// или https://")
    database.update_business_settings(business["id"], payload.collect_phone, privacy_url)
    return {"ok": True}


@app.get("/api/admin/schedule")
def admin_get_schedule(business_id: int, init_data: str = "", owner_tg_id: int | None = None):
    business = resolve_business(business_id)
    check_owner(business, init_data, owner_tg_id)
    return database.get_schedule(business["id"])


@app.put("/api/admin/schedule")
def admin_update_schedule(payload: ScheduleRequest):
    business = resolve_business(payload.business_id)
    check_owner(business, payload.init_data, payload.owner_tg_id)

    if payload.work_end_hour <= payload.work_start_hour:
        raise HTTPException(status_code=400, detail="Время закрытия должно быть позже времени открытия")
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=400, detail="Неизвестный часовой пояс")

    database.update_schedule(business["id"], payload.model_dump(exclude={"owner_tg_id", "business_id", "init_data"}))
    return {"ok": True, "schedule": database.get_schedule(business["id"])}


# Отдаём саму Mini App (index.html, style.css, app.js) как статику.
# ВАЖНО: это должно быть смонтировано ПОСЛЕДНИМ, после всех /api роутов.
webapp_dir = os.path.join(os.path.dirname(__file__), "..", "webapp")
app.mount("/", StaticFiles(directory=webapp_dir, html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=SERVER_PORT, reload=True)
