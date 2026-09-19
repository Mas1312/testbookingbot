import sqlite3
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from config import DB_PATH, DEFAULT_THEME, DEFAULT_SCHEDULE


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(conn, table: str, columns: dict):
    """Добавляет недостающие колонки в уже существующую таблицу (ALTER TABLE ADD COLUMN).
    Нужно, чтобы у БД, развёрнутой ещё до появления расписания/заморозки цены в заявках,
    не отвалился старт — CREATE TABLE IF NOT EXISTS новые колонки в старую таблицу не добавит."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def init_db():
    """Создаёт таблицы, если их ещё нет, и докатывает недостающие колонки на старых базах.

    Схема мультитенантная: одна база обслуживает несколько бизнесов (у каждого свой
    Telegram-бот и свой владелец), всё завязано на businesses.id.

    Позиция (services) бывает двух типов:
      - type='slot'  — требует выбора даты и времени (стрижка, аренда сапборда, консультация)
      - type='order' — разовый заказ без привязки ко времени (шаурма, товар, любая мгновенная услуга)
    Для type='order' поле duration_min не используется (хранится 0).
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            bot_token TEXT NOT NULL UNIQUE,
            bot_username TEXT,
            bg_color TEXT NOT NULL,
            surface_color TEXT NOT NULL,
            text_color TEXT NOT NULL,
            hint_color TEXT NOT NULL,
            primary_color TEXT NOT NULL,
            primary_text_color TEXT NOT NULL,
            danger_color TEXT NOT NULL,
            success_color TEXT NOT NULL,
            radius INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _ensure_columns(conn, "businesses", {
        "timezone": "TEXT NOT NULL DEFAULT 'Europe/Moscow'",
        "work_start_hour": "INTEGER NOT NULL DEFAULT 9",
        "work_end_hour": "INTEGER NOT NULL DEFAULT 18",
        "slot_step_minutes": "INTEGER NOT NULL DEFAULT 30",
        "days_ahead": "INTEGER NOT NULL DEFAULT 7",
        # Включает шаг «выбор мастера» для клиентов (см. таблицу masters).
        "use_masters": "INTEGER NOT NULL DEFAULT 0",
    })

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            duration_min INTEGER NOT NULL DEFAULT 0,
            type TEXT NOT NULL DEFAULT 'slot',   -- 'slot' или 'order'
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (business_id) REFERENCES businesses (id)
        )
    """)

    # Мастера (сотрудники/исполнители). У каждого свой календарь: занятость слотов
    # считается по master_id заявки. master_services — какие позиции мастер ведёт;
    # НЕТ строк = ведёт все позиции по расписанию (так новая позиция сразу доступна всем).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS masters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (business_id) REFERENCES businesses (id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS master_services (
            master_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            PRIMARY KEY (master_id, service_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            client_name TEXT,
            client_tg_id INTEGER,
            date TEXT,                 -- YYYY-MM-DD, NULL для заказов без слота
            time TEXT,                 -- HH:MM, NULL для заказов без слота
            quantity INTEGER NOT NULL DEFAULT 1,
            comment TEXT,
            status TEXT DEFAULT 'new', -- new -> confirmed -> done; из new/confirmed -> cancelled
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses (id),
            FOREIGN KEY (service_id) REFERENCES services (id)
        )
    """)
    _ensure_columns(conn, "bookings", {
        # Снимок названия/цены позиции на момент брони — чтобы более поздние правки
        # цены или удаление позиции не переписывали историю задним числом.
        "service_name": "TEXT",
        "price": "INTEGER",
        # Мастер, к которому записали (NULL — записи без мастеров / сделанные до их появления).
        # Имя — снимок на момент брони, как и у услуги.
        "master_id": "INTEGER",
        "master_name": "TEXT",
        # Отправлены ли напоминания клиенту (за сутки / за 2 часа) — чтобы не слать дважды.
        "reminded_24h": "INTEGER NOT NULL DEFAULT 0",
        "reminded_2h": "INTEGER NOT NULL DEFAULT 0",
    })

    conn.commit()
    conn.close()


# ---------- Бизнесы ----------

