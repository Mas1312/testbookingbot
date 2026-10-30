"""Токены ботов не должны попадать в журнал: aiohttp/aiogram в тексте ошибок печатают URL вида
https://api.telegram.org/bot<токен>/метод, а журнал читают при разборе сбоев."""
import logging
import re

TOKEN_PATTERN = re.compile(r"(bot)\d{6,}:[A-Za-z0-9_-]{30,}")


def redact(text: str) -> str:
    return TOKEN_PATTERN.sub(r"\1[скрыто]", text)


class RedactTokenFilter(logging.Filter):
    """Ставится на handler (фильтр логгера не видит записи дочерних логгеров). Текст и трейс форматируем заранее,
    потому что токен чаще всего сидит в исключении, а не в самом сообщении."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install():
    """Вешает фильтр на все уже созданные handler-ы (корневой и uvicorn); повторный вызов безопасен."""
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactTokenFilter) for f in handler.filters):
                handler.addFilter(RedactTokenFilter())
