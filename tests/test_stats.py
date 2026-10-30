"""Статистика владельца: расчёты (database.get_stats), доступ к API, разметка вкладки."""
import os
import sys
import tempfile
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_cabinet import sign_init_data  # noqa: E402

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
OTHER_TOKEN = "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN"
OWNER, OTHER_OWNER = 4242, 5252
WEBAPP = os.path.join(os.path.dirname(__file__), "..", "webapp")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        server.DEV_SKIP_INITDATA_CHECK = False
        database.init_db()
        database.create_business(1, "Заполнитель", "923456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM")
        self.biz = database.create_business(OWNER, "Салон", TOKEN)
        self.other = database.create_business(OTHER_OWNER, "Чужой", OTHER_TOKEN)
        self.bid = self.biz["id"]
        self.today = database._now_in_business_tz(database.get_schedule(self.bid)["timezone"]).date()

    def tearDown(self):
        database.DB_PATH, server.DEV_SKIP_INITDATA_CHECK = self._orig
        self.tmp.cleanup()

    def day(self, days_ago):
        return self.today - timedelta(days=days_ago)

    def add(self, days_ago=0, time="10:00", status="done", price=1000, qty=1, service="Стрижка", master=None, client=None,
            business=None, date=True, created=None):
        """Запись напрямую в БД: дата = сегодня - days_ago (отрицательное — будущее)."""
        business = business or self.biz
        day = self.day(days_ago).isoformat()
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO bookings (business_id, service_id, service_name, price, quantity, client_name, client_id, date, time,"
            " status, master_name, created_at) VALUES (?, 1, ?, ?, ?, 'Клиент', ?, ?, ?, ?, ?, ?)",
            (business["id"], service, price, qty, client, day if date else None, time if date else None, status, master,
             created or f"{day} 09:00:00"),
        )
        conn.commit()
        conn.close()

    def stats(self, period=30):
        return database.get_stats(self.bid, period)


class TotalsTest(Base):
    def test_held_rule_and_numbers(self):
        self.add(2, status="done", price=1000)                    # состоялась
        self.add(3, status="confirmed", price=500)                # подтверждена и прошла: тоже состоялась
        self.add(4, status="new", price=700)                      # не подтверждена: записей +1, выручки нет
        self.add(1, status="cancelled", price=900)                # отмена
        self.add(-2, status="confirmed", price=800)               # будущая: не в периоде, но «ожидается»
        self.add(40, status="done", price=300)                    # до периода
        s = self.stats(30)
        t = s["totals"]
        self.assertEqual((t["revenue"], t["completed"], t["bookings"], t["cancelled"]), (1500, 2, 3, 1))
        self.assertEqual((t["avg_check"], t["cancel_rate"]), (750, 25.0))
        self.assertEqual(s["upcoming"], {"count": 1, "revenue": 800})
        self.assertEqual(s["previous"]["revenue"], 300)           # предыдущие 30 дней: today-59 .. today-30

    def test_quantity_multiplies_price(self):
        self.add(1, price=350, qty=3)
        self.assertEqual(self.stats()["totals"]["revenue"], 1050)

    def test_orders_without_a_date_count_by_creation_day(self):
        self.add(2, status="done", price=500, date=False)
        s = self.stats()
        self.assertEqual((s["totals"]["revenue"], s["totals"]["bookings"]), (500, 1))
        self.assertEqual((sum(s["weekdays"]), sum(s["hours"])), (0, 0))   # без времени в «когда загружено» не попадают

    def test_confirmed_order_without_time_is_not_held_until_done(self):
        self.add(2, status="confirmed", price=500, date=False)
        self.assertEqual(self.stats()["totals"]["revenue"], 0)

    def test_empty_business(self):
        s = self.stats(7)
        self.assertEqual(s["totals"], {"revenue": 0, "bookings": 0, "completed": 0, "avg_check": 0, "cancelled": 0,
                                       "cancel_rate": 0.0})
        self.assertEqual((len(s["series"]), s["services"], s["masters"], s["upcoming"]),
                         (7, [], [], {"count": 0, "revenue": 0}))

    def test_other_business_never_leaks(self):
        self.add(1, price=1000)
        self.add(1, price=9999, business=self.other, client=1)
        self.assertEqual(self.stats()["totals"]["revenue"], 1000)
        self.assertEqual(database.get_stats(self.other["id"], 30)["totals"]["revenue"], 9999)


