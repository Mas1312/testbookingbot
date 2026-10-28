"""Перенос записи клиентом и владельцем: слоты, гонки, мастера, напоминания, уведомления, доступ."""
import asyncio
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
import notifications  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_cabinet import sign_init_data  # noqa: E402

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
OWNER, CLIENT, OTHER = 4242, 1001, 1002


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs.get("reply_markup")))


def run(coro):
    return asyncio.run(coro)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK, notifications.get_bot)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        server.DEV_SKIP_INITDATA_CHECK = False
        database.init_db()
        self.business = database.create_business(OWNER, "Салон", TOKEN)
        self.bid = self.business["id"]
        self.service = database.create_service(self.bid, "Стрижка", 1000, 60, "slot")
        tz = database.get_schedule(self.bid)["timezone"]
        today = datetime.now(ZoneInfo(tz)).date().isoformat()
        self.dates = [d for d in database.get_available_dates(self.bid) if d != today]
        self.bot = FakeBot()
        notifications.get_bot = lambda business: self.bot

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK, notifications.get_bot = self._orig
        self.tmp.cleanup()

    def book(self, time="10:00", date=None, client=CLIENT, status=None, service=None, master=None):
        booking_id = database.create_booking_checked(
            self.bid, service or self.service, "Стрижка", 1000, date or self.dates[0], time, "Анна", client,
            master_id=master["id"] if master else None, master_name=master["name"] if master else None)
        self.assertIsNotNone(booking_id)
        if status:
            database.update_booking_status(self.bid, booking_id, status)
        return booking_id

    def move(self, booking_id, time, date=None):
        return database.reschedule_booking(self.bid, booking_id, date or self.dates[0], time)

    def row(self, booking_id):
        conn = database.get_connection()
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        conn.close()
        return row


