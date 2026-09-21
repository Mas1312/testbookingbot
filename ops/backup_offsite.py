#!/usr/bin/env python3
"""Ежедневный ЗАШИФРОВАННЫЙ бэкап базы в S3-хранилище (Beget Object Storage) — копия вне сервера.

Что делает:
  1. Снимок SQLite через backup API (консистентный, даже пока сервер пишет) + integrity_check.
  2. gzip -> шифрование openssl AES-256-CBC (PBKDF2) ключом-фразой из /etc/booking-backup/passphrase.
  3. Загрузка через rclone, сверка размера.
  4. КОНТРОЛЬНОЕ ВОССТАНОВЛЕНИЕ: скачивает загруженный файл обратно, расшифровывает и сверяет
     sha256 с исходным архивом — бэкап, который нельзя восстановить, не бэкап.
  5. Удаляет копии старше RETENTION_DAYS.
  Любая ошибка -> сообщение владельцу в Telegram и код выхода 1.

Файлы настроек (создаёт ops/backup-setup.sh, читает только пользователь booking):
  /etc/booking-backup/rclone.conf   ключи доступа к S3 (remote [s3])
  /etc/booking-backup/passphrase    фраза шифрования (ОБЯЗАТЕЛЬНО хранить копию вне сервера!)
  /etc/booking-backup/backup.conf   BACKUP_BUCKET=..., RETENTION_DAYS=...

Восстановление:  python3 backup_offsite.py --restore <имя_файла_в_бакете> <куда_положить.db>
Пробный локальный прогон без загрузки:  --dry-run
"""
import argparse
import datetime
import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monitor_reachability as mon  # noqa: E402  (load_env, send_telegram)

APP_DIR = os.environ.get("BOOKING_APP_DIR", "/opt/tg-booking-bot")
CONF_DIR = os.environ.get("BACKUP_CONF_DIR", "/etc/booking-backup")
DB_PATH = os.environ.get("BOOKING_DB", os.path.join(APP_DIR, "booking.db"))
REMOTE = "s3"
PBKDF2_ITER = "200000"


def snapshot_db(src, dst):
    """Копия БД через backup API + проверка целостности."""
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        target.close()
        source.close()
    if result != "ok":
        raise RuntimeError(f"integrity_check копии: {result}")


def gzip_file(src, dst):
    with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)


def _openssl(decrypt, src, dst, passfile):
    cmd = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", PBKDF2_ITER, "-salt",
           "-in", src, "-out", dst, "-pass", f"file:{passfile}"]
    if decrypt:
        cmd.insert(2, "-d")
    subprocess.run(cmd, check=True, capture_output=True)


def encrypt_file(src, dst, passfile):
    _openssl(False, src, dst, passfile)


def decrypt_file(src, dst, passfile):
    _openssl(True, src, dst, passfile)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rclone(*args, capture=False):
    cmd = ["rclone", "--config", os.path.join(CONF_DIR, "rclone.conf"), *args]
    return subprocess.run(cmd, check=True, capture_output=capture, text=True, timeout=600)


def run_backup(dry_run=False):
    conf = mon.load_env(os.path.join(CONF_DIR, "backup.conf"))
    bucket = conf.get("BACKUP_BUCKET")
    retention = int(conf.get("RETENTION_DAYS", "30"))
    passfile = os.path.join(CONF_DIR, "passphrase")
    if not dry_run and not bucket:
        raise RuntimeError("в backup.conf нет BACKUP_BUCKET")

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"booking-{stamp}.db.gz.enc"
    with tempfile.TemporaryDirectory(prefix="booking-backup-") as tmp:
        raw, gz, enc = (os.path.join(tmp, n) for n in ("snap.db", "snap.db.gz", name))
        snapshot_db(DB_PATH, raw)
        gzip_file(raw, gz)
        expected = sha256(gz)
        encrypt_file(gz, enc, passfile)
        size = os.path.getsize(enc)
        logging.info("снимок готов: %s, %d байт (зашифрован)", name, size)
        if dry_run:
            check = os.path.join(tmp, "check.gz")
            decrypt_file(enc, check, passfile)
            if sha256(check) != expected:
                raise RuntimeError("локальная проверка расшифровки не сошлась")
            logging.info("dry-run: снимок, сжатие, шифрование и расшифровка — ок; в S3 не загружали")
            return name

        remote_path = f"{REMOTE}:{bucket}/{name}"
        rclone("copyto", enc, remote_path)
        listing = rclone("lsjson", remote_path, capture=True).stdout
        if f'"Size":{size}' not in listing.replace(" ", ""):
            raise RuntimeError(f"размер в хранилище не совпал с {size}: {listing.strip()[:200]}")

        # контрольное восстановление
        back_enc, back_gz = os.path.join(tmp, "back.enc"), os.path.join(tmp, "back.gz")
        rclone("copyto", remote_path, back_enc)
        decrypt_file(back_enc, back_gz, passfile)
        if sha256(back_gz) != expected:
            raise RuntimeError("контрольное восстановление не сошлось (sha256)")
        logging.info("загружено и проверено восстановлением: %s", remote_path)

        rclone("delete", f"{REMOTE}:{bucket}", "--min-age", f"{retention}d", "--include", "booking-*.db.gz.enc")
        return name


def restore(name, out_path):
    conf = mon.load_env(os.path.join(CONF_DIR, "backup.conf"))
    passfile = os.path.join(CONF_DIR, "passphrase")
    with tempfile.TemporaryDirectory(prefix="booking-restore-") as tmp:
        enc, gz = os.path.join(tmp, "b.enc"), os.path.join(tmp, "b.gz")
        rclone("copyto", f"{REMOTE}:{conf['BACKUP_BUCKET']}/{name}", enc)
        decrypt_file(enc, gz, passfile)
        with gzip.open(gz, "rb") as f_in, open(out_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    check = sqlite3.connect(out_path)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    print(f"восстановлено в {out_path}, integrity_check = {result}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restore", nargs=2, metavar=("ИМЯ", "ФАЙЛ"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.restore:
        restore(*args.restore)
        return 0
    try:
        run_backup(dry_run=args.dry_run)
        return 0
    except Exception as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        logging.error("бэкап не удался: %s: %s", type(exc).__name__, str(detail)[:300])
        env = mon.load_env(os.path.join(APP_DIR, ".env"))
        if env.get("BOT_TOKEN") and env.get("OWNER_TG_ID") and not args.dry_run:
            mon.send_telegram(env["BOT_TOKEN"], env["OWNER_TG_ID"],
                              f"Внимание: ночной бэкап базы НЕ выполнен ({type(exc).__name__}). "
                              "Смотрите: journalctl -u booking-backup -n 30")
        return 1


if __name__ == "__main__":
    sys.exit(main())
