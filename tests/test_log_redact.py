"""Токен бота не должен доезжать до журнала, даже если он в тексте исключения."""
import io
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import log_redact  # noqa: E402

TOKEN = "123456789:AAH" + "x" * 32


class RedactTokenTest(unittest.TestCase):
    def _logger(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(log_redact.RedactTokenFilter())
        logger = logging.getLogger("test_log_redact")
        logger.handlers = [handler]
        logger.propagate = False
        return logger, stream

    def test_message_is_cleaned(self):
        logger, stream = self._logger()
        logger.error("Cannot connect to https://api.telegram.org/bot%s/getMe", TOKEN)
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertIn("api.telegram.org/bot[скрыто]/getMe", stream.getvalue())

    def test_traceback_is_cleaned(self):
        logger, stream = self._logger()
        try:
            raise RuntimeError(f"https://api.telegram.org/bot{TOKEN}/sendMessage failed")
        except RuntimeError:
            logger.exception("send failed")
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertIn("RuntimeError", stream.getvalue())

    def test_plain_text_untouched(self):
        self.assertEqual(log_redact.redact("booking 12:30 ok"), "booking 12:30 ok")

    def test_installed_on_server_handlers(self):
        import server  # noqa: F401
        root = logging.getLogger()
        self.assertTrue(all(any(isinstance(f, log_redact.RedactTokenFilter) for f in h.filters)
                            for h in root.handlers))


if __name__ == "__main__":
    unittest.main()
