"""Проверка, что два одновременных клиента не займут один слот.

Запуск из корня проекта:  venv\\Scripts\\python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402


class BookingRaceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = database.DB_PATH
        database.DB_PATH = os.path.join(self._tmp.name, "test.db")
        database.init_db()
        self.business = database.create_business(1, "Тест", "0:test-token")
        self.business_id = self.business["id"] if isinstance(self.business, dict) else self.business
        self.service_id = database.create_service(self.business_id, "Стрижка", 1000, 60, "slot")
        tz = database.get_schedule(self.business_id)["timezone"]
        # завтра — чтобы «сегодня уже прошло» не отфильтровало слоты
        self.date = (datetime.now(ZoneInfo(tz)) + timedelta(days=1)).date().isoformat()

    def tearDown(self):
        database.DB_PATH = self._orig_path
        self._tmp.cleanup()

    def _book(self, time, name="Клиент"):
        return database.create_booking_checked(
            self.business_id, self.service_id, "Стрижка", 1000, self.date, time, name, 100,
        )

    def test_sequential_second_booking_is_rejected(self):
        self.assertIsNotNone(self._book("10:00"))
        self.assertIsNone(self._book("10:00"))

    def test_overlapping_duration_is_rejected(self):
        self.assertIsNotNone(self._book("10:00"))       # 10:00-11:00
        self.assertIsNone(self._book("10:30"))          # пересекается
        self.assertIsNotNone(self._book("11:00"))       # встык — можно

    def test_concurrent_bookings_only_one_wins(self):
        results = []
        barrier = threading.Barrier(12)

        def worker(i):
            barrier.wait()
            results.append(self._book("12:00", f"Клиент {i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 12)
        self.assertEqual(sum(r is not None for r in results), 1, results)
        conn = database.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE business_id = ? AND date = ? AND time = '12:00'",
            (self.business_id, self.date),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
