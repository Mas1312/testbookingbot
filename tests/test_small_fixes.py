"""Скрытие позиции, занятое время удалённой позиции, экран ошибки загрузки."""
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
WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")


class _NoBackground:
    def add_task(self, *args, **kwargs):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        server.DEV_SKIP_INITDATA_CHECK = False
        database.init_db()
        self.business = database.create_business(OWNER, "Салон", TOKEN)
        self.bid = self.business["id"]
        tz = database.get_schedule(self.bid)["timezone"]
        self.date = (datetime.now(ZoneInfo(tz)) + timedelta(days=1)).date().isoformat()
        if self.date not in database.get_available_dates(self.bid):
            self.date = database.get_available_dates(self.bid)[1]

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK = self._orig
        self.tmp.cleanup()

    def book(self, service_id, time, **kw):
        return server.BookingRequest(business_id=self.bid, service_id=service_id, date=self.date, time=time,
                                     client_name="Клиент", **kw)


class HiddenServiceTest(Base):
    def setUp(self):
        super().setUp()
        self.sid = database.create_service(self.bid, "Стрижка", 1000, 60, "slot")

    def hide(self, active=False):
        s = database.get_service(self.bid, self.sid)
        database.update_service(self.bid, self.sid, s["name"], s["price"], s["duration_min"], s["type"], active)

    def test_hidden_service_is_not_offered_to_clients(self):
        self.hide()
        self.assertEqual(database.get_services(self.bid), [])
        self.assertEqual(len(database.get_services(self.bid, active_only=False)), 1)  # владелец её видит

    def test_hidden_service_does_not_accept_client_bookings(self):
        self.hide()
        with self.assertRaises(HTTPException) as ctx:
            server.api_book(self.book(self.sid, "10:00"), _NoBackground())
        self.assertEqual(ctx.exception.status_code, 404)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 0)
        conn.close()

    def test_visible_service_still_books(self):
        self.assertTrue(server.api_book(self.book(self.sid, "10:00"), _NoBackground())["ok"])

    def test_showing_it_again_restores_booking(self):
        self.hide()
        self.hide(True)
        self.assertTrue(server.api_book(self.book(self.sid, "10:00"), _NoBackground())["ok"])

    def test_owner_can_still_book_hidden_service_manually(self):
        self.hide()
        result = server.admin_create_booking(server.ManualBookingRequest(
            business_id=self.bid, service_id=self.sid, date=self.date, time="10:00", client_name="Анна",
            init_data=sign_init_data(OWNER, TOKEN)))
        self.assertTrue(result["ok"])


class DeletedServiceBlocksTimeTest(Base):
    def setUp(self):
        super().setUp()
        self.a = database.create_service(self.bid, "Окрашивание", 3000, 120, "slot")
        self.b = database.create_service(self.bid, "Стрижка", 1000, 30, "slot")

    def slots_b(self):
        return database.get_available_slots(self.bid, self.b, self.date)

    def test_snapshot_stored_at_booking(self):
        bid = server.api_book(self.book(self.a, "10:00"), _NoBackground())["booking_id"]
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT duration_min FROM bookings WHERE id = ?", (bid,)).fetchone()[0], 120)
        conn.close()

    def test_deleting_the_service_keeps_its_booking_blocking_the_calendar(self):
        server.api_book(self.book(self.a, "10:00"), _NoBackground())  # занято 10:00-12:00
        self.assertNotIn("11:00", self.slots_b())
        database.delete_service(self.bid, self.a)
        slots = self.slots_b()
        for blocked in ("10:00", "10:30", "11:00", "11:30"):
            self.assertNotIn(blocked, slots)
        self.assertIn("12:00", slots)
        self.assertIn("09:30", slots)

    def test_later_duration_edit_does_not_change_existing_booking(self):
        server.api_book(self.book(self.a, "10:00"), _NoBackground())
        database.update_service(self.bid, self.a, "Окрашивание", 3000, 30, "slot", True)
        self.assertNotIn("11:00", self.slots_b())  # всё ещё занято: бронь была на 120 минут

    def test_legacy_booking_without_snapshot_uses_the_service_duration(self):
        server.api_book(self.book(self.a, "10:00"), _NoBackground())
        conn = database.get_connection()
        conn.execute("UPDATE bookings SET duration_min = NULL")
        conn.commit()
        conn.close()
        self.assertNotIn("11:00", self.slots_b())

    def test_legacy_booking_of_deleted_service_blocks_at_least_one_step(self):
        server.api_book(self.book(self.a, "10:00"), _NoBackground())
        conn = database.get_connection()
        conn.execute("UPDATE bookings SET duration_min = NULL")
        conn.commit()
        conn.close()
        database.delete_service(self.bid, self.a)
        self.assertNotIn("10:00", self.slots_b())

    def test_cancelled_booking_of_deleted_service_frees_the_time(self):
        bid = server.api_book(self.book(self.a, "10:00"), _NoBackground())["booking_id"]
        database.delete_service(self.bid, self.a)
        database.update_booking_status(self.bid, bid, "cancelled")
        self.assertIn("10:00", self.slots_b())

    def test_orders_without_date_never_block_slots(self):
        order = database.create_service(self.bid, "Торт", 500, 0, "order")
        database.create_booking(self.bid, order, "Торт", 500, None, None, "Клиент", 5)
        database.delete_service(self.bid, order)
        self.assertEqual(self.slots_b(), database.get_available_slots(self.bid, self.b, self.date))
        self.assertIn("10:00", self.slots_b())

    def test_column_exists_after_init(self):
        conn = database.get_connection()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bookings)")]
        conn.close()
        self.assertIn("duration_min", cols)


class UiTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(WEBAPP, name), encoding="utf-8") as f:
            return f.read()

    def test_visibility_toggle_is_wired(self):
        html, js = self.read("index.html"), self.read("app.js")
        self.assertIn('id="service-active"', html)
        self.assertIn('el("service-active").checked', js)
        self.assertNotIn("is_active: true, init_data", js)  # раньше каждое сохранение молча показывало позицию

    def test_load_error_screen(self):
        html, js = self.read("index.html"), self.read("app.js")
        self.assertIn('id="load-error"', html)
        self.assertIn('id="load-retry-btn"', html)
        self.assertIn("showLoadError()", js)

    def test_script_is_versioned_so_browsers_do_not_serve_a_stale_copy(self):
        self.assertRegex(self.read("index.html"), r"app\.js\?v=\d+")


if __name__ == "__main__":
    unittest.main()
