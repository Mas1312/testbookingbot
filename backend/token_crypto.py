"""Шифрование токенов ботов в базе.

Токен бота — полный контроль над ботом владельца, поэтому в БД (и в её резервных копиях) он лежит
зашифрованным, а ключ — отдельно, в `.env` (TOKEN_ENCRYPTION_KEY, права 600). Так утечка одного только файла
базы или копии из хранилища токенов не раскрывает. От взлома самого сервера (где есть и база, и ключ) это не защищает.

Формат значения в БД: "enc:v1:<токен Fernet>" (AES + HMAC, подделку обнаруживает). Значение без этого префикса —
обычный токен «как раньше»: пока ключ не задан (локальная разработка, тесты), всё работает открытым текстом, а когда
ключ появляется, `database.init_db` сам зашифровывает старые строки.

Для поиска бизнеса по токену (проверка «этот бот уже подключён») храним отдельный отпечаток — HMAC-SHA256 токена
под тем же ключом: по нему токен не подобрать, но два одинаковых токена дают одинаковый отпечаток.

ВАЖНО: потеряете ключ — зашифрованные токены не восстановить (владельцам придётся заново подключать ботов).
Ключ надо хранить отдельно от сервера (как фразу шифрования бэкапов)."""
import base64
import hashlib
import hmac
import os

import config  # noqa: F401  (config загружает .env в os.environ при импорте)

PREFIX = "enc:v1:"
KEY_ENV = "TOKEN_ENCRYPTION_KEY"
_DEV_PEPPER = b"teleslot-no-encryption-key"  # без ключа секретности нет; отпечаток нужен только для поиска


class TokenCryptoError(RuntimeError):
    pass


def _key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def enabled() -> bool:
    return _key() is not None


def _fernet():
    key = _key()
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # библиотека нужна, только когда ключ задан
        raise TokenCryptoError("Задан TOKEN_ENCRYPTION_KEY, но не установлена библиотека cryptography") from exc
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError(
            "TOKEN_ENCRYPTION_KEY некорректен: нужен ключ Fernet (44 символа base64, "
            "создаётся командой Fernet.generate_key())") from exc


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)


def encrypt(token: str) -> str:
    """Токен -> значение для БД. Без ключа возвращает токен как есть (режим разработки)."""
    if not enabled():
        return token
    return PREFIX + _fernet().encrypt(token.encode()).decode()


def decrypt(value: str) -> str:
    """Значение из БД -> токен. Старые (открытые) значения возвращаются как есть. Если значение зашифровано, а ключа нет
    или он не тот, падаем сразу и громко: молча отдать мусор вместо токена хуже, чем остановиться."""
    if not is_encrypted(value):
        return value
    if not enabled():
        raise TokenCryptoError("В базе зашифрованные токены, а TOKEN_ENCRYPTION_KEY не задан")
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("Токен не расшифровывается: неверный TOKEN_ENCRYPTION_KEY или повреждённые данные") from exc


def token_hash(token: str) -> str:
    """Отпечаток токена для поиска/уникальности (HMAC-SHA256 под ключом; без ключа — под фиксированной «солью»)."""
    key = (_key() or "").encode() or _DEV_PEPPER
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def looks_like_key(value: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(value.encode())) == 32
    except (ValueError, TypeError):
        return False
