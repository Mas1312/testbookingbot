"""База клиентов: привязка записей, поиск, статистика, заметки, изоляция бизнесов."""
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

TOKEN_A = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
TOKEN_B = "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN"
OWNER_A, OWNER_B = 4242, 5252


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
        self.a = database.create_business(OWNER_A, "Салон А", TOKEN_A)
        self.b = database.create_business(OWNER_B, "Салон Б", TOKEN_B)
        self.sa = database.create_service(self.a["id"], "Стрижка", 1000, 60, "slot")
        self.sb = database.create_service(self.b["id"], "Массаж", 2000, 60, "slot")
        tz = database.get_schedule(self.a["id"])["timezone"]
        today = datetime.now(ZoneInfo(tz)).date().isoformat()
        self.dates = [d for d in database.get_available_dates(self.a["id"]) if d != today]

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK = self._orig
        self.tmp.cleanup()

    def auth(self, owner=OWNER_A, token=TOKEN_A):
        return sign_init_data(owner, token)

    def book(self, business=None, service=None, time="10:00", date=None, name="Анна", tg=None, phone=None, status=None):
        business, service = business or self.a, service or self.sa
        booking_id = database.create_booking_checked(
            business["id"], service, "Услуга", 1000, date or self.dates[0], time, name, tg, client_phone=phone)
        self.assertIsNotNone(booking_id)
        if status:
            database.update_booking_status(business["id"], booking_id, status)
        return booking_id

    def rows(self, sql, *args):
        conn = database.get_connection()
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows


class LinkingTest(Base):
    def test_same_telegram_user_is_one_client(self):
        self.book(time="10:00", tg=77, name="Анна")
        self.book(time="12:00", tg=77, name="Анна К.")
        self.assertEqual(len(database.get_clients(self.a["id"])), 1)
        ids = {r["client_id"] for r in self.rows("SELECT client_id FROM bookings")}
        self.assertEqual(len(ids), 1)

    def test_same_phone_without_telegram_is_one_client(self):
        self.book(time="10:00", phone="+79001112233", name="Ольга")
        self.book(time="12:00", phone="+79001112233", name="Оля")
        self.assertEqual([c["name"] for c in database.get_clients(self.a["id"])], ["Ольга"])

    def test_no_contacts_means_separate_clients(self):
        self.book(time="10:00", name="Анна")
        self.book(time="12:00", name="Анна")
        self.assertEqual(len(database.get_clients(self.a["id"])), 2)

    def test_manual_client_becomes_the_telegram_one_when_phone_matches(self):
        self.book(time="10:00", phone="+79001112233", name="Ольга")           # внесена вручную
        self.book(time="12:00", phone="+79001112233", name="Ольга", tg=99)    # записалась сама
        clients = database.get_clients(self.a["id"])
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["tg_id"], 99)

    def test_generic_name_is_replaced_by_a_real_one(self):
        self.book(time="10:00", tg=5, name="Клиент")
        self.book(time="12:00", tg=5, name="Марина")
        self.assertEqual(database.get_clients(self.a["id"])[0]["name"], "Марина")
        self.book(time="14:00", tg=5, name="Другое имя")  # настоящее имя уже есть — не перезаписываем
        self.assertEqual(database.get_clients(self.a["id"])[0]["name"], "Марина")

    def test_businesses_are_isolated(self):
        self.book(tg=77, name="Анна")
        self.book(business=self.b, service=self.sb, tg=77, name="Анна")
        self.assertEqual(len(database.get_clients(self.a["id"])), 1)
        self.assertEqual(len(database.get_clients(self.b["id"])), 1)
        self.assertNotEqual(database.get_clients(self.a["id"])[0]["id"], database.get_clients(self.b["id"])[0]["id"])

    def test_client_booking_through_the_api_creates_and_reuses_the_client(self):
        req = lambda t: server.BookingRequest(business_id=self.a["id"], service_id=self.sa, date=self.dates[0], time=t,
                                              client_name="Света", init_data=self.auth(777, TOKEN_A))
        server.api_book(req("10:00"), _NoBackground())
        server.api_book(req("12:00"), _NoBackground())
        (client,) = database.get_clients(self.a["id"])
        self.assertEqual((client["name"], client["tg_id"], client["bookings_count"]), ("Света", 777, 2))


