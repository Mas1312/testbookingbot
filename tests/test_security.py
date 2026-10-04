"""Шифрование токенов ботов в базе и очистка подписанных данных Telegram из журнала."""
import hashlib
import io
import logging
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402
import server  # noqa: E402
import token_crypto  # noqa: E402
import webhooks  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from test_cabinet import sign_init_data  # noqa: E402

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
TOKEN_A = "123456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lM"
TOKEN_B = "223456789:AAEhBOweik6ad9r_QXMENQjcrTu-Ge1S3lN"
OWNER = 4242


class CryptoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (database.DB_PATH, os.environ.get(token_crypto.KEY_ENV), server.DEV_SKIP_INITDATA_CHECK)
        database.DB_PATH = os.path.join(self.tmp.name, "t.db")
        os.environ.pop(token_crypto.KEY_ENV, None)
        server.DEV_SKIP_INITDATA_CHECK = False
        database.init_db()

    def tearDown(self):
        database.DB_PATH, key, server.DEV_SKIP_INITDATA_CHECK = self._orig
        if key is None:
            os.environ.pop(token_crypto.KEY_ENV, None)
        else:
            os.environ[token_crypto.KEY_ENV] = key
        self.tmp.cleanup()

    def use_key(self, key=KEY):
        os.environ[token_crypto.KEY_ENV] = key

    def raw(self, business_id):
        conn = sqlite3.connect(database.DB_PATH)
        row = conn.execute("SELECT bot_token, bot_token_hash FROM businesses WHERE id = ?", (business_id,)).fetchone()
        conn.close()
        return row


class WithoutKeyTest(CryptoBase):
    def test_development_mode_keeps_plaintext_and_still_works(self):
        b = database.create_business(OWNER, "Салон", TOKEN_A)
        stored, stored_hash = self.raw(b["id"])
        self.assertEqual(stored, TOKEN_A)
        self.assertEqual(stored_hash, token_crypto.token_hash(TOKEN_A))
        self.assertEqual(database.get_business(b["id"])["bot_token"], TOKEN_A)
        self.assertEqual(database.get_business_by_bot_token(TOKEN_A)["id"], b["id"])
        self.assertNotIn("bot_token_hash", database.get_business(b["id"]))

    def test_init_db_without_key_does_not_touch_tokens(self):
        b = database.create_business(OWNER, "Салон", TOKEN_A)
        database.init_db()
        self.assertEqual(self.raw(b["id"])[0], TOKEN_A)

    def test_duplicate_token_is_refused(self):
        database.create_business(OWNER, "Один", TOKEN_A)
        with self.assertRaises(sqlite3.IntegrityError):
            database.create_business(OWNER, "Два", TOKEN_A)