class BucketsTest(Base):
    def fill(self):
        for ago in (0, 1, 3, 8, 20, 35, 70, 120, 300):
            self.add(ago, price=100 + ago, client=ago + 1)

    def test_bucket_kinds_and_lengths(self):
        self.fill()
        self.assertEqual([(p, self.stats(p)["bucket"], len(self.stats(p)["series"])) for p in (7, 30)],
                         [(7, "day", 7), (30, "day", 30)])
        self.assertEqual(self.stats(90)["bucket"], "week")
        self.assertEqual(self.stats(365)["bucket"], "month")
        self.assertEqual(self.stats(7)["series"][-1]["date"], self.today.isoformat())

    def test_buckets_add_up_to_the_totals(self):
        self.fill()
        for period in (7, 30, 90, 365):
            with self.subTest(period=period):
                s = self.stats(period)
                self.assertEqual(sum(b["revenue"] for b in s["series"]), s["totals"]["revenue"])
                self.assertEqual(sum(b["bookings"] for b in s["series"]), s["totals"]["bookings"])
                dates = [b["date"] for b in s["series"]]
                self.assertEqual(dates, sorted(dates))
                self.assertEqual(len(dates), len(set(dates)))

    def test_empty_days_are_zero_filled(self):
        self.add(0, price=500)
        s = self.stats(7)
        self.assertEqual([b["revenue"] for b in s["series"]], [0, 0, 0, 0, 0, 0, 500])

    def test_weeks_start_on_monday_and_months_on_the_first(self):
        self.fill()
        for b in self.stats(90)["series"]:
            self.assertEqual(self.today.fromisoformat(b["date"]).weekday(), 0)
        for b in self.stats(365)["series"]:
            self.assertTrue(b["date"].endswith("-01"))


class BreakdownsTest(Base):
    def test_weekday_and_hour_counts(self):
        monday = next(self.today - timedelta(days=n) for n in range(1, 8) if (self.today - timedelta(days=n)).weekday() == 0)
        ago = (self.today - monday).days
        self.add(ago, time="10:30")
        self.add(ago, time="10:00", status="confirmed")
        self.add(ago, time="15:00", status="cancelled")           # отменённые не считаются
        s = self.stats(30)
        self.assertEqual(s["weekdays"], [2, 0, 0, 0, 0, 0, 0])
        self.assertEqual((s["hours"][10], s["hours"][15], sum(s["hours"])), (2, 0, 2))

    def test_services_sorted_by_revenue_and_tail_is_folded(self):
        for i in range(10):
            self.add(1, price=100 * (i + 1), service=f"Услуга {i}")
        self.add(1, price=999999, status="cancelled", service="Отменённая")
        services = self.stats()["services"]
        self.assertEqual(len(services), 8)
        self.assertEqual(services[0]["name"], "Услуга 9")
        self.assertEqual(services[-1], {"name": "Остальные", "revenue": 100 + 200 + 300, "count": 3, "other": True})
        self.assertEqual(sum(s["revenue"] for s in services), sum(100 * (i + 1) for i in range(10)))

    def test_masters_only_when_named(self):
        self.add(1, price=1000, master="Анна")
        self.add(1, price=500, master="Анна", time="12:00")
        self.add(1, price=300, master="Борис", time="14:00")
        self.add(1, price=200, master=None, time="16:00")
        self.assertEqual([(m["name"], m["revenue"], m["count"]) for m in self.stats()["masters"]],
                         [("Анна", 1500, 2), ("Борис", 300, 1)])

    def test_clients_new_and_returning(self):
        self.add(60, client=1)                                    # клиент 1 пришёл давно
        self.add(5, client=1, time="11:00")                       # и вернулся: «вернувшийся»
        self.add(3, client=2, time="11:00")                       # клиент 2 впервые: «новый»
        self.add(3, client=2, time="13:00")                       # второй раз в тот же день: в столбце один раз
        s = self.stats(30)
        self.assertEqual(s["clients"], {"new": 1, "returning": 1})
        by_date = {b["date"]: b for b in s["series"]}
        self.assertEqual((by_date[self.day(5).isoformat()]["returning_clients"], by_date[self.day(5).isoformat()]["new_clients"]),
                         (1, 0))
        self.assertEqual((by_date[self.day(3).isoformat()]["new_clients"], by_date[self.day(3).isoformat()]["returning_clients"]),
                         (1, 0))

    def test_cancelled_visits_do_not_make_a_client_returning(self):
        self.add(60, client=1, status="cancelled")
        self.add(2, client=1)
        self.assertEqual(self.stats()["clients"], {"new": 1, "returning": 0})