class BackfillTest(Base):
    def test_old_bookings_get_clients_and_it_is_idempotent(self):
        conn = database.get_connection()
        for name, tg, phone, tm in (("Анна", 7, None, "10:00"), ("Анна", 7, None, "12:00"),
                                    ("Ольга", None, "+79001112233", "13:00"), ("Ольга", None, "+79001112233", "14:00"),
                                    ("Иван", None, None, "15:00"), ("Иван", None, None, "16:00")):
            conn.execute("INSERT INTO bookings (business_id, service_id, client_name, client_tg_id, client_phone, date, time,"
                         " status) VALUES (?, ?, ?, ?, ?, ?, ?, 'done')", (self.a["id"], self.sa, name, tg, phone,
                                                                           self.dates[0], tm))
        conn.commit()
        conn.close()
        database.init_db()
        self.assertEqual(sorted(c["name"] for c in database.get_clients(self.a["id"])), ["Анна", "Иван", "Иван", "Ольга"])
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM bookings WHERE client_id IS NULL")[0]["n"], 0)
        before = [dict(r) for r in self.rows("SELECT * FROM clients ORDER BY id")]
        database.init_db()
        self.assertEqual([dict(r) for r in self.rows("SELECT * FROM clients ORDER BY id")], before)


class StatsTest(Base):
    def test_numbers(self):
        d0, d1 = self.dates[0], self.dates[1]
        self.book(time="10:00", tg=1, name="Анна", status="done")
        self.book(time="12:00", tg=1, name="Анна", status="done", date=d1)
        self.book(time="14:00", tg=1, name="Анна", status="cancelled")
        self.book(time="15:00", tg=1, name="Анна", status="confirmed", date=d1)
        (c,) = database.get_clients(self.a["id"])
        self.assertEqual(c["bookings_count"], 3)        # отменённая не считается
        self.assertEqual(c["visits"], 2)                # выполненные
        self.assertEqual(c["last_visit"], d1)
        self.assertEqual(c["next_booking"], f"{d1} 15:00")  # ближайшая будущая активная
        # цена берётся из снимка в заявке: 1000 ₽ × 2 выполненные
        self.assertEqual(c["spent"], 2000)

    def test_recent_activity_goes_first(self):
        self.book(time="10:00", tg=1, name="Старая")
        self.book(time="12:00", tg=2, name="Новая")
        conn = database.get_connection()
        conn.execute("UPDATE bookings SET created_at = '2026-01-01 10:00:00' WHERE client_name = 'Старая'")
        conn.commit()
        conn.close()
        self.assertEqual([c["name"] for c in database.get_clients(self.a["id"])], ["Новая", "Старая"])

    def test_search_ignores_case_for_cyrillic_and_matches_phone_digits(self):
        self.book(time="10:00", tg=1, name="Анна Петрова")
        self.book(time="12:00", name="Ольга", phone="+79001112233")
        names = lambda q: [c["name"] for c in database.get_clients(self.a["id"], search=q)]
        self.assertEqual(names("анна"), ["Анна Петрова"])
        self.assertEqual(names("ПЕТРОВ"), ["Анна Петрова"])
        self.assertEqual(names("900 111-22"), ["Ольга"])
        self.assertEqual(names("+7 (900) 111-22-33"), ["Ольга"])
        self.assertEqual(names("нет такого"), [])
        self.assertEqual(len(names("")), 2)


class AdminApiTest(Base):
    def setUp(self):
        super().setUp()
        self.book(time="10:00", tg=1, name="Анна", status="done")
        self.book(time="12:00", tg=1, name="Анна", status="confirmed", date=self.dates[1])
        self.client_id = database.get_clients(self.a["id"])[0]["id"]

    def test_only_the_owner_sees_clients(self):
        for kwargs in ({"init_data": self.auth(999, TOKEN_A)}, {"init_data": ""}):
            with self.assertRaises(HTTPException) as ctx:
                server.admin_list_clients(self.a["id"], **kwargs)
            self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:
            server.admin_get_client(self.client_id, self.a["id"], init_data=self.auth(OWNER_B, TOKEN_B))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_list_and_card(self):
        listed = server.admin_list_clients(self.a["id"], init_data=self.auth())
        self.assertEqual([c["name"] for c in listed], ["Анна"])
        card = server.admin_get_client(self.client_id, self.a["id"], init_data=self.auth())
        self.assertEqual(card["visits"], 1)
        self.assertEqual([h["status"] for h in card["history"]], ["confirmed", "done"])  # новые сверху

    def test_other_business_client_is_not_reachable(self):
        self.book(business=self.b, service=self.sb, tg=2, name="Чужая")
        foreign = database.get_clients(self.b["id"])[0]["id"]
        with self.assertRaises(HTTPException) as ctx:
            server.admin_get_client(foreign, self.a["id"], init_data=self.auth())
        self.assertEqual(ctx.exception.status_code, 404)
        payload = server.ClientUpdateRequest(business_id=self.a["id"], name="Взлом", init_data=self.auth())
        with self.assertRaises(HTTPException) as ctx:
            server.admin_update_client(foreign, payload)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(database.get_client(self.b["id"], foreign)["name"], "Чужая")

    def test_update_name_phone_notes(self):
        payload = server.ClientUpdateRequest(business_id=self.a["id"], name="  Анна К.  ", phone="8 900 111-22-33",
                                             notes="аллергия на латекс", init_data=self.auth())
        self.assertTrue(server.admin_update_client(self.client_id, payload)["ok"])
        card = server.admin_get_client(self.client_id, self.a["id"], init_data=self.auth())
        self.assertEqual((card["name"], card["phone"], card["notes"]), ("Анна К.", "+79001112233", "аллергия на латекс"))

    def test_update_validation(self):
        for extra in ({"phone": "12"}, {"name": "   "}):
            data = dict(business_id=self.a["id"], name="Анна", init_data=self.auth())
            data.update(extra)
            with self.assertRaises(HTTPException) as ctx:
                server.admin_update_client(self.client_id, server.ClientUpdateRequest(**data))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_clearing_phone_and_notes(self):
        server.admin_update_client(self.client_id, server.ClientUpdateRequest(
            business_id=self.a["id"], name="Анна", phone="+79001112233", notes="x", init_data=self.auth()))
        server.admin_update_client(self.client_id, server.ClientUpdateRequest(
            business_id=self.a["id"], name="Анна", phone="", notes="  ", init_data=self.auth()))
        card = database.get_client(self.a["id"], self.client_id)
        self.assertEqual((card["phone"], card["notes"]), (None, None))


