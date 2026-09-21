import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота, который выдал @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Публичный HTTPS-адрес, по которому Telegram сможет открыть твою Mini App
# Локально это будет адрес от ngrok/cloudflared, например https://abcd1234.ngrok-free.app
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")

# Порт, на котором поднимается локальный сервер (FastAPI)
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# Название бизнеса — показывается в шапке Mini App
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Моя запись")

# Telegram ID владельца бизнеса — именно этому пользователю в Mini App показывается
# админ-панель (управление позициями и заявками). Узнать свой ID можно у бота @userinfobot.
# 0 означает "не задан" — тогда админку не увидит никто.
OWNER_TG_ID = int(os.getenv("OWNER_TG_ID", "0"))

# Режим работы бота:
#   false (по умолчанию, локальная разработка) — бот сам опрашивает Telegram (polling),
#     запускается отдельным процессом через backend/bot.py.
#   true (продакшен/облако, например Render) — Telegram сам присылает обновления на
#     POST {WEBAPP_URL}/webhook/{business_id} (свой путь на каждый бизнес), всё работает
#     внутри одного процесса server.py, отдельный bot.py запускать не нужно и нельзя
#     (конфликт с вебхуком). Секрет для проверки входящих запросов сервер считает сам
#     из токена каждого бота — хранить/генерировать его отдельно не нужно.
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"

# Доступ в админку проверяется криптографически — по подписи Telegram WebApp initData
# (см. backend/telegram_auth.py), а не по присланному клиентом owner_tg_id, который
# подделать ничего не стоит. Но initData бывает только внутри настоящего Telegram —
# при обычном открытии сервера в браузере (локальная отладка вёрстки без ngrok/Telegram)
# его взять неоткуда. Этот флаг включает старую, небезопасную проверку по owner_tg_id
# как запасной вариант ТОЛЬКО для такой локальной отладки.
# НИКОГДА не включать в проде (.env на Render) — иначе админку сможет открыть кто угодно,
# зная только твой Telegram ID (он не секретный).
DEV_SKIP_INITDATA_CHECK = os.getenv("DEV_SKIP_INITDATA_CHECK", "false").lower() == "true"

# Бизнес, чей бот служит «входом для владельцев»: только в нём работает /newbusiness (подключить
# СВОЙ бизнес). В ботах клиентов команда не отвечает и нигде не рекламируется. По умолчанию — бизнес №1
# (прод-бот). Когда заведём отдельный платформенный бот, сюда пойдёт его business_id.
PLATFORM_BUSINESS_ID = int(os.getenv("PLATFORM_BUSINESS_ID", "1"))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "booking.db")

# Расписание по умолчанию для НОВОГО бизнеса (при создании через create_business) —
# дальше у каждого бизнеса своё, хранится в БД в его строке в businesses и меняется
# в разделе «Расписание» админки. Часовой пояс — своё, самое важное: раньше слоты
# считались по времени СЕРВЕРА (на Render это UTC), что для бизнеса в Москве сдвигало
# доступные часы на 3 часа.
# Демо-услуги (стрижка, шаурма...) в новом бизнесе. По умолчанию ВЫКЛ: новый владелец начинает
# с пустого списка и мастера первого запуска. true — только для локальной разработки/тестов.
SEED_DEMO_SERVICES = os.getenv("SEED_DEMO_SERVICES", "false").lower() == "true"

DEFAULT_SCHEDULE = {
    "timezone": "Europe/Moscow",
    "work_start_hour": 9,
    "work_end_hour": 18,
    "slot_step_minutes": 30,
    "days_ahead": 7,
}

# Оформление Mini App по умолчанию для нового бизнеса — та же история, что и с
# расписанием: дальше своё у каждого, хранится в его строке в businesses.
DEFAULT_THEME = {
    "bg_color": "#FAFAFA",
    "surface_color": "#FFFFFF",
    "text_color": "#1A1A1A",
    "hint_color": "#767676",
    "primary_color": "#E1251B",
    "primary_text_color": "#FFFFFF",
    "danger_color": "#D92D20",
    "success_color": "#12805C",
    "radius": 16,
}
