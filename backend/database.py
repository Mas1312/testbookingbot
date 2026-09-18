import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH, WORK_START_HOUR, WORK_END_HOUR, SLOT_STEP_MINUTES, DAYS_AHEAD, DEFAULT_THEME


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицы, если их ещё нет.

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
            status TEXT DEFAULT 'new', -- new -> done / cancelled
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses (id),
            FOREIGN KEY (service_id) REFERENCES services (id)
        )
    """)

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
    """Заводит новый бизнес (свой Telegram-бот, свой владелец) с демо-позициями
    и темой по умолчанию. Пока вызывается вручную/скриптом — полноценный
    онбординг через диалог с ботом будет отдельным этапом."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO businesses
            (owner_tg_id, name, bot_token, bg_color, surface_color, text_color, hint_color,
             primary_color, primary_text_color, danger_color, success_color, radius)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            owner_tg_id, name, bot_token,
            DEFAULT_THEME["bg_color"], DEFAULT_THEME["surface_color"],
            DEFAULT_THEME["text_color"], DEFAULT_THEME["hint_color"],
            DEFAULT_THEME["primary_color"], DEFAULT_THEME["primary_text_color"],
            DEFAULT_THEME["danger_color"], DEFAULT_THEME["success_color"],
            DEFAULT_THEME["radius"],
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


# ---------- Слоты (только для позиций type='slot') ----------

def get_available_dates():
    """Список дат на DAYS_AHEAD вперёд начиная с сегодня, для выбора в Mini App."""
    today = datetime.now().date()
    return [(today + timedelta(days=i)).isoformat() for i in range(DAYS_AHEAD)]


def _slot_overlaps_busy(slot_start_minutes, slot_duration, busy_ranges):
    slot_end = slot_start_minutes + slot_duration
    for busy_start, busy_duration in busy_ranges:
        busy_end = busy_start + busy_duration
        if slot_start_minutes < busy_end and slot_end > busy_start:
            return True
    return False


def get_available_slots(business_id: int, service_id: int, date: str):
    """Возвращает список свободных времён (HH:MM) для позиции на дату,
    с учётом её длительности и уже существующих записей в этот день."""
    service = get_service(business_id, service_id)
    if not service or service["type"] != "slot":
        return []
    duration = service["duration_min"]

    conn = get_connection()
    busy_rows = conn.execute(
        """
        SELECT b.time, s.duration_min
        FROM bookings b
        JOIN services s ON s.id = b.service_id
        WHERE b.business_id = ? AND b.date = ? AND b.status != 'cancelled' AND s.type = 'slot'
        """,
        (business_id, date),
    ).fetchall()
    conn.close()

    busy_ranges = []
    for row in busy_rows:
        h, m = map(int, row["time"].split(":"))
        busy_ranges.append((h * 60 + m, row["duration_min"]))

    slots = []
    start_minutes = WORK_START_HOUR * 60
    end_minutes = WORK_END_HOUR * 60

    now = datetime.now()
    is_today = date == now.date().isoformat()
    now_minutes = now.hour * 60 + now.minute

    t = start_minutes
    while t + duration <= end_minutes:
        if not (is_today and t <= now_minutes):
            if not _slot_overlaps_busy(t, duration, busy_ranges):
                slots.append(f"{t // 60:02d}:{t % 60:02d}")
        t += SLOT_STEP_MINUTES

    return slots


# ---------- Заявки (bookings) ----------

def create_booking(business_id, service_id, date, time, client_name, client_tg_id, quantity=1, comment=None):
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO bookings (business_id, service_id, client_name, client_tg_id, date, time, quantity, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (business_id, service_id, client_name, client_tg_id, date, time, quantity, comment),
    )
    conn.commit()
    booking_id = cur.lastrowid
    conn.close()
    return booking_id


def get_all_bookings(business_id: int, status: str = None):
    conn = get_connection()
    query = """
        SELECT b.id, b.date, b.time, b.client_name, b.client_tg_id, b.quantity,
               b.comment, b.status, b.created_at,
               s.name as service_name, s.price, s.type as service_type
        FROM bookings b JOIN services s ON s.id = b.service_id
        WHERE b.business_id = ?
    """
    params = [business_id]
    if status:
        query += " AND b.status = ?"
        params.append(status)
    query += " ORDER BY b.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_booking_status(business_id: int, booking_id: int, status: str):
    conn = get_connection()
    conn.execute(
        "UPDATE bookings SET status = ? WHERE id = ? AND business_id = ?", (status, booking_id, business_id)
    )
    conn.commit()
    conn.close()
