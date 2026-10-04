"""График по дням недели: логика (weekly_schedule.py), расчёт слотов и дат, API расписания."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
import server  # noqa: E402
import weekly_schedule as ws  # noqa: E402
from fastapi import HTTPException  # noqa: E402

OWNER = 4242
STRANGER = 777


def day(open_=True, start="09:00", end="18:00", bs=None, be=None):
    return {"open": open_, "start": start, "end": end, "break_start": bs, "break_end": be}


def week(**overrides):
    """Все дни 09:00-18:00; overrides — {индекс_дня: словарь_дня}."""
    result = [day() for _ in range(7)]
    for index, value in overrides.items():
        result[int(index.lstrip("d"))] = value
    return result


class ParseTest(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(ws.parse_time("09:30"), 570)
        self.assertEqual(ws.parse_time("9:05"), 545)
        self.assertEqual(ws.parse_time("24:00"), 1440)
        for bad in ["", None, "24:30", "12:60", "abc", "9", "9:5", "-1:00", 930, "25:00"]:
            self.assertIsNone(ws.parse_time(bad), bad)

    def test_format_roundtrip(self):
        self.assertEqual(ws.format_time(ws.parse_time("07:05")), "07:05")


class ValidateTest(unittest.TestCase):
    def test_valid_week(self):
        self.assertIsNone(ws.validate_week(week()))
        self.assertIsNone(ws.validate_week(week(d2=day(bs="13:00", be="14:00"), d5=day(False), d6=day(False))))

    def test_wrong_shape(self):
        self.assertIn("7 дней", ws.validate_week([day()] * 6))
        self.assertIn("7 дней", ws.validate_week("nope"))

    def test_needs_at_least_one_open_day(self):
        self.assertIn("хотя бы один", ws.validate_week([day(False)] * 7))

    def test_end_must_be_after_start(self):
        error = ws.validate_week(week(d0=day(start="18:00", end="09:00")))
        self.assertIn("Понедельник", error)
        self.assertIn("позже", error)

    def test_bad_time_format_names_the_day(self):
        self.assertIn("Среда", ws.validate_week(week(d2=day(start="9am"))))

    def test_break_rules(self):
        self.assertIn("начало и конец", ws.validate_week(week(d1=day(bs="13:00"))))
        self.assertIn("позже, чем начинается", ws.validate_week(week(d1=day(bs="14:00", be="13:00"))))
        self.assertIn("внутри рабочего", ws.validate_week(week(d1=day(bs="08:00", be="09:30"))))
        self.assertIn("внутри рабочего", ws.validate_week(week(d1=day(bs="17:00", be="19:00"))))

    def test_closed_day_is_not_validated_beyond_flag(self):
        self.assertIsNone(ws.validate_week(week(d6={"open": False})))


class NormalizeAndLoadTest(unittest.TestCase):
    def test_closed_day_keeps_hours_but_drops_break(self):
        normal = ws.normalize_week(week(d6=day(False, "10:00", "15:00", "12:00", "12:30")))
        self.assertEqual(normal[6], {"open": False, "start": "10:00", "end": "15:00",
                                     "break_start": None, "break_end": None})

    def test_load_week_falls_back_on_garbage(self):
        for raw in [None, "", "not json", "[]", json.dumps([{"open": True}] * 3)]:
            loaded = ws.load_week(raw, 10, 20)
            self.assertEqual(len(loaded), 7)
            self.assertTrue(all(d["open"] and d["start"] == "10:00" and d["end"] == "20:00" for d in loaded), raw)

    def test_dump_load_roundtrip(self):
        original = week(d2=day(bs="13:00", be="14:00"), d6=day(False))
        loaded = ws.load_week(ws.dump_week(original), 9, 18)
        self.assertEqual(loaded, ws.normalize_week(original))

    def test_legacy_hours_cover_all_open_days(self):
        w = week(d0=day(start="10:30", end="16:00"), d5=day(start="09:00", end="20:15"), d6=day(False, "05:00", "23:00"))
        self.assertEqual(ws.legacy_hours(w), (9, 21))  # закрытый день не учитывается, конец округляется вверх


class ScheduleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        database.init_db()
        server.DEV_SKIP_INITDATA_CHECK = True
        self.business = database.create_business(OWNER, "Салон", "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")
        self.bid = self.business["id"]
        self.service = database.create_service(self.bid, "Стрижка", 1000, 60, "slot")
        self.tz = ZoneInfo(database.get_schedule(self.bid)["timezone"])

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK = self._orig
        self.tmp.cleanup()

    def next_date(self, weekday):
        """Ближайшая БУДУЩАЯ (не сегодня) дата с нужным днём недели, в часовом поясе бизнеса."""
        d = datetime.now(self.tz).date() + timedelta(days=1)
        while d.weekday() != weekday:
            d += timedelta(days=1)
        return d.isoformat()

    def save(self, weekly, step=30, days_ahead=14):
        database.update_schedule(self.bid, {"timezone": "Europe/Moscow", "slot_step_minutes": step,
                                            "days_ahead": days_ahead, "weekly": weekly})

    def slots(self, weekday):
        return database.get_available_slots(self.bid, self.service, self.next_date(weekday))


class SlotsTest(ScheduleBase):
    def test_default_business_behaves_as_before(self):
        schedule = database.get_schedule(self.bid)
        self.assertEqual(len(schedule["weekly"]), 7)
        self.assertTrue(all(d["open"] for d in schedule["weekly"]))
        for wd in range(7):
            self.assertEqual(self.slots(wd)[0], "09:00")
            self.assertEqual(self.slots(wd)[-1], "17:00")  # 60-минутная услуга должна уложиться до 18:00

    def test_closed_day_has_no_slots(self):
        self.save(week(d0=day(False)))
        self.assertEqual(self.slots(0), [])
        self.assertTrue(self.slots(1))

    def test_each_weekday_has_its_own_hours(self):
        self.save(week(d5=day(start="10:00", end="14:00")), step=60)
        self.assertEqual(self.slots(5), ["10:00", "11:00", "12:00", "13:00"])
        self.assertEqual(self.slots(1)[0], "09:00")

    def test_break_blocks_overlapping_slots_only(self):
        self.save(week(d2=day(bs="13:00", be="14:00")), step=30)
        slots = self.slots(2)
        self.assertIn("12:00", slots)       # 12:00-13:00 заканчивается ровно к началу перерыва
        for blocked in ("12:30", "13:00", "13:30"):
            self.assertNotIn(blocked, slots, blocked)
        self.assertIn("14:00", slots)

    def test_break_applies_only_to_its_own_day(self):
        self.save(week(d2=day(bs="13:00", be="14:00")))
        self.assertIn("13:00", self.slots(3))

    def test_existing_booking_and_break_combine(self):
        self.save(week(d4=day(bs="12:00", be="13:00")))
        date = self.next_date(4)
        database.create_booking(self.bid, self.service, "Стрижка", 1000, date, "10:00", "Анна", 1)
        slots = database.get_available_slots(self.bid, self.service, date)
        self.assertNotIn("10:00", slots)
        self.assertNotIn("11:30", slots)   # пересекает перерыв 12:00-13:00? 11:30-12:30 — да
        self.assertIn("11:00", slots)

    def test_booking_on_closed_day_is_refused(self):
        self.save(week(d6=day(False)))
        date = self.next_date(6)
        result = database.create_booking_checked(self.bid, self.service, "Стрижка", 1000, date, "10:00", "Анна", 1)
        self.assertIsNone(result)

    def test_garbage_date_returns_empty_instead_of_crashing(self):
        self.assertEqual(database.get_available_slots(self.bid, self.service, "не-дата"), [])


class DatesTest(ScheduleBase):
    def test_closed_weekdays_are_not_offered(self):
        self.save(week(d5=day(False), d6=day(False)), days_ahead=14)
        dates = database.get_available_dates(self.bid)
        weekdays = {datetime.strptime(d, "%Y-%m-%d").weekday() for d in dates}
        self.assertTrue(weekdays)
        self.assertFalse(weekdays & {5, 6})
        self.assertLessEqual(len(dates), 10)

    def test_all_days_open_gives_full_range(self):
        self.save(week(), days_ahead=7)
        self.assertEqual(len(database.get_available_dates(self.bid)), 7)


class PersistenceTest(ScheduleBase):
    def test_weekly_roundtrip_and_legacy_hours_derived(self):
        self.save(week(d0=day(start="10:00", end="16:00"), d5=day(start="09:00", end="20:00"), d6=day(False)))
        schedule = database.get_schedule(self.bid)
        self.assertEqual(schedule["weekly"][5]["end"], "20:00")
        self.assertFalse(schedule["weekly"][6]["open"])
        self.assertEqual((schedule["work_start_hour"], schedule["work_end_hour"]), (9, 20))

    def test_saving_uniform_hours_resets_weekly_schedule(self):
        self.save(week(d0=day(False)))
        database.update_schedule(self.bid, {"timezone": "Europe/Moscow", "work_start_hour": 11, "work_end_hour": 19,
                                            "slot_step_minutes": 30, "days_ahead": 7})
        schedule = database.get_schedule(self.bid)
        self.assertTrue(all(d["open"] and d["start"] == "11:00" and d["end"] == "19:00" for d in schedule["weekly"]))

    def test_corrupt_stored_json_does_not_break_slots(self):
        conn = database.get_connection()
        conn.execute("UPDATE businesses SET weekly_schedule = ? WHERE id = ?", ("{broken", self.bid))
        conn.commit()
        conn.close()
        self.assertTrue(self.slots(1))


def request(**kwargs):
    base = dict(business_id=None, timezone="Europe/Moscow", slot_step_minutes=30, days_ahead=7, owner_tg_id=OWNER)
    base.update(kwargs)
    return server.ScheduleRequest(**base)


class ApiTest(ScheduleBase):
    def put(self, **kwargs):
        return server.admin_update_schedule(request(business_id=self.bid, **kwargs))

    def test_weekly_saved_and_returned(self):
        result = self.put(weekly=week(d6=day(False), d2=day(bs="13:00", be="14:00")))
        self.assertTrue(result["ok"])
        self.assertFalse(result["schedule"]["weekly"][6]["open"])
        got = server.admin_get_schedule(self.bid, init_data="", owner_tg_id=OWNER)
        self.assertEqual(got["weekly"][2]["break_start"], "13:00")

    def test_invalid_weekly_gives_400_with_reason(self):
        with self.assertRaises(HTTPException) as ctx:
            self.put(weekly=week(d3=day(start="18:00", end="09:00")))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Четверг", ctx.exception.detail)

    def test_all_closed_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self.put(weekly=[day(False)] * 7)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_legacy_payload_without_weekly_still_works(self):
        result = self.put(work_start_hour=10, work_end_hour=20)
        self.assertTrue(all(d["start"] == "10:00" for d in result["schedule"]["weekly"]))

    def test_neither_hours_nor_weekly_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self.put()
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException) as ctx:
            self.put(work_start_hour=18, work_end_hour=9)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_wrong_number_of_days_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self.put(weekly=[day()] * 5)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_stranger_cannot_change_schedule(self):
        with self.assertRaises(HTTPException) as ctx:
            server.admin_update_schedule(request(business_id=self.bid, owner_tg_id=STRANGER, weekly=week()))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_booking_endpoint_refuses_closed_day(self):
        self.put(weekly=week(d1=day(False)))
        date = self.next_date(1)
        with self.assertRaises(HTTPException) as ctx:
            server.api_book(server.BookingRequest(business_id=self.bid, service_id=self.service, date=date,
                                                  time="10:00", client_name="Анна", client_tg_id=5),
                            background_tasks=_NoBackground())
        self.assertEqual(ctx.exception.status_code, 409)


class _NoBackground:
    def add_task(self, *args, **kwargs):
        pass


if __name__ == "__main__":
    unittest.main()
