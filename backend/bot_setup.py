"""Настройка Telegram-бота владельца и тексты онбординга.

При подключении бизнеса (/newbusiness) мы сами выставляем у ЕГО бота то, что иначе пришлось бы делать руками
в BotFather: меню-кнопку «Записаться» (рядом с полем ввода — клиенту одно касание вместо двух), список
команд и описание, которое клиент видит на пустом экране бота до нажатия /start.
Всё «по возможности»: сбой любого шага только логируется и не мешает подключению."""
import logging
import re

from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import WEBAPP_URL

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
MENU_BUTTON_TEXT = "Записаться"


def looks_like_token(text: str) -> bool:
    return bool(TOKEN_RE.match((text or "").strip()))


def bot_link(username: str) -> str:
    return f"https://t.me/{username}"


def mini_app_url(business_id: int) -> str:
    return f"{WEBAPP_URL}/?business_id={business_id}"


def short_description(name: str) -> str:
    """Короткое описание (лимит Telegram — 120 символов): показывается в профиле бота и при пересылке."""
    return f"Онлайн-запись: {name}"[:120]


def description(name: str) -> str:
    """Описание на пустом экране бота (лимит — 512 символов)."""
    text = (
        f"Онлайн-запись «{name}».\n\n"
        "Нажмите «Записаться» слева от поля ввода (или /start), выберите услугу и удобное время. "
        "Подтверждение и напоминания придут сюда, в чат."
    )
    return text[:512]


def commands(platform: bool) -> list[BotCommand]:
    result = [
        BotCommand(command="start", description="Записаться"),
        BotCommand(command="my", description="Мои записи"),
    ]
    if platform:
        result.append(BotCommand(command="newbusiness", description="Подключить свой бизнес"))
    return result


async def configure_bot(bot: Bot, business: dict, platform: bool = False) -> list[str]:
    """Выставляет меню-кнопку, команды и описания. Возвращает названия шагов, которые не удались."""
    failed = []
    steps = {
        "menu_button": lambda: bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text=MENU_BUTTON_TEXT, web_app=WebAppInfo(url=mini_app_url(business["id"]))
            )
        ),
        "commands": lambda: bot.set_my_commands(commands(platform)),
        "description": lambda: bot.set_my_description(description=description(business["name"])),
        "short_description": lambda: bot.set_my_short_description(
            short_description=short_description(business["name"])
        ),
    }
    for name, call in steps.items():
        try:
            await call()
        except Exception as exc:
            logging.warning("настройка бота business_id=%s, шаг %s: %s", business["id"], name, exc)
            failed.append(name)
    return failed


async def configure_business_bot(business: dict, platform: bool = False) -> list[str]:
    """То же, но со своим Bot-инстансом по токену бизнеса (после закрывает сессию)."""
    bot = Bot(token=business["bot_token"])
    try:
        return await configure_bot(bot, business, platform)
    finally:
        await bot.session.close()


# ---------------- Тексты онбординга ----------------

def ask_token_text(name: str) -> str:
    return (
        f"Принял: «{name}».\n\n"
        "Теперь нужен свой бот: через него ваши клиенты будут записываться. Создать его можно за пару минут:\n\n"
        "1. Откройте @BotFather (https://t.me/BotFather)\n"
        "2. Отправьте ему команду /newbot\n"
        "3. Название бота: его увидят клиенты, например «Маникюр у Анны»\n"
        "4. Username бота: латиницей, обязательно оканчивается на bot, например anna_nails_bot\n"
        "5. BotFather пришлёт сообщение с токеном: длинная строка вида 123456789:AAE... "
        "Скопируйте её целиком и пришлите сюда.\n\n"
        "Токен — это ключ от вашего бота. Я сразу удалю ваше сообщение с ним из этого чата; "
        "никому другому его не показывайте.\n\n"
        "Отмена — /cancel"
    )


NOT_A_TOKEN_TEXT = (
    "Это не похоже на токен. Он выглядит так: 123456789:AAE... и приходит от @BotFather в сообщении "
    "«Use this token to access the HTTP API». Скопируйте его целиком и пришлите сюда, или /cancel."
)

TOKEN_REJECTED_TEXT = (
    "Telegram не принял этот токен: возможно, он скопирован не полностью или бот удалён. "
    "Проверьте и пришлите ещё раз, или /cancel."
)

TOKEN_ALREADY_USED_TEXT = "Этот бот уже подключён к системе. Пришлите токен другого бота, или /cancel."


def done_text(name: str, username: str) -> str:
    return (
        f"Готово! «{name}» подключён: @{username}\n"
        f"Ссылка для клиентов: {bot_link(username)}\n\n"
        "Что дальше (пара минут):\n"
        f"1. Откройте своего бота по ссылке выше и нажмите /start. Это обязательно: иначе Telegram не даст ему "
        "присылать вам уведомления о новых заявках.\n"
        "2. Нажмите «Записаться»: откроется настройка. Выберете, чем занимаетесь, добавите услуги и рабочие часы.\n"
        "3. Когда всё готово, вставьте ссылку на бота в Instagram, 2ГИС или отправьте клиентам в мессенджере."
    )


NOT_PLATFORM_TEXT = "Нажмите /start, чтобы записаться, или /my, чтобы посмотреть свои записи."
