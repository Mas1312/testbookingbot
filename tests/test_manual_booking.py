"""Ручная запись владельцем: доступ только владельцу, общий календарь с клиентскими записями, без уведомлений."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_cabinet import sign_init_data  # noqa: E402

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
OWNER = 4242
STRANGER = 777


class ManualBookingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        server.DEV_SKIP_INITDATA_CHECK = False
        database.init_db()
        self.business = database.create_business(OWNER, "Салон", TOKEN)
        self.bid = self.business["id"]
        self.service_id = database.create_service(self.bid, "Стрижка", 1000, 60, "slot")
        self.order_id = database.create_service(self.bid, "Торт", 500, 0, "order")
        self.dates = database.get_available_dates(self.bid)
        # не сегодня: иначе «время уже прошло» зависит от часа запуска теста
        tz = database.get_schedule(self.bid)["timezone"]
        today = datetime.now(ZoneInfo(tz)).date().isoformat()
        self.date = next(d for d in self.dates if d != today)

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK = self._orig
        self.tmp.cleanup()

    def req(self, **kw):
        data = dict(business_id=self.bid, service_id=self.service_id, date=self.date, time="10:00",
                    client_name="Анна", init_data=sign_init_data(OWNER, TOKEN))
        data.update(kw)
        return server.ManualBookingRequest(**data)

    def rows(self):
        conn = database.get_connection()
        rows = conn.execute("SELECT * FROM bookings WHERE business_id = ?", (self.bid,)).fetchall()
        conn.close()
        return rows

    def test_owner_creates_confirmed_booking_without_telegram_client(self):
        result = server.admin_create_booking(self.req(client_phone="8 (900) 123-45-67", comment="по звонку"))
        self.assertTrue(result["ok"])
        (row,) = self.rows()
        self.assertEqual((row["status"], row["client_tg_id"], row["client_name"]), ("confirmed", None, "Анна"))
        self.assertEqual((row["service_name"], row["price"], row["time"]), ("Стрижка", 1000, "10:00"))
        self.assertEqual((row["client_phone"], row["comment"]), ("+79001234567", "по звонку"))
        self.assertIsNone(row["consent_at"])

    def test_only_the_owner_may_book(self):
        with self.assertRaises(HTTPException) as ctx:
            server.admin_create_booking(self.req(init_data=sign_init_data(STRANGER, TOKEN)))
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:
            server.admin_create_booking(self.req(init_data=""))
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.rows(), [])

    def test_owner_of_another_business_is_rejected(self):
        other = database.create_business(STRANGER, "Чужой", "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN")
        with self.assertRaises(HTTPException) as ctx:
            server.admin_create_booking(self.req(init_data=sign_init_data(STRANGER, other["bot_token"])))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_taken_slot_conflicts_both_ways(self):
        server.admin_create_booking(self.req(time="10:00"))
        with self.assertRaises(HTTPException) as ctx:
            server.admin_create_booking(self.req(time="10:30"))  # пересекается с 10:00-11:00
        self.assertEqual(ctx.exception.status_code, 409)
        # клиентская запись на то же время тоже не пройдёт: календарь общий
        self.assertNotIn("10:00", database.get_available_slots(self.bid, self.service_id, self.date))
        self.assertIsNone(database.create_booking_checked(self.bid, self.service_id, "Стрижка", 1000,
                                                          self.date, "10:00", "Клиент", 5))
        server.admin_create_booking(self.req(time="11:00"))  # встык — можно
        self.assertEqual(len(self.rows()), 2)

    def test_cancelled_booking_frees_the_slot(self):
        server.admin_create_booking(self.req(time="10:00"))
        database.update_booking_status(self.bid, self.rows()[0]["id"], "cancelled")
        server.admin_create_booking(self.req(time="10:00"))
        self.assertEqual(len(self.rows()), 2)

    def test_validation(self):
        bad = [
            (dict(service_id=self.order_id), 400),               # разовый заказ без расписания
            (dict(service_id=99999), 404),                       # чужая/несуществующая позиция
            (dict(date="2020-01-01"), 400),                      # дата вне доступных
            (dict(date="не дата"), 400),
            (dict(client_phone="12"), 400),                      # не телефон
            (dict(client_name="   "), 400),                      # пустое имя
        ]
        for extra, code in bad:
            with self.subTest(extra=extra), self.assertRaises(HTTPException) as ctx:
                server.admin_create_booking(self.req(**extra))
            self.assertEqual(ctx.exception.status_code, code)
        self.assertEqual(self.rows(), [])

    def test_time_outside_schedule_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            server.admin_create_booking(self.req(time="03:00"))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_master_is_required_and_checked_when_enabled(self):
        master = database.create_master(self.bid, "Анна", [self.service_id]) if hasattr(database, "create_master") else None
        if master is None:
            self.skipTest("create_master недоступен")
        mid = master["id"] if isinstance(master, dict) else master
        database.set_use_masters(self.bid, True)
        with self.assertRaises(HTTPException) as ctx:
            server.admin_create_booking(self.req())
        self.assertEqual(ctx.exception.status_code, 400)  # «Выберите мастера»
        server.admin_create_booking(self.req(master_id=mid))
        self.assertEqual(self.rows()[0]["master_id"], mid)

    def test_does_not_notify_anyone(self):
        import notifications
        sent = []

        async def fake_send(*args, **kwargs):
            sent.append(args)

        orig = notifications._safe_send
        notifications._safe_send = fake_send
        try:
            server.admin_create_booking(self.req())
        finally:
            notifications._safe_send = orig
        self.assertEqual(sent, [])

    def test_manual_booking_appears_in_admin_list(self):
        server.admin_create_booking(self.req())
        listed = server.admin_list_bookings(self.bid, init_data=sign_init_data(OWNER, TOKEN))
        self.assertEqual([(b["client_name"], b["status"]) for b in listed], [("Анна", "confirmed")])


class ManualBookingUiTest(unittest.TestCase):
    """Фронтенд без браузера: форма и кнопка на месте, версия скрипта поднята (иначе кэш отдаст старый app.js)."""

    def test_form_wired_up(self):
        webapp = os.path.join(os.path.dirname(__file__), "..", "webapp")
        with open(os.path.join(webapp, "index.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(webapp, "app.js"), encoding="utf-8") as f:
            js = f.read()
        for ident in ("add-manual-btn", "admin-screen-manual", "manual-service", "manual-date", "manual-time",
                      "manual-name", "save-manual-btn"):
            self.assertIn(f'id="{ident}"', html)
            self.assertIn(f'"{ident}"', js)


if __name__ == "__main__":
    unittest.main()