class WithKeyTest(CryptoBase):
    def setUp(self):
        super().setUp()
        self.use_key()

    def test_token_is_encrypted_at_rest_and_plain_for_the_app(self):
        b = database.create_business(OWNER, "Салон", TOKEN_A)
        stored, _ = self.raw(b["id"])
        self.assertTrue(stored.startswith(token_crypto.PREFIX))
        self.assertNotIn(TOKEN_A, stored)
        self.assertNotIn(TOKEN_A.split(":")[1], stored)
        self.assertEqual(b["bot_token"], TOKEN_A)  # create_business возвращает рабочий токен
        self.assertEqual(database.get_business(b["id"])["bot_token"], TOKEN_A)

    def test_every_getter_returns_the_plain_token(self):
        b = database.create_business(OWNER, "Салон", TOKEN_A, trial_days=3)
        self.assertEqual([x["bot_token"] for x in database.get_all_businesses()], [TOKEN_A])
        self.assertEqual([x["bot_token"] for x in database.get_businesses_by_owner(OWNER)], [TOKEN_A])
        self.assertEqual([x["bot_token"] for x in database.get_subscription_reminder_candidates()], [TOKEN_A])
        self.assertEqual(database.get_business_by_bot_token(TOKEN_A)["id"], b["id"])
        for x in database.get_all_businesses():
            self.assertNotIn("bot_token_hash", x)

    def test_lookup_and_uniqueness_work_through_the_fingerprint(self):
        b = database.create_business(OWNER, "Один", TOKEN_A)
        self.assertEqual(database.get_business_by_bot_token(TOKEN_A)["id"], b["id"])
        self.assertIsNone(database.get_business_by_bot_token(TOKEN_B))
        with self.assertRaises(sqlite3.IntegrityError):
            database.create_business(OWNER, "Два", TOKEN_A)

    def test_same_token_encrypts_differently_each_time_but_fingerprint_is_stable(self):
        self.assertNotEqual(token_crypto.encrypt(TOKEN_A), token_crypto.encrypt(TOKEN_A))
        self.assertEqual(token_crypto.token_hash(TOKEN_A), token_crypto.token_hash(TOKEN_A))

    def test_fingerprint_is_keyed_not_a_plain_hash_of_the_token(self):
        plain_sha = hashlib.sha256(TOKEN_A.encode()).hexdigest()
        self.assertNotEqual(token_crypto.token_hash(TOKEN_A), plain_sha)
        first = token_crypto.token_hash(TOKEN_A)
        self.use_key(OTHER_KEY)
        self.assertNotEqual(token_crypto.token_hash(TOKEN_A), first)

    def test_webhook_secret_does_not_depend_on_the_database_fingerprint(self):
        """Секрет вебхука не меняется, иначе пришлось бы перерегистрировать вебхуки всех ботов."""
        self.assertEqual(webhooks.webhook_secret_for(TOKEN_A), hashlib.sha256(TOKEN_A.encode()).hexdigest()[:32])


class MigrationTest(CryptoBase):
    def test_existing_plaintext_tokens_get_encrypted_once(self):
        a = database.create_business(OWNER, "Один", TOKEN_A)
        b = database.create_business(OWNER, "Два", TOKEN_B)
        self.assertEqual(self.raw(a["id"])[0], TOKEN_A)  # пока ключа нет — открытым текстом
        self.use_key()
        database.init_db()  # то, что происходит при перезапуске сервиса после установки ключа
        for business, token in ((a, TOKEN_A), (b, TOKEN_B)):
            stored, stored_hash = self.raw(business["id"])
            self.assertTrue(stored.startswith(token_crypto.PREFIX))
            self.assertEqual(stored_hash, token_crypto.token_hash(token))
            self.assertEqual(database.get_business(business["id"])["bot_token"], token)
        # повторный запуск ничего не перешифровывает
        before = [self.raw(a["id"]), self.raw(b["id"])]
        database.init_db()
        self.assertEqual([self.raw(a["id"]), self.raw(b["id"])], before)

    def test_fingerprint_is_refreshed_when_the_key_appears(self):
        b = database.create_business(OWNER, "Один", TOKEN_A)
        dev_hash = self.raw(b["id"])[1]
        self.use_key()
        database.init_db()
        self.assertNotEqual(self.raw(b["id"])[1], dev_hash)
        self.assertEqual(database.get_business_by_bot_token(TOKEN_A)["id"], b["id"])


class FailureModesTest(CryptoBase):
    def make_encrypted(self):
        self.use_key()
        b = database.create_business(OWNER, "Салон", TOKEN_A)
        return b

    def test_encrypted_rows_without_a_key_fail_loudly(self):
        b = self.make_encrypted()
        os.environ.pop(token_crypto.KEY_ENV)
        with self.assertRaises(token_crypto.TokenCryptoError):
            database.get_business(b["id"])
        with self.assertRaises(token_crypto.TokenCryptoError):
            database.init_db()  # сервис не запустится, а не станет работать с мусором вместо токенов

    def test_wrong_key_fails_loudly(self):
        b = self.make_encrypted()
        self.use_key(OTHER_KEY)
        with self.assertRaises(token_crypto.TokenCryptoError):
            database.get_business(b["id"])

    def test_tampered_ciphertext_is_detected(self):
        b = self.make_encrypted()
        stored, _ = self.raw(b["id"])
        tampered = stored[:-4] + ("AAAA" if not stored.endswith("AAAA") else "BBBB")
        conn = sqlite3.connect(database.DB_PATH)
        conn.execute("UPDATE businesses SET bot_token = ? WHERE id = ?", (tampered, b["id"]))
        conn.commit()
        conn.close()
        with self.assertRaises(token_crypto.TokenCryptoError):
            database.get_business(b["id"])

    def test_invalid_key_is_reported_clearly(self):
        os.environ[token_crypto.KEY_ENV] = "not-a-key"
        with self.assertRaises(token_crypto.TokenCryptoError):
            token_crypto.encrypt(TOKEN_A)

    def test_generated_key_is_valid(self):
        key = token_crypto.generate_key()
        self.assertTrue(token_crypto.looks_like_key(key))
        self.assertFalse(token_crypto.looks_like_key("not-a-key"))
        self.use_key(key)
        self.assertEqual(token_crypto.decrypt(token_crypto.encrypt(TOKEN_A)), TOKEN_A)


