"""ops/backup_offsite: снимок БД, сжатие, шифрование и расшифровка (без сети и S3)."""
import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))

import backup_offsite as b  # noqa: E402


@unittest.skipUnless(shutil.which("openssl"), "openssl не найден в PATH")
class BackupRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.db = os.path.join(self.dir, "src.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [("запись %d" % i,) for i in range(200)])
        conn.commit()
        conn.close()
        self.passfile = os.path.join(self.dir, "pass")
        with open(self.passfile, "w") as f:
            f.write("test-passphrase\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_compress_encrypt_decrypt_roundtrip(self):
        snap, gz, enc, back = (os.path.join(self.dir, n) for n in ("snap.db", "s.gz", "s.enc", "back.gz"))
        b.snapshot_db(self.db, snap)
        b.gzip_file(snap, gz)
        b.encrypt_file(gz, enc, self.passfile)
        with open(enc, "rb") as f:
            self.assertNotEqual(f.read(3), b"\x1f\x8b\x08", "зашифрованный файл не должен быть gzip")
        b.decrypt_file(enc, back, self.passfile)
        self.assertEqual(b.sha256(gz), b.sha256(back))
        restored = os.path.join(self.dir, "restored.db")
        with gzip.open(back, "rb") as f_in, open(restored, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        conn = sqlite3.connect(restored)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 200)
        conn.close()

    def test_wrong_passphrase_fails(self):
        gz, enc, back = (os.path.join(self.dir, n) for n in ("s.gz", "s.enc", "back.gz"))
        b.gzip_file(self.db, gz)
        b.encrypt_file(gz, enc, self.passfile)
        wrong = os.path.join(self.dir, "wrong")
        with open(wrong, "w") as f:
            f.write("другая-фраза\n")
        try:
            b.decrypt_file(enc, back, wrong)
        except subprocess.CalledProcessError:
            return  # обычный исход: openssl отвергает неверную фразу
        # редкий случай (~1/256): проверка заполнения прошла случайно — но содержимое всё равно другое
        self.assertNotEqual(b.sha256(gz), b.sha256(back))

    def test_dry_run_end_to_end(self):
        conf = os.path.join(self.dir, "conf")
        os.makedirs(conf)
        shutil.copy(self.passfile, os.path.join(conf, "passphrase"))
        b.CONF_DIR, b.DB_PATH = conf, self.db
        name = b.run_backup(dry_run=True)
        self.assertTrue(name.endswith(".db.gz.enc"))


if __name__ == "__main__":
    unittest.main()
