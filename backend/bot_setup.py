"""Настройка Telegram-бота владельца и тексты онбординга.

При подключении бизнеса (/newbusiness) мы сами выставляем у ЕГО бота то, что иначе пришлось бы делать руками
в BotFather: меню-кнопку «Записаться» (рядом с полем ввода — клиенту одно касание вместо двух), список
команд и описание, которое клиент видит на пустом экране бота до нажатия /start.
Всё «по возможности»: сбой любого шага только логируется и не мешает подключению."""
import logging
import re

from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonCommands, MenuButtonWebApp, WebAppInfo

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


def platform_short_description(name: str) -> str:
    return "Конструктор Telegram-записи. /newbusiness — подключить бизнес"[:120]


def platform_description(name: str) -> str:
    """{name} — это TeleSlot сам по себе: у него нет клиентской записи, только подключение бизнесов."""
    text = (
        f"{name} — конструктор онлайн-записи в Telegram.\n\n"
        "Команда /newbusiness подключит ваш бизнес: за пару минут появится свой бот с записью, "
        "приёмом заявок в чат и напоминаниями клиентам."
    )
    return text[:512]


def commands(platform: bool) -> list[BotCommand]:
    if platform:
        # У TeleSlot самого нет записи и «Моих записей» — только подключение бизнесов.
        return [
            BotCommand(command="start", description="Что такое TeleSlot"),
            BotCommand(command="newbusiness", description="Подключить бизнес"),
        ]
    return [
        BotCommand(command="start", description="Записаться"),
        BotCommand(command="my", description="Мои записи"),
    ]


async def configure_bot(bot: Bot, business: dict, platform: bool = False) -> list[str]:
    """Выставляет меню-кнопку, команды и описания. Возвращает названия шагов, которые не удались.

    Платформенный бот (TeleSlot) не показывает Mini App записи — у него нет ни услуг, ни клиентов,
    только /newbusiness, поэтому меню-кнопка — обычный список команд, а не WebApp."""
    failed = []
    menu_button = (
        MenuButtonCommands() if platform
        else MenuButtonWebApp(text=MENU_BUTTON_TEXT, web_app=WebAppInfo(url=mini_app_url(business["id"])))
    )
    desc = platform_description(business["name"]) if platform else description(business["name"])
    short_desc = platform_short_description(business["name"]) if platform else short_description(business["name"])
    steps = {
        "menu_button": lambda: bot.set_chat_menu_button(menu_button=menu_button),
        "commands": lambda: bot.set_my_commands(commands(platform)),
        "description": lambda: bot.set_my_description(description=desc),
        "short_description": lambda: bot.set_my_short_description(short_description=short_desc),
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

PLATFORM_START_TEXT = (
    "TeleSlot — конструктор онлайн-записи в Telegram.\n\n"
    "Подключите свой бизнес: получите отдельного бота с записью, приёмом заявок в чат и "
    "напоминаниями клиентам — без установки приложений."
)