class ApiTest(Base):
    def call(self, **kw):
        data = dict(business_id=self.bid, init_data=sign_init_data(OWNER, TOKEN))
        data.update(kw)
        return server.admin_stats(**data)

    def test_owner_gets_the_numbers(self):
        self.add(1, price=700)
        result = self.call(period=7)
        self.assertEqual((result["totals"]["revenue"], result["period"], result["use_masters"]), (700, 7, False))

    def test_bad_period_is_rejected(self):
        for period in (0, 5, 31, 1000, -7):
            with self.subTest(period=period), self.assertRaises(HTTPException) as ctx:
                self.call(period=period)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_only_the_owner(self):
        for init_data in ("", sign_init_data(999, TOKEN), sign_init_data(OTHER_OWNER, OTHER_TOKEN)):
            with self.subTest(init_data=init_data[:10]), self.assertRaises(HTTPException) as ctx:
                server.admin_stats(self.bid, init_data=init_data)
            self.assertEqual(ctx.exception.status_code, 403)


class UiTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(WEBAPP, name), encoding="utf-8") as f:
            return f.read()

    def test_wiring(self):
        html, js, css = self.read("index.html"), self.read("app.js"), self.read("style.css")
        for ident in ("admin-tab-stats", "admin-screen-stats", "stats-period", "stats-body", "stats-tiles", "stats-revenue-chart",
                      "stats-clients-chart", "stats-weekday-chart", "stats-hours-chart", "stats-services-chart",
                      "stats-masters-chart", "stats-metric-revenue", "stats-metric-bookings", "stats-empty", "stats-upcoming"):
            self.assertIn(f'id="{ident}"', html)
            if ident not in ("stats-period",):             # блок периодов в JS ищется по классу .period-pill
                self.assertIn(f'"{ident}"', js)
        self.assertIn("/api/admin/stats", js)
        self.assertIn("data-table-toggle", html)            # у каждого графика есть таблица-двойник
        self.assertEqual(html.count("data-table-toggle"), 5)

    def test_period_buttons_do_not_collide_with_the_order_filters(self):
        """Кнопки периода не должны быть .filter-pill: на этот класс уже повешен фильтр статусов заявок."""
        html = self.read("index.html")
        block = html[html.index('id="stats-period"'):html.index('id="stats-body"')]
        self.assertNotIn("filter-pill", block)
        self.assertEqual(block.count("period-pill"), 4)
        for days in (7, 30, 90, 365):
            self.assertIn(f'data-period="{days}"', block)

    def test_texts_go_in_as_text_and_chart_follows_the_design_rules(self):
        js, css = self.read("app.js"), self.read("style.css")
        start = js.index("// АДМИН-РЕЖИМ: статистика")
        stats_js = js[start:js.index("// АДМИН-РЕЖИМ: перенос записи")]
        # названия услуг и мастеров попадают в страницу только через textContent / атрибуты, не через HTML-строки
        self.assertNotIn("innerHTML = `", stats_js)
        self.assertNotIn("insertAdjacentHTML", stats_js)
        self.assertNotIn("innerHTML +=", stats_js)
        self.assertIn("Math.min(24", stats_js)           # столбцы не толще 24 px
        self.assertIn("grid-line", css)
        self.assertNotIn("stroke-dasharray", css)         # сетка сплошная, не пунктир
        self.assertIn("aria-label", stats_js)             # у графиков есть текстовое описание


if __name__ == "__main__":
    unittest.main()