class DatabaseTest(Base):
    def test_moves_to_a_free_slot_and_keeps_the_status(self):
        b = self.book("10:00", status="confirmed")
        outcome = self.move(b, "14:00", self.dates[1])
        self.assertEqual((outcome["result"], outcome["old_date"], outcome["old_time"]), ("ok", self.dates[0], "10:00"))
        row = self.row(b)
        self.assertEqual((row["date"], row["time"], row["status"]), (self.dates[1], "14:00", "confirmed"))

    def test_own_slot_does_not_block_a_move_that_overlaps_it(self):
        b = self.book("10:00")                       # 10:00-11:00
        self.assertEqual(self.move(b, "10:30")["result"], "ok")  # 10:30-11:30 пересекает собственное время
        self.assertEqual(self.row(b)["time"], "10:30")

    def test_busy_and_overlapping_slots_are_rejected(self):
        b = self.book("10:00")
        self.book("12:00")                           # 12:00-13:00
        self.assertEqual(self.move(b, "12:00")["result"], "busy")
        self.assertEqual(self.move(b, "11:30")["result"], "busy")   # 11:30-12:30 пересекается с 12:00
        self.assertEqual(self.move(b, "11:00")["result"], "ok")     # встык — можно
        self.assertEqual(self.row(b)["time"], "11:00")

    def test_cancelled_booking_frees_its_time_for_a_move(self):
        b = self.book("10:00")
        other = self.book("12:00", status="cancelled")
        self.assertEqual(self.move(b, "12:00")["result"], "ok")
        self.assertEqual(self.row(other)["status"], "cancelled")

    def test_same_time_and_bad_date(self):
        b = self.book("10:00")
        self.assertEqual(self.move(b, "10:00")["result"], "same")
        for bad in ("2020-01-01", "не дата", "2099-01-01"):
            with self.subTest(bad=bad):
                self.assertEqual(database.reschedule_booking(self.bid, b, bad, "10:00")["result"], "bad_date")

    def test_time_outside_working_hours_is_busy(self):
        b = self.book("10:00")
        self.assertEqual(self.move(b, "03:00")["result"], "busy")
        self.assertEqual(self.row(b)["time"], "10:00")

    def test_not_reschedulable(self):
        done = self.book("10:00", status="done")
        cancelled = self.book("12:00", status="cancelled")
        order = database.create_service(self.bid, "Торт", 500, 0, "order")
        order_booking = database.create_booking(self.bid, order, "Торт", 500, None, None, "Анна", CLIENT)
        for booking_id in (done, cancelled, order_booking):
            with self.subTest(booking_id=booking_id):
                self.assertEqual(self.move(booking_id, "16:00")["result"], "not_reschedulable")

    def test_started_booking_cannot_be_moved(self):
        conn = database.get_connection()
        cur = conn.execute("INSERT INTO bookings (business_id, service_id, client_name, client_tg_id, date, time, status)"
                           " VALUES (?, ?, 'Анна', ?, '2020-01-01', '10:00', 'confirmed')", (self.bid, self.service, CLIENT))
        conn.commit()
        conn.close()
        self.assertEqual(self.move(cur.lastrowid, "10:00")["result"], "not_reschedulable")

    def test_deleted_service_cannot_be_rescheduled(self):
        b = self.book("10:00")
        database.delete_service(self.bid, self.service)
        self.assertEqual(self.move(b, "14:00")["result"], "not_reschedulable")

    def test_unknown_and_foreign_business_booking(self):
        b = self.book("10:00")
        other = database.create_business(5252, "Другой", "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN")
        self.assertEqual(database.reschedule_booking(self.bid, 9999, self.dates[0], "11:00")["result"], "not_found")
        self.assertEqual(database.reschedule_booking(other["id"], b, self.dates[0], "11:00")["result"], "not_found")
        self.assertEqual(self.row(b)["time"], "10:00")

    def test_masters_have_separate_calendars(self):
        database.create_master(self.bid, "Анна", [self.service])
        database.create_master(self.bid, "Борис", [self.service])
        database.set_use_masters(self.bid, True)
        anna, boris = database.get_masters(self.bid)
        b = self.book("10:00", master=anna)
        self.book("12:00", master=boris)
        self.book("14:00", master=anna)
        self.assertEqual(self.move(b, "12:00")["result"], "ok")      # у Бориса занято, у Анны свободно
        b2 = self.book("16:00", master=anna)
        self.assertEqual(self.move(b2, "14:00")["result"], "busy")   # у самой Анны занято
        self.assertEqual(self.row(b)["master_id"], anna["id"])        # мастер не меняется

    def test_slot_list_includes_own_time_only_when_excluded(self):
        b = self.book("10:00")
        self.assertNotIn("10:00", database.get_available_slots(self.bid, self.service, self.dates[0]))
        self.assertIn("10:00", database.get_available_slots(self.bid, self.service, self.dates[0], exclude_booking_id=b))

    def test_concurrent_moves_into_one_slot_only_one_wins(self):
        ids = [self.book(f"{9 + i:02d}:00") for i in range(5)]   # 9:00 ... 13:00, затем все хотят на 16:00
        results = []
        barrier = threading.Barrier(len(ids))

        def worker(booking_id):
            barrier.wait()
            results.append(self.move(booking_id, "16:00")["result"])

        threads = [threading.Thread(target=worker, args=(i,)) for i in ids]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ["busy"] * 4 + ["ok"])

    def test_reminders_restart_for_a_far_future_time(self):
        b = self.book("10:00")
        database.mark_reminded(b, ["24h", "2h"])
        self.move(b, "14:00", self.dates[-1])
        row = self.row(b)
        self.assertEqual((row["reminded_24h"], row["reminded_2h"]), (0, 0))

    def test_no_instant_reminder_when_moved_close_to_now(self):
        b = self.book("10:00")
        soon = datetime.now(timezone.utc) + timedelta(hours=1)
        with mock.patch.object(database, "booking_start", lambda d, t, tz: soon):
            self.move(b, "14:00", self.dates[1])
        row = self.row(b)
        self.assertEqual((row["reminded_24h"], row["reminded_2h"]), (1, 1))
        later = datetime.now(timezone.utc) + timedelta(hours=10)
        b2 = self.book("12:00")
        with mock.patch.object(database, "booking_start", lambda d, t, tz: later):
            self.move(b2, "15:00", self.dates[1])
        row = self.row(b2)
        self.assertEqual((row["reminded_24h"], row["reminded_2h"]), (1, 0))


