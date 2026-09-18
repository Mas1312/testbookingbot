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

# --- Настройки рабочего времени бизнеса (пока захардкожены для прототипа) ---
WORK_START_HOUR = 9      # во сколько открываемся
WORK_END_HOUR = 18       # во сколько закрываемся
SLOT_STEP_MINUTES = 30   # шаг сетки слотов (можно ставить запись каждые 30 минут)
DAYS_AHEAD = 7           # на сколько дней вперёд можно записаться

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "booking.db")

# Оформление Mini App по умолчанию (пока одно на бизнес, хранится в БД в таблице
# theme_settings — владелец может поменять его в разделе «Оформление» админки).
# Когда прототип превратится в SaaS, эта же схема просто переедет в строку на business_id.
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