class ManualBookingWithClientTest(Base):
    def manual(self, **kw):
        data = dict(business_id=self.a["id"], service_id=self.sa, date=self.dates[0], time="10:00",
                    client_name="Кто-то", init_data=self.auth())
        data.update(kw)
        return server.admin_create_booking(server.ManualBookingRequest(**data))

    def test_chosen_client_gets_the_booking_and_keeps_contacts(self):
        self.book(time="09:00", tg=1, name="Анна", phone="+79001112233", status="done")
        cid = database.get_clients(self.a["id"])[0]["id"]
        self.manual(client_id=cid, client_name="что угодно")
        self.assertEqual(len(database.get_clients(self.a["id"])), 1)
        row = self.rows("SELECT client_name, client_phone, client_id, client_tg_id FROM bookings ORDER BY id DESC")[0]
        self.assertEqual((row["client_name"], row["client_phone"], row["client_id"], row["client_tg_id"]),
                         ("Анна", "+79001112233", cid, 1))

    def test_foreign_client_id_is_rejected(self):
        self.book(business=self.b, service=self.sb, tg=2, name="Чужая")
        foreign = database.get_clients(self.b["id"])[0]["id"]
        with self.assertRaises(HTTPException) as ctx:
            self.manual(client_id=foreign)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM bookings WHERE business_id = ?", self.a["id"])[0]["n"], 0)

    def test_without_client_id_a_new_client_is_created_and_phone_dedups(self):
        self.manual(client_name="Ольга", client_phone="8 900 111 22 33", time="10:00")
        self.manual(client_name="Оля", client_phone="+7 900 111 22 33", time="12:00")
        self.assertEqual(len(database.get_clients(self.a["id"])), 1)
        self.manual(client_name="Иван", time="14:00")
        self.assertEqual(len(database.get_clients(self.a["id"])), 2)

    def test_chosen_client_without_telegram_gets_no_client_message_path(self):
        self.manual(client_name="Ольга", client_phone="+79001112233")
        cid = database.get_clients(self.a["id"])[0]["id"]
        self.manual(client_id=cid, time="12:00")
        row = self.rows("SELECT client_tg_id FROM bookings ORDER BY id DESC")[0]
        self.assertIsNone(row["client_tg_id"])  # нет Telegram — нет и напоминаний


class UiTest(unittest.TestCase):
    def test_wiring(self):
        webapp = os.path.join(os.path.dirname(__file__), "..", "webapp")
        with open(os.path.join(webapp, "index.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(webapp, "app.js"), encoding="utf-8") as f:
            js = f.read()
        for ident in ("admin-tab-clients", "admin-screen-clients", "clients-search", "clients-list", "admin-screen-client",
                      "client-name", "client-phone", "client-notes", "save-client-btn", "client-book-btn",
                      "client-history", "manual-clients"):
            self.assertIn(f'id="{ident}"', html)
            self.assertIn(f'"{ident}"', js)
        self.assertIn("client_id: state.admin.manualClientId", js)
        # данные клиента (имя, заметки, комментарии) вставляются в HTML только через escapeHtml
        self.assertNotIn("${client.name}", js)
        self.assertNotIn("${client.notes}", js)
        self.assertNotIn("${b.service_name}", js.replace("escapeHtml(b.service_name)", ""))


if __name__ == "__main__":
    unittest.main()
