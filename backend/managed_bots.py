"""Создание бота владельцу без BotFather: «управляемые боты» Telegram (Bot API 9.6).

Платформенный бот даёт владельцу ссылку https://t.me/newbot/<платформенный_бот>/<юзернейм>?name=<название>; владелец
подтверждает создание в самом Telegram, а нам приходит обновление `managed_bot` (кто создал и какой бот). Токен нового
бота берём методом getManagedBotToken — дальше он обычный бот бизнеса, как при подключении через BotFather.

Работает, только если у платформенного бота включён «Bot Management Mode» (BotFather -> Mini App -> настройки бота):
тогда getMe отдаёт can_manage_bots. Пока режим выключен, available() возвращает False, и подключение идёт по-старому
(токен от BotFather). Версию aiogram не обновляли, поэтому новый метод описан здесь сами."""
import logging
import random
import re
import time
from urllib.parse import quote_plus

from aiogram.methods.base import TelegramMethod

AVAILABILITY_TTL = 300  # секунд: как долго помним ответ getMe (режим включают один раз, дёргать getMe на каждого незачем)

_availability: dict[int, tuple[float, bool]] = {}  # id платформенного бота -> (когда проверили, можно ли)


class GetManagedBotToken(TelegramMethod[str]):
    """getManagedBotToken: токен управляемого бота по его user_id."""
    __returning__ = str
    __api_method__ = "getManagedBotToken"

    user_id: int


async def available(bot) -> bool:
    """Может ли платформенный бот создавать ботов (can_manage_bots в getMe). Ошибка getMe = «нет» и не запоминается."""
    cached = _availability.get(bot.id) if getattr(bot, "id", None) else None
    now = time.monotonic()
    if cached and now - cached[0] < AVAILABILITY_TTL:
        return cached[1]
    try:
        me = await bot.get_me()
    except Exception:
        logging.warning("getMe для проверки управляемых ботов не удался", exc_info=True)
        return False
    can = bool(getattr(me, "can_manage_bots", False))  # поле пришло из Telegram как дополнительное
    if getattr(bot, "id", None):
        _availability[bot.id] = (now, can)
    return can


def reset_cache():
    _availability.clear()


_RU = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
     "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


def suggest_username(name: str, rng: random.Random | None = None) -> str:
    """Предлагаемый username бота из названия бизнеса: латиница, цифры и «_», заканчивается на «bot», 5-32 символа.
    Владелец может поправить его в окне создания; цифры в середине снижают шанс, что имя уже занято."""
    rng = rng or random
    slug = "".join(_RU.get(ch, ch) for ch in name.lower())
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")[:18].strip("_") or "booking"
    username = f"{slug}_{rng.randint(100, 999)}_bot"
    if not username[0].isalpha():
        username = "b" + username
    return username[:32]


def newbot_link(manager_username: str, username: str, name: str) -> str:
    """Ссылка, по которой Telegram покажет окно создания бота с уже заполненными именем и username."""
    return f"https://t.me/newbot/{manager_username}/{username}?name={quote_plus(name[:64])}"


def parse_update(data: dict) -> tuple[int, int, str | None, str | None] | None:
    """(id создателя, id нового бота, username бота, имя бота) из поля managed_bot обновления или None, если формат
    незнакомый. Название поля с ботом в документации расходится (bot / bot_user), поэтому принимаем оба."""
    payload = data.get("managed_bot")
    if not isinstance(payload, dict):
        return None
    creator = payload.get("user")
    bot = payload.get("bot") or payload.get("bot_user")
    if not isinstance(creator, dict) or not isinstance(bot, dict):
        return None
    creator_id, bot_id = creator.get("id"), bot.get("id")
    if not isinstance(creator_id, int) or not isinstance(bot_id, int):
        return None
    return creator_id, bot_id, bot.get("username"), bot.get("first_name")
