"""Ссылка и QR-код бота бизнеса — то, что владелец раздаёт клиентам (Instagram, 2ГИС, визитка, стойка)."""
import io
import logging

import segno
from aiogram import Bot

import bot_setup
import database


def make_qr_png(link: str) -> bytes:
    """PNG с QR-кодом ссылки: ~500 px, поля по 4 модуля (как требует стандарт, иначе сканеры путаются),
    средняя коррекция ошибок — читается и с помятой распечатки."""
    qr = segno.make(link, error="m")
    out = io.BytesIO()
    qr.save(out, kind="png", scale=12, border=4)
    return out.getvalue()


async def ensure_bot_username(business: dict) -> str | None:
    """Username бота без @: из БД, а если его там нет (бизнесы, заведённые до этой функции) —
    спрашиваем у Telegram по токену и запоминаем. None — если Telegram не ответил."""
    if business.get("bot_username"):
        return business["bot_username"]
    bot = Bot(token=business["bot_token"])
    try:
        me = await bot.get_me()
    except Exception as exc:
        logging.warning("getMe для business_id=%s не удался: %s", business["id"], type(exc).__name__)
        return None
    finally:
        await bot.session.close()
    database.set_bot_username(business["id"], me.username)
    return me.username


def share_caption(business_name: str, link: str) -> str:
    return (
        f"Ссылка для клиентов «{business_name}»:\n{link}\n\n"
        "QR-код выше можно распечатать и поставить у стойки или на визитке. "
        "Ссылку вставьте в Instagram, 2ГИС, мессенджеры: клиент нажмёт и сразу попадёт на запись."
    )


def link_for(username: str) -> str:
    return bot_setup.bot_link(username)
