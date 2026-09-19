"""Проверка Telegram WebApp initData — доказывает, что запрос действительно пришёл
от Telegram и именно от того пользователя, за которого себя выдаёт (в отличие от
owner_tg_id, который клиент мог бы просто вписать любой). Алгоритм — ровно тот,
что описан в официальной документации Telegram:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60  # initData считается просроченным через сутки


def verify_init_data(init_data: str, bot_token: str) -> int | None:
    """Возвращает подтверждённый Telegram user_id, если подпись верна и данные не
    просрочены, иначе None. bot_token обязательно должен быть токеном ИМЕННО ТОГО бота,
    из которого открыт Mini App — подпись у каждого бота своя."""
    if not init_data or not bot_token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if not auth_date or time.time() - int(auth_date) > MAX_INIT_DATA_AGE_SECONDS:
        return None

    try:
        user = json.loads(pairs.get("user", ""))
        return int(user["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