class NotificationsTest(Base):
    def test_client_move_notifies_the_owner_only(self):
        b = self.book("10:00", status="confirmed")
        booking, result = run(notifications.reschedule_booking(self.business, b, self.dates[1], "15:00", client_tg_id=CLIENT))
        self.assertEqual(result, "ok")
        (chat, text, markup), = self.bot.sent
        self.assertEqual(chat, OWNER)
        self.assertIn("Клиент перенёс запись", text)
        self.assertIn("Было", text)
        self.assertIn("15:00", text)
        self.assertIsNotNone(markup)

    def test_owner_move_notifies_the_client(self):
        b = self.book("10:00", status="confirmed")
        booking, result = run(notifications.reschedule_booking(self.business, b, self.dates[1], "15:00"))
        self.assertEqual(result, "ok")
        (chat, text, markup), = self.bot.sent
        self.assertEqual(chat, CLIENT)
        self.assertIn("Запись перенесена", text)
        self.assertIn("Было", text)
        self.assertIn("Стало", text)
        self.assertIn("15:00", text)

    def test_nothing_is_sent_for_a_client_without_telegram_or_the_owner_himself(self):
        manual = self.book("10:00", client=None)
        run(notifications.reschedule_booking(self.business, manual, self.dates[1], "15:00"))
        own = self.book("12:00", client=OWNER)
        run(notifications.reschedule_booking(self.business, own, self.dates[1], "17:00"))
        self.assertEqual(self.bot.sent, [])

    def test_someone_elses_booking_cannot_be_moved_by_a_client(self):
        b = self.book("10:00", client=CLIENT)
        booking, result = run(notifications.reschedule_booking(self.business, b, self.dates[1], "15:00", client_tg_id=OTHER))
        self.assertEqual((booking, result), (None, "not_found"))
        self.assertEqual((self.row(b)["time"], self.bot.sent), ("10:00", []))

    def test_failed_move_sends_nothing(self):
        b = self.book("10:00")
        self.book("12:00")
        _, result = run(notifications.reschedule_booking(self.business, b, self.dates[0], "12:00"))
        self.assertEqual((result, self.bot.sent), ("busy", []))