def _seed_demo_services(conn, business_id: int):
    demo_services = [
        ("Стрижка мужская", 1200, 30, "slot", 1),
        ("Аренда сапборда (1 час)", 1500, 60, "slot", 2),
        ("Консультация специалиста", 2000, 45, "slot", 3),
        ("Шаурма классическая", 350, 0, "order", 4),
        ("Доставка на дом", 500, 0, "order", 5),
    ]
    conn.executemany(
        "INSERT INTO services (business_id, name, price, duration_min, type, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
        [(business_id, *s) for s in demo_services],
    )


def create_business(owner_tg_id: int, name: str, bot_token: str):
    """Заводит новый бизнес (свой Telegram-бот, свой владелец) с демо-позициями,
    темой и расписанием по умолчанию. Пока вызывается вручную/скриптом или через
    /newbusiness — полноценная форма настройки расписания будет в админке после."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO businesses
            (owner_tg_id, name, bot_token, bg_color, surface_color, text_color, hint_color,
             primary_color, primary_text_color, danger_color, success_color, radius,
             timezone, work_start_hour, work_end_hour, slot_step_minutes, days_ahead)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            owner_tg_id, name, bot_token,
            DEFAULT_THEME["bg_color"], DEFAULT_THEME["surface_color"],
            DEFAULT_THEME["text_color"], DEFAULT_THEME["hint_color"],
            DEFAULT_THEME["primary_color"], DEFAULT_THEME["primary_text_color"],
            DEFAULT_THEME["danger_color"], DEFAULT_THEME["success_color"],
            DEFAULT_THEME["radius"],
            DEFAULT_SCHEDULE["timezone"], DEFAULT_SCHEDULE["work_start_hour"],
            DEFAULT_SCHEDULE["work_end_hour"], DEFAULT_SCHEDULE["slot_step_minutes"],
            DEFAULT_SCHEDULE["days_ahead"],
        ),
    )
    business_id = cur.lastrowid
    _seed_demo_services(conn, business_id)
    conn.commit()
    row = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    conn.close()
    return dict(row)


def get_or_create_business_from_env(bot_token: str, owner_tg_id: int, business_name: str):
    """Гарантирует, что для текущего BOT_TOKEN из .env есть запись в businesses —
    временный мост, пока онбординг новых бизнесов не сделан отдельным шагом (этап 4).
    Если бизнеса с таким токеном ещё нет — создаёт его с демонстрационными позициями."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM businesses WHERE bot_token = ?", (bot_token,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return create_business(owner_tg_id, business_name, bot_token)


def get_all_businesses():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM businesses ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_business(business_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_business_by_bot_token(bot_token: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM businesses WHERE bot_token = ?", (bot_token,)).fetchone()
    conn.close()
    return dict(row) if row else None


THEME_FIELDS = (
    "bg_color", "surface_color", "text_color", "hint_color",
    "primary_color", "primary_text_color", "danger_color", "success_color", "radius",
)


def get_theme(business_id: int):
    business = get_business(business_id)
    return {k: business[k] for k in THEME_FIELDS} if business else dict(DEFAULT_THEME)


def update_theme(business_id: int, theme: dict):
    conn = get_connection()
    conn.execute(
        """
        UPDATE businesses
        SET bg_color = ?, surface_color = ?, text_color = ?, hint_color = ?,
            primary_color = ?, primary_text_color = ?, danger_color = ?,
            success_color = ?, radius = ?
        WHERE id = ?
        """,
        (
            theme["bg_color"], theme["surface_color"], theme["text_color"], theme["hint_color"],
            theme["primary_color"], theme["primary_text_color"], theme["danger_color"],
            theme["success_color"], theme["radius"], business_id,
        ),
    )
    conn.commit()
    conn.close()


SCHEDULE_FIELDS = ("timezone", "work_start_hour", "work_end_hour", "slot_step_minutes", "days_ahead")


def get_schedule(business_id: int):
    business = get_business(business_id)
    return {k: business[k] for k in SCHEDULE_FIELDS} if business else dict(DEFAULT_SCHEDULE)


def update_schedule(business_id: int, schedule: dict):
    conn = get_connection()
    conn.execute(
        """
        UPDATE businesses
        SET timezone = ?, work_start_hour = ?, work_end_hour = ?, slot_step_minutes = ?, days_ahead = ?
        WHERE id = ?
        """,
        (
            schedule["timezone"], schedule["work_start_hour"], schedule["work_end_hour"],
            schedule["slot_step_minutes"], schedule["days_ahead"], business_id,
        ),
    )
    conn.commit()
    conn.close()


# ---------- Позиции (услуги/товары) ----------

def get_services(business_id: int, active_only: bool = True):
    conn = get_connection()
    query = "SELECT * FROM services WHERE business_id = ?"
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY sort_order, id"
    rows = conn.execute(query, (business_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_service(business_id: int, service_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM services WHERE id = ? AND business_id = ?", (service_id, business_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_service(business_id: int, name: str, price: int, duration_min: int, type_: str):
    conn = get_connection()
    cur = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM services WHERE business_id = ?", (business_id,)
    )
    next_order = cur.fetchone()[0]
    cur = conn.execute(
        "INSERT INTO services (business_id, name, price, duration_min, type, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
        (business_id, name, price, duration_min if type_ == "slot" else 0, type_, next_order),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_service(business_id: int, service_id: int, name: str, price: int, duration_min: int, type_: str, is_active: bool):
    conn = get_connection()
    conn.execute(
        """
        UPDATE services
        SET name = ?, price = ?, duration_min = ?, type = ?, is_active = ?
        WHERE id = ? AND business_id = ?
        """,
        (name, price, duration_min if type_ == "slot" else 0, type_, 1 if is_active else 0, service_id, business_id),
    )
    conn.commit()
    conn.close()


def delete_service(business_id: int, service_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM services WHERE id = ? AND business_id = ?", (service_id, business_id))
    conn.commit()
    conn.close()


# ---------- Мастера ----------

def _attach_service_ids(conn, masters: list[dict]) -> list[dict]:
    for m in masters:
        rows = conn.execute("SELECT service_id FROM master_services WHERE master_id = ?", (m["id"],)).fetchall()
        m["service_ids"] = sorted(r["service_id"] for r in rows)  # пусто = все позиции
    return masters


def get_masters(business_id: int, active_only: bool = True):
    conn = get_connection()
    query = "SELECT * FROM masters WHERE business_id = ?"
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY sort_order, id"
    masters = _attach_service_ids(conn, [dict(r) for r in conn.execute(query, (business_id,)).fetchall()])
    conn.close()
    return masters


def get_master(business_id: int, master_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM masters WHERE id = ? AND business_id = ?", (master_id, business_id)).fetchone()
    result = _attach_service_ids(conn, [dict(row)])[0] if row else None
    conn.close()
    return result


def master_does_service(master: dict, service_id: int) -> bool:
    return not master["service_ids"] or service_id in master["service_ids"]


def get_masters_for_service(business_id: int, service_id: int):
    """Активные мастера, которые ведут эту позицию — для шага «выбор мастера» у клиента."""
    return [m for m in get_masters(business_id) if master_does_service(m, service_id)]


def _save_master_services(conn, business_id: int, master_id: int, service_ids: list[int]):
    """Пишет ограничение по позициям. Если выбраны ВСЕ позиции по расписанию — строки не
    храним (пусто = все), чтобы будущие позиции доставались мастеру автоматически."""
    slot_ids = {r["id"] for r in conn.execute(
        "SELECT id FROM services WHERE business_id = ? AND type = 'slot'", (business_id,)
    ).fetchall()}
    chosen = set(service_ids) & slot_ids
    conn.execute("DELETE FROM master_services WHERE master_id = ?", (master_id,))
    if chosen and chosen != slot_ids:
        conn.executemany(
            "INSERT INTO master_services (master_id, service_id) VALUES (?, ?)",
            [(master_id, sid) for sid in chosen],
        )


def create_master(business_id: int, name: str, service_ids: list[int], is_active: bool = True):
    conn = get_connection()
    next_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM masters WHERE business_id = ?", (business_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO masters (business_id, name, is_active, sort_order) VALUES (?, ?, ?, ?)",
        (business_id, name, 1 if is_active else 0, next_order),
    )
    master_id = cur.lastrowid
    _save_master_services(conn, business_id, master_id, service_ids)
    conn.commit()
    conn.close()
    return master_id


def update_master(business_id: int, master_id: int, name: str, service_ids: list[int], is_active: bool):
    conn = get_connection()
    conn.execute(
        "UPDATE masters SET name = ?, is_active = ? WHERE id = ? AND business_id = ?",
        (name, 1 if is_active else 0, master_id, business_id),
    )
    _save_master_services(conn, business_id, master_id, service_ids)
    conn.commit()
    conn.close()


def delete_master(business_id: int, master_id: int):
    """Прошлые и будущие заявки остаются (имя мастера хранится в самой заявке)."""
    conn = get_connection()
    conn.execute("DELETE FROM master_services WHERE master_id = ?", (master_id,))
    conn.execute("DELETE FROM masters WHERE id = ? AND business_id = ?", (master_id, business_id))
    conn.commit()
    conn.close()


def set_use_masters(business_id: int, enabled: bool):
    conn = get_connection()
    conn.execute("UPDATE businesses SET use_masters = ? WHERE id = ?", (1 if enabled else 0, business_id))
    conn.commit()
    conn.close()


# ---------- Слоты (только для позиций type='slot') ----------

def _now_in_business_tz(timezone: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone))
    except Exception:
        # Битая/неизвестная таймзона в БД — не роняем расчёт слотов, просто откатываемся к UTC.
        return datetime.now(ZoneInfo("UTC"))


def get_available_dates(business_id: int):
    """Список дат на days_ahead бизнеса вперёд, начиная с сегодняшнего дня В ЕГО
    часовом поясе (а не часовом поясе сервера — на Render это UTC, что для
    Europe/Moscow, например, может давать неверную дату ближе к полуночи)."""
    schedule = get_schedule(business_id)
    today = _now_in_business_tz(schedule["timezone"]).date()
    return [(today + timedelta(days=i)).isoformat() for i in range(schedule["days_ahead"])]


def _slot_overlaps_busy(slot_start_minutes, slot_duration, busy_ranges):
    slot_end = slot_start_minutes + slot_duration
    for busy_start, busy_duration in busy_ranges:
        busy_end = busy_start + busy_duration
        if slot_start_minutes < busy_end and slot_end > busy_start:
            return True
    return False


def get_available_slots(business_id: int, service_id: int, date: str, master_id: int | None = None):
    """Возвращает список свободных времён (HH:MM) для позиции на дату, с учётом её
    длительности, уже существующих записей и расписания бизнеса (рабочие часы, шаг
    сетки — свои у каждого бизнеса) — время «сейчас» берётся в часовом поясе бизнеса.

    master_id — календарь конкретного мастера: заняты только его записи (и старые записи
    без мастера — они не привязаны ни к кому, поэтому блокируют всех). Без master_id
    (бизнес без мастеров) календарь общий."""
    service = get_service(business_id, service_id)
    if not service or service["type"] != "slot":
        return []
    duration = service["duration_min"]
    schedule = get_schedule(business_id)

    conn = get_connection()
    busy_query = """
        SELECT b.time, s.duration_min
        FROM bookings b
        JOIN services s ON s.id = b.service_id
        WHERE b.business_id = ? AND b.date = ? AND b.status != 'cancelled' AND s.type = 'slot'
    """
    busy_params = [business_id, date]
    if master_id is not None:
        busy_query += " AND (b.master_id = ? OR b.master_id IS NULL)"
        busy_params.append(master_id)
    busy_rows = conn.execute(busy_query, busy_params).fetchall()
    conn.close()

    busy_ranges = []
    for row in busy_rows:
        h, m = map(int, row["time"].split(":"))
        busy_ranges.append((h * 60 + m, row["duration_min"]))

    slots = []
    start_minutes = schedule["work_start_hour"] * 60
    end_minutes = schedule["work_end_hour"] * 60
    slot_step = schedule["slot_step_minutes"]

    now = _now_in_business_tz(schedule["timezone"])
    is_today = date == now.date().isoformat()
    now_minutes = now.hour * 60 + now.minute

    t = start_minutes
    while t + duration <= end_minutes:
        if not (is_today and t <= now_minutes):
            if not _slot_overlaps_busy(t, duration, busy_ranges):
                slots.append(f"{t // 60:02d}:{t % 60:02d}")
        t += slot_step

    return slots


# ---------- Заявки (bookings) ----------

def create_booking(business_id, service_id, service_name, price, date, time, client_name, client_tg_id, quantity=1, comment=None,
                   master_id=None, master_name=None):
    """service_name/price — снимок с позиции НА МОМЕНТ брони: последующее изменение
    цены или удаление позиции больше не переписывает историю заявок задним числом."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO bookings (business_id, service_id, service_name, price, client_name, client_tg_id, date, time, quantity, comment,
                              master_id, master_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (business_id, service_id, service_name, price, client_name, client_tg_id, date, time, quantity, comment,
         master_id, master_name),
    )
    conn.commit()
    booking_id = cur.lastrowid
    conn.close()
    return booking_id


# LEFT JOIN, а не JOIN: если позицию потом удалили, её прошлые заявки не должны
# молча пропадать из истории (раньше INNER JOIN именно так и делал). Название/цену
# берём из самой заявки (заморожены на момент брони, см. create_booking), а на
# join к services переключаемся только для старых записей, сделанных до этой правки.
_BOOKING_SELECT = """
    SELECT b.id, b.date, b.time, b.client_name, b.client_tg_id, b.quantity,
           b.comment, b.status, b.created_at, b.master_id, b.master_name,
           COALESCE(b.service_name, s.name, 'Позиция удалена') as service_name,
           COALESCE(b.price, s.price, 0) as price,
           s.type as service_type
    FROM bookings b LEFT JOIN services s ON s.id = b.service_id
    WHERE b.business_id = ?
"""


def get_all_bookings(business_id: int, status: str = None):
    conn = get_connection()
    query = _BOOKING_SELECT
    params = [business_id]
    if status:
        query += " AND b.status = ?"
        params.append(status)
    query += " ORDER BY b.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_booking(business_id: int, booking_id: int):
    conn = get_connection()
    row = conn.execute(_BOOKING_SELECT + " AND b.id = ?", (business_id, booking_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_client_bookings(business_id: int, client_tg_id: int, limit: int = 50):
    """Заявки конкретного клиента в этом бизнесе, новые сверху (для «Мои записи»)."""
    conn = get_connection()
    rows = conn.execute(
        _BOOKING_SELECT + " AND b.client_tg_id = ? ORDER BY b.created_at DESC, b.id DESC LIMIT ?",
        (business_id, client_tg_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def booking_start(date: str | None, time: str | None, timezone: str) -> datetime | None:
    """Момент начала записи (с часовым поясом бизнеса) или None для заказов без слота."""
    if not date or not time:
        return None
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    try:
        return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError:
        return None


def can_cancel_booking(booking: dict, timezone: str) -> bool:
    """Отменить можно, пока запись активна (новая/подтверждённая) и время ещё не наступило.
    Заказы без слота — пока не обработаны владельцем."""
    if booking["status"] not in ("new", "confirmed"):
        return False
    start = booking_start(booking["date"], booking["time"], timezone)
    return start is None or start > datetime.now(dt_timezone.utc)


def get_reminder_candidates():
    """Активные записи со слотом и известным клиентом, по которым ещё не отправлены
    все напоминания. Грубый предфильтр по дате — точное время считает вызывающий код."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT b.id, b.business_id, b.date, b.time, b.created_at, b.reminded_24h, b.reminded_2h,
               bz.timezone
        FROM bookings b JOIN businesses bz ON bz.id = b.business_id
        WHERE b.date IS NOT NULL AND b.time IS NOT NULL AND b.client_tg_id IS NOT NULL
          AND b.status IN ('new', 'confirmed')
          AND (b.reminded_24h = 0 OR b.reminded_2h = 0)
          AND b.date >= date('now', '-1 day')
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_reminded(booking_id: int, kinds: list[str]):
    columns = {"24h": "reminded_24h", "2h": "reminded_2h"}
    conn = get_connection()
    for kind in kinds:
        conn.execute(f"UPDATE bookings SET {columns[kind]} = 1 WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()


def update_booking_status(business_id: int, booking_id: int, status: str):
    conn = get_connection()
    conn.execute(
        "UPDATE bookings SET status = ? WHERE id = ? AND business_id = ?", (status, booking_id, business_id)
    )
    conn.commit()
    conn.close()
