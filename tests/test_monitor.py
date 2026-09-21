"""Логика решений в ops/monitor_reachability.evaluate."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))

import monitor_reachability as m  # noqa: E402

OK = {"ru1.node.check-host.net": True, "ru2.node.check-host.net": True, "ru3.node.check-host.net": True}
BAD = {"ru1.node.check-host.net": False, "ru2.node.check-host.net": False, "ru3.node.check-host.net": True}


class EvaluateTest(unittest.TestCase):
    def test_all_ok_is_silent(self):
        state, msg = m.evaluate(OK, True, {})
        self.assertIsNone(msg)
        self.assertEqual(state["bad_runs"], 0)

    def test_single_bad_run_does_not_alert(self):
        state, msg = m.evaluate(BAD, True, {})
        self.assertIsNone(msg)
        self.assertEqual(state["bad_runs"], 1)

    def test_second_bad_run_alerts_once(self):
        state, _ = m.evaluate(BAD, True, {})
        state, msg = m.evaluate(BAD, True, state)
        self.assertIn("блокировку", msg)
        self.assertTrue(state["alerted"])
        state, msg = m.evaluate(BAD, True, state)  # третий — уже не повторяем
        self.assertIsNone(msg)

    def test_recovery_message(self):
        state, _ = m.evaluate(BAD, True, {})
        state, _ = m.evaluate(BAD, True, state)
        state, msg = m.evaluate(OK, True, state)
        self.assertIn("восстановилась", msg)
        self.assertFalse(state["alerted"])

    def test_one_failed_node_is_not_an_outage(self):
        one_bad = dict(OK, **{"ru1.node.check-host.net": False})
        state, msg = m.evaluate(one_bad, True, {"bad_runs": 5})
        self.assertIsNone(msg)
        self.assertEqual(state["bad_runs"], 0)

    def test_app_down_locally_has_different_text(self):
        state, _ = m.evaluate(BAD, False, {})
        _, msg = m.evaluate(BAD, False, state)
        self.assertIn("не отвечает на самом сервере", msg)

    def test_no_data_keeps_state(self):
        nodes = {"ru1.node.check-host.net": None, "ru2.node.check-host.net": None, "ru3.node.check-host.net": True}
        state, msg = m.evaluate(nodes, True, {"bad_runs": 1})
        self.assertIsNone(msg)
        self.assertEqual(state["bad_runs"], 1)

    def test_parse_node_result(self):
        self.assertIsNone(m.parse_node_result(None))
        self.assertTrue(m.parse_node_result([{"address": "1.2.3.4", "time": 0.01}]))
        self.assertFalse(m.parse_node_result([{"error": "Connection timed out"}]))


if __name__ == "__main__":
    unittest.main()