class ApiTest(Base):
    def client_auth(self, user=CLIENT):
        return sign_init_data(user, TOKEN)

    def owner_auth(self):
        return sign_init_data(OWNER, TOKEN)

    def payload(self, time, date=None, **kw):
        data = dict(business_id=self.bid, date=date or self.dates[1], time=time, init_data=self.client_auth())
        data.update(kw)
        return server.RescheduleRequest(**data)

    def err(self, coro_or_call):
        with self.assertRaises(HTTPException) as ctx:
            result = coro_or_call()
            if asyncio.iscoroutine(result):
                run(result)
        return ctx.exception.status_code

    def test_my_bookings_flags(self):
        slot = self.book("10:00")
        cancelled = self.book("12:00", status="cancelled")
        order = database.create_service(self.bid, "Торт", 500, 0, "order")
        order_booking = database.create_booking(self.bid, order, "Торт", 500, None, None, "Анна", CLIENT)
        flags = {b["id"]: b["can_reschedule"] for b in server.api_my_bookings(self.bid, init_data=self.client_auth())}
        self.assertEqual(flags, {slot: True, cancelled: False, order_booking: False})

    def test_client_reschedules_own_booking(self):
        b = self.book("10:00")
        self.assertTrue(run(server.api_reschedule_my_booking(b, self.payload("15:00")))["ok"])
        self.assertEqual((self.row(b)["date"], self.row(b)["time"]), (self.dates[1], "15:00"))

    def test_client_cannot_move_someone_elses_booking(self):
        b = self.book("10:00", client=OTHER)
        self.assertEqual(self.err(lambda: server.api_reschedule_my_booking(b, self.payload("15:00"))), 404)
        self.assertEqual(self.row(b)["time"], "10:00")

    def test_no_signature_is_rejected(self):
        b = self.book("10:00")
        self.assertEqual(self.err(lambda: server.api_reschedule_my_booking(b, self.payload("15:00", init_data=""))), 403)

    def test_error_codes(self):
        b = self.book("10:00")
        self.book("12:00", date=self.dates[1])
        self.assertEqual(self.err(lambda: server.api_reschedule_my_booking(b, self.payload("12:00"))), 409)       # занято
        self.assertEqual(self.err(lambda: server.api_reschedule_my_booking(b, self.payload("10:00", date=self.dates[0]))), 400)
        self.assertEqual(self.err(lambda: server.api_reschedule_my_booking(b, self.payload("10:00", date="2020-01-01"))), 400)

    def test_suspended_business_does_not_accept_client_moves(self):
        # первый бизнес в базе платформенный (его не приостанавливают), поэтому проверяем на обычном втором
        token = "323456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lO"
        biz = database.create_business(7777, "Салон 2", token)
        service = database.create_service(biz["id"], "Стрижка", 1000, 60, "slot")
        booking_id = database.create_booking_checked(biz["id"], service, "Стрижка", 1000, self.dates[0], "10:00", "Анна", CLIENT)
        conn = database.get_connection()
        conn.execute("UPDATE businesses SET trial_until = ? WHERE id = ?",
                     ((datetime.utcnow() - timedelta(days=1)).strftime(database.DB_TIME_FORMAT), biz["id"]))
        conn.commit()
        conn.close()
        client = server.RescheduleRequest(business_id=biz["id"], date=self.dates[1], time="15:00",
                                          init_data=sign_init_data(CLIENT, token))
        self.assertEqual(self.err(lambda: server.api_reschedule_my_booking(booking_id, client)), 403)
        # владелец при этом может переносить сам
        owner = server.RescheduleRequest(business_id=biz["id"], date=self.dates[1], time="15:00",
                                         init_data=sign_init_data(7777, token))
        self.assertTrue(run(server.admin_reschedule_booking(booking_id, owner))["ok"])

    def test_slots_endpoint_shows_own_time_to_its_client_only(self):
        b = self.book("10:00")
        slots = server.api_my_booking_reschedule_slots(b, self.bid, self.dates[0], init_data=self.client_auth())
        self.assertIn("10:00", slots)
        self.assertEqual(self.err(lambda: server.api_my_booking_reschedule_slots(
            b, self.bid, self.dates[0], init_data=self.client_auth(OTHER))), 404)
        # публичный список слотов по-прежнему считает это время занятым
        self.assertNotIn("10:00", server.api_slots(self.service, self.dates[0], self.bid))

    def test_admin_endpoints_require_the_owner(self):
        b = self.book("10:00")
        for call in (
            lambda: server.admin_reschedule_slots(b, self.bid, self.dates[0], init_data=self.client_auth()),
            lambda: server.admin_reschedule_booking(b, self.payload("15:00", init_data=self.client_auth())),
        ):
            self.assertEqual(self.err(call), 403)
        self.assertEqual(self.row(b)["time"], "10:00")

    def test_admin_slots_and_reschedule(self):
        b = self.book("10:00", client=None)
        self.assertIn("10:00", server.admin_reschedule_slots(b, self.bid, self.dates[0], init_data=self.owner_auth()))
        result = run(server.admin_reschedule_booking(b, self.payload("15:00", init_data=self.owner_auth())))
        self.assertTrue(result["ok"])
        self.assertEqual((self.row(b)["date"], self.row(b)["time"]), (self.dates[1], "15:00"))

    def test_admin_cannot_reach_another_business(self):
        other = database.create_business(5252, "Другой", "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN")
        b = self.book("10:00")
        payload = server.RescheduleRequest(business_id=other["id"], date=self.dates[1], time="15:00",
                                           init_data=sign_init_data(5252, "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN"))
        self.assertEqual(self.err(lambda: server.admin_reschedule_booking(b, payload)), 404)
        self.assertEqual(self.row(b)["time"], "10:00")


class UiTest(unittest.TestCase):
    def test_wiring(self):
        webapp = os.path.join(os.path.dirname(__file__), "..", "webapp")
        with open(os.path.join(webapp, "index.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(webapp, "app.js"), encoding="utf-8") as f:
            js = f.read()
        for ident in ("screen-reschedule", "reschedule-dates", "reschedule-slots", "reschedule-confirm",
                      "admin-screen-reschedule", "admin-reschedule-date", "admin-reschedule-time", "admin-reschedule-save"):
            self.assertIn(f'id="{ident}"', html)
            self.assertIn(f'"{ident}"', js)
        self.assertIn("/reschedule", js)
        self.assertIn("can_reschedule", js)


if __name__ == "__main__":
    unittest.main()