class EndToEndTest(CryptoBase):
    """Важные пути с зашифрованной базой: подпись Telegram проверяется настоящим (расшифрованным) токеном."""

    def setUp(self):
        super().setUp()
        self.use_key()
        self.platform = database.create_business(900, "TeleSlot", TOKEN_A)
        self.client = database.create_business(OWNER, "Салон", TOKEN_B)
        self._platform_id = server.PLATFORM_BUSINESS_ID
        server.PLATFORM_BUSINESS_ID = self.platform["id"]

    def tearDown(self):
        server.PLATFORM_BUSINESS_ID = self._platform_id
        super().tearDown()

    def test_owner_signature_by_the_business_bot_is_verified_with_encrypted_storage(self):
        init = sign_init_data(OWNER, TOKEN_B)
        self.assertIn("weekly", server.admin_get_schedule(self.client["id"], init_data=init))

    def test_cabinet_signature_by_the_platform_bot_is_verified_with_encrypted_storage(self):
        init = sign_init_data(OWNER, TOKEN_A)
        self.assertEqual(server.cabinet_me(init_data=init)["user_id"], OWNER)

    def test_signature_made_with_the_ciphertext_is_useless(self):
        stored, _ = self.raw(self.client["id"])
        with self.assertRaises(Exception):
            server.admin_get_schedule(self.client["id"], init_data=sign_init_data(OWNER, stored))


class LogRedactionTest(unittest.TestCase):
    def logged(self, path):
        """Прогоняет запись через настоящий журнал uvicorn.access так же, как это делает uvicorn."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("uvicorn.access")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 200)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        return stream.getvalue()

    def test_filter_is_installed_on_the_access_logger(self):
        self.assertTrue(any(isinstance(f, server.RedactInitDataFilter)
                            for f in logging.getLogger("uvicorn.access").filters))

    def test_init_data_value_is_removed_from_the_log_line(self):
        secret = "query_id%3DAAH%26user%3D%257B%2522id%2522%253A4242%257D%26hash%3Dabcdef123456"
        line = self.logged(f"/api/admin/orders?business_id=2&init_data={secret}&owner_tg_id=42")
        self.assertNotIn("abcdef123456", line)
        self.assertNotIn("query_id", line)
        self.assertIn("init_data=[скрыто]", line)

    def test_other_parameters_stay_for_diagnostics(self):
        line = self.logged("/api/admin/orders?business_id=2&init_data=SECRET&status=new")
        self.assertIn("business_id=2", line)
        self.assertIn("status=new", line)
        self.assertNotIn("SECRET", line)

    def test_init_data_in_the_middle_and_at_the_end(self):
        for path in ("/x?init_data=SECRET", "/x?a=1&init_data=SECRET&b=2", "/x?a=1&init_data=SECRET"):
            self.assertNotIn("SECRET", self.logged(path), path)

    def test_lines_without_init_data_are_untouched(self):
        line = self.logged("/api/services?business_id=2")
        self.assertIn("/api/services?business_id=2", line)
        self.assertNotIn("[скрыто]", line)


if __name__ == "__main__":
    unittest.main()
