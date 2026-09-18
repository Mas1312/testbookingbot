import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram.types import Update

import database
from config import SERVER_PORT, BUSINESS_NAME, OWNER_TG_ID, USE_WEBHOOK, WEBHOOK_SECRET, WEBAPP_URL, BOT_TOKEN
from bot import bot as tg_bot, dp as tg_dp

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
    service_id: int
    date: str | None = None
    time: str | None = None
    quantity: int = 1
    comment: str | None = None
    client_name: str = "Клиент"
    client_tg_id: int | None = None


class ServiceRequest(BaseModel):
    name: str
    price: int
    duration_min: int = 0
    type: str = "slot"        # 'slot' или 'order'
    is_active: bool = True
    owner_tg_id: int          # для проверки, что запрос от владельца


class StatusRequest(BaseModel):
    status: str               # 'new' | 'done' | 'cancelled'
    owner_tg_id: int


class ThemeRequest(BaseModel):
    bg_color: str
    surface_color: str
    text_color: str
    hint_color: str
    primary_color: str
    primary_text_color: str
    danger_color: str
    success_color: str
    radius: int
    owner_tg_id: int


def check_owner(owner_tg_id: int):
    """Простая проверка для прототипа: сверяем присланный tg_id с тем, что в .env.
    Для продакшена стоит валидировать initData от Telegram по HMAC, а не просто верить id."""
    if OWNER_TG_ID == 0 or owner_tg_id != OWNER_TG_ID:
        raise HTTPException(status_code=403, detail="Доступно только владельцу")


# Схема БД уже мультитенантная (таблица businesses + business_id у services/bookings),
# но онбординг новых бизнесов ещё не сделан (это следующий этап) — поэтому пока сервер
# всегда обслуживает один бизнес, привязанный к BOT_TOKEN из .env, и резолвит его id
# один раз при старте. Когда появится онбординг, это заменится на резолв per-request.
CURRENT_BUSINESS_ID: int | None = None


@app.on_event("startup")
def on_startup():
    global CURRENT_BUSINESS_ID
    database.init_db()
    business = database.get_or_create_business_from_env(BOT_TOKEN, OWNER_TG_ID, BUSINESS_NAME)
    CURRENT_BUSINESS_ID = business["id"]


@app.on_event("startup")
async def on_startup_webhook():
    """В облаке (USE_WEBHOOK=true) регистрируем вебхук у Telegram при каждом старте
    процесса — так не нужен отдельный always-on процесс для polling (bot.py),
    что важно для бесплатных хостингов вроде Render, где живёт только web-сервис."""
    if USE_WEBHOOK:
        await tg_bot.set_webhook(
            url=f"{WEBAPP_URL}/webhook",
            secret_token=WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Неверный секрет вебхука")
    data = await request.json()
    update = Update.model_validate(data, context={"bot": tg_bot})
    await tg_dp.feed_update(tg_bot, update)
    return {"ok": True}


# ---------- Публичное API (для клиентов) ----------

@app.get("/api/config")
def api_config():
    return {
        "business_name": BUSINESS_NAME,
        "owner_tg_id": OWNER_TG_ID,
        "theme": database.get_theme(CURRENT_BUSINESS_ID),
    }


@app.get("/api/services")
def api_services():
    return database.get_services(CURRENT_BUSINESS_ID, active_only=True)


@app.get("/api/dates")
def api_dates():
    return database.get_available_dates()


@app.get("/api/slots")
def api_slots(service_id: int, date: str):
    service = database.get_service(CURRENT_BUSINESS_ID, service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return database.get_available_slots(CURRENT_BUSINESS_ID, service_id, date)


@app.post("/api/book")
def api_book(booking: BookingRequest):
    service = database.get_service(CURRENT_BUSINESS_ID, booking.service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Позиция не найдена")

    if service["type"] == "slot":
        if not booking.date or not booking.time:
            raise HTTPException(status_code=400, detail="Для этой позиции нужно выбрать дату и время")
        free_slots = database.get_available_slots(CURRENT_BUSINESS_ID, booking.service_id, booking.date)
        if booking.time not in free_slots:
            raise HTTPException(status_code=409, detail="Это время уже занято, выберите другое")
        date, time = booking.date, booking.time
    else:
        # Позиция без расписания — дата/время не нужны, это разовый заказ
        date, time = None, None

    booking_id = database.create_booking(
        CURRENT_BUSINESS_ID, booking.service_id, date, time, booking.client_name, booking.client_tg_id,
        quantity=max(1, booking.quantity), comment=booking.comment,
    )
    return {
        "ok": True,
        "booking_id": booking_id,
        "service_name": service["name"],
        "price": service["price"],
        "quantity": max(1, booking.quantity),
        "date": date,
        "time": time,
    }


# ---------- Админ-API (только для владельца, проверяется owner_tg_id) ----------

@app.get("/api/admin/services")
def admin_list_services(owner_tg_id: int):
    check_owner(owner_tg_id)
    return database.get_services(CURRENT_BUSINESS_ID, active_only=False)


@app.post("/api/admin/services")
def admin_create_service(payload: ServiceRequest):
    check_owner(payload.owner_tg_id)
    new_id = database.create_service(CURRENT_BUSINESS_ID, payload.name, payload.price, payload.duration_min, payload.type)
    return {"ok": True, "id": new_id}


@app.put("/api/admin/services/{service_id}")
def admin_update_service(service_id: int, payload: ServiceRequest):
    check_owner(payload.owner_tg_id)
    if not database.get_service(CURRENT_BUSINESS_ID, service_id):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    database.update_service(
        CURRENT_BUSINESS_ID, service_id, payload.name, payload.price, payload.duration_min, payload.type, payload.is_active
    )
    return {"ok": True}


@app.delete("/api/admin/services/{service_id}")
def admin_delete_service(service_id: int, owner_tg_id: int):
    check_owner(owner_tg_id)
    if not database.get_service(CURRENT_BUSINESS_ID, service_id):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    database.delete_service(CURRENT_BUSINESS_ID, service_id)
    return {"ok": True}


@app.get("/api/admin/bookings")
def admin_list_bookings(owner_tg_id: int, status: str | None = None):
    check_owner(owner_tg_id)
    return database.get_all_bookings(CURRENT_BUSINESS_ID, status=status)


@app.patch("/api/admin/bookings/{booking_id}")
def admin_update_booking(booking_id: int, payload: StatusRequest):
    check_owner(payload.owner_tg_id)
    if payload.status not in ("new", "done", "cancelled"):
        raise HTTPException(status_code=400, detail="Неверный статус")
    database.update_booking_status(CURRENT_BUSINESS_ID, booking_id, payload.status)
    return {"ok": True}


@app.put("/api/admin/theme")
def admin_update_theme(payload: ThemeRequest):
    check_owner(payload.owner_tg_id)
    database.update_theme(CURRENT_BUSINESS_ID, payload.model_dump(exclude={"owner_tg_id"}))
    return {"ok": True, "theme": database.get_theme(CURRENT_BUSINESS_ID)}


# Отдаём саму Mini App (index.html, style.css, app.js) как статику.
# ВАЖНО: это должно быть смонтировано ПОСЛЕДНИМ, после всех /api роутов.
webapp_dir = os.path.join(os.path.dirname(__file__), "..", "webapp")
app.mount("/", StaticFiles(directory=webapp_dir, html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=SERVER_PORT, reload=True)
