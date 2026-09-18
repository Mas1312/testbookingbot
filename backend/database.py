import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH, WORK_START_HOUR, WORK_END_HOUR, SLOT_STEP_MINUTES, DAYS_AHEAD, DEFAULT_THEME


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицы, если их ещё нет, и наполняет примером позиций при первом запуске.

    Позиция (services) бывает двух типов:
      - type='slot'  — требует выбора даты и времени (стрижка, аренда сапборда, консультация)
      - type='order' — разовый заказ без привязки ко времени (шаурма, товар, любая мгновенная услуга)
    Для type='order' поле duration_min не используется (хранится 0).
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            duration_min INTEGER NOT NULL DEFAULT 0,
            type TEXT NOT NULL DEFAULT 'slot',   -- 'slot' или 'order'
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            bg_color TEXT NOT NULL,
            surface_color TEXT NOT NULL,
            text_color TEXT NOT NULL,
            hint_color TEXT NOT NULL,
            primary_color TEXT NOT NULL,
            primary_text_color TEXT NOT NULL,
            danger_color TEXT NOT NULL,
            success_color TEXT NOT NULL,
            radius INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL,
            client_name TEXT,
            client_tg_id INTEGER,
            date TEXT,                 -- YYYY-MM-DD, NULL для заказов без слота
            time TEXT,                 -- HH:MM, NULL для заказов без слота
            quantity INTEGER NOT NULL DEFAULT 1,
            comment TEXT,
            status TEXT DEFAULT 'new', -- new -> done / cancelled
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (service_id) REFERENCES services (id)
        )
    """)

    # Сидируем демонстрационные позиции разных типов, только если таблица пустая —
    # чтобы сразу было видно, что подходит и под запись по времени, и под разовый заказ.
    cur.execute("SELECT COUNT(*) FROM services")
    if cur.fetchone()[0] == 0:
        demo_services = [
            ("Стрижка мужская", 1200, 30, "slot", 1),
            ("Аренда сапборда (1 час)", 1500, 60, "slot", 2),
            ("Консультация специалиста", 2000, 45, "slot", 3),
            ("Шаурма классическая", 350, 0, "order", 4),
            ("Доставка на дом", 500, 0, "order", 5),
        ]
        cur.executemany(
            "INSERT INTO services (name, price, duration_min, type, sort_order) VALUES (?, ?, ?, ?, ?)",
            demo_services,
        )

    cur.execute("SELECT COUNT(*) FROM theme_settings")
    if cur.fetchone()[0] == 0:
        cur.execute(
            """
            INSERT INTO theme_settings
                (id, bg_color, surface_color, text_color, hint_color,
                 primary_color, primary_text_color, danger_color, success_color, radius)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_THEME["bg_color"], DEFAULT_THEME["surface_color"],
                DEFAULT_THEME["text_color"], DEFAULT_THEME["hint_color"],
                DEFAULT_THEME["primary_color"], DEFAULT_THEME["primary_text_color"],
                DEFAULT_THEME["danger_color"], DEFAULT_THEME["success_color"],
                DEFAULT_THEME["radius"],
            ),
        )

    conn.commit()
    conn.close()


# ---------- Оформление (тема) ----------

def get_theme():
    conn = get_connection()
    row = conn.execute("SELECT * FROM theme_settings WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else dict(DEFAULT_THEME)


def update_theme(theme: dict):
    conn = get_connection()
    conn.execute(
        """
        UPDATE theme_settings
        SET bg_color = ?, surface_color = ?, text_color = ?, hint_color = ?,
            primary_color = ?, primary_text_color = ?, danger_color = ?,
            success_color = ?, radius = ?
        WHERE id = 1
        """,
        (
            theme["bg_color"], theme["surface_color"], theme["text_color"], theme["hint_color"],
            theme["primary_color"], theme["primary_text_color"], theme["danger_color"],
            theme["success_color"], theme["radius"],
        ),
    )
    conn.commit()
    conn.close()


# ---------- Позиции (услуги/товары) ----------

def get_services(active_only: bool = True):
    conn = get_connection()
    query = "SELECT * FROM services"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY sort_order, id"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_service(service_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_service(name: str, price: int, duration_min: int, type_: str):
    conn = get_connection()
    cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM services")
    next_order = cur.fetchone()[0]
    cur = conn.execute(
        "INSERT INTO services (name, price, duration_min, type, sort_order) VALUES (?, ?, ?, ?, ?)",
        (name, price, duration_min if type_ == "slot" else 0, type_, next_order),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_service(service_id: int, name: str, price: int, duration_min: int, type_: str, is_active: bool):
    conn = get_connection()
    conn.execute(
        """
        UPDATE services
        SET name = ?, price = ?, duration_min = ?, type = ?, is_active = ?
        WHERE id = ?
        """,
        (name, price, duration_min if type_ == "slot" else 0, type_, 1 if is_active else 0, service_id),
    )
    conn.commit()
    conn.close()


def delete_service(service_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
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


def get_available_slots(service_id: int, date: str):
    """Возвращает список свободных времён (HH:MM) для позиции на дату,
    с учётом её длительности и уже существующих записей в этот день."""
    service = get_service(service_id)
    if not service or service["type"] != "slot":
        return []
    duration = service["duration_min"]

    conn = get_connection()
    busy_rows = conn.execute(
        """
        SELECT b.time, s.duration_min
        FROM bookings b
        JOIN services s ON s.id = b.service_id
        WHERE b.date = ? AND b.status != 'cancelled' AND s.type = 'slot'
        """,
        (date,),
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

def create_booking(service_id, date, time, client_name, client_tg_id, quantity=1, comment=None):
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO bookings (service_id, client_name, client_tg_id, date, time, quantity, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (service_id, client_name, client_tg_id, date, time, quantity, comment),
    )
    conn.commit()
    booking_id = cur.lastrowid
    conn.close()
    return booking_id


def get_all_bookings(status: str = None):
    conn = get_connection()
    query = """
        SELECT b.id, b.date, b.time, b.client_name, b.client_tg_id, b.quantity,
               b.comment, b.status, b.created_at,
               s.name as service_name, s.price, s.type as service_type
        FROM bookings b JOIN services s ON s.id = b.service_id
    """
    params = ()
    if status:
        query += " WHERE b.status = ?"
        params = (status,)
    query += " ORDER BY b.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_booking_status(booking_id: int, status: str):
    conn = get_connection()
    conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    conn.commit()
    conn.close()
