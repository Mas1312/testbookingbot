#!/bin/bash
# Включает шифрование токенов ботов в базе: создаёт ключ TOKEN_ENCRYPTION_KEY в .env и перезапускает сервис —
# при старте все открытые токены в базе зашифровываются (см. backend/token_crypto.py).
# Запускать ВРУЧНУЮ от root, с терминалом:
#   ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/token-key-setup.sh"
#
# Ключ показывается ОДИН РАЗ в конце — сохраните его вне сервера (менеджер паролей), как фразу шифрования бэкапов.
# Без ключа зашифрованные токены не восстановить: владельцам пришлось бы заново подключать своих ботов.
# Повторный запуск ничего не меняет, если ключ уже задан (менять ключ нельзя: старые токены перестанут читаться).
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }
[ -t 0 ] || { echo "Нужен терминал: ssh -t ..."; exit 1; }

APP=/opt/tg-booking-bot
ENV="$APP/.env"
[ -f "$ENV" ] || { echo "Не найден $ENV"; exit 1; }

if grep -q '^TOKEN_ENCRYPTION_KEY=.\+' "$ENV"; then
  echo "Ключ шифрования уже задан, ничего не меняю."
  exit 0
fi

echo "1/5 Устанавливаю зависимости..."
sudo -u booking "$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "2/5 Копия базы перед шифрованием..."
STAMP=$(date -u +%Y%m%d%H%M)
python3 - "$STAMP" <<'PY'
import sqlite3, sys
src = sqlite3.connect("/opt/tg-booking-bot/booking.db")
dst = sqlite3.connect("/var/backups/booking/pre-encrypt-%s.db" % sys.argv[1])
src.backup(dst)
dst.close(); src.close()
print("   сохранено: /var/backups/booking/pre-encrypt-%s.db" % sys.argv[1])
PY

echo "3/5 Создаю ключ..."
KEY=$("$APP/venv/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
cp "$ENV" "$ENV.bak-key-$STAMP"
[ -n "$(tail -c1 "$ENV")" ] && echo >> "$ENV"   # если в конце файла нет перевода строки, не склеиваем строки
umask 077
printf 'TOKEN_ENCRYPTION_KEY=%s\n' "$KEY" >> "$ENV"
chown --reference="$ENV.bak-key-$STAMP" "$ENV"
chmod 600 "$ENV"

echo "4/5 Перезапускаю сервис (токены зашифруются при старте)..."
systemctl restart tg-booking
sleep 12
if [ "$(systemctl is-active tg-booking)" != active ]; then
  echo
  echo "ВНИМАНИЕ: сервис не запустился. Откат:"
  echo "  systemctl stop tg-booking"
  echo "  cp /var/backups/booking/pre-encrypt-$STAMP.db $APP/booking.db && chown booking:booking $APP/booking.db"
  echo "  cp $ENV.bak-key-$STAMP $ENV"
  echo "  systemctl start tg-booking"
  exit 1
fi

echo "5/5 Проверяю базу..."
sudo -u booking "$APP/venv/bin/python" - <<'PY'
import sqlite3
db = sqlite3.connect("/opt/tg-booking-bot/booking.db")
total = db.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
enc = db.execute("SELECT COUNT(*) FROM businesses WHERE bot_token LIKE 'enc:v1:%'").fetchone()[0]
print("   токенов в базе: %d, из них зашифровано: %d" % (total, enc))
print("   ОК" if total == enc else "   ВНИМАНИЕ: не все токены зашифрованы, посмотрите журнал: journalctl -u tg-booking -n 50")
PY

echo
echo "================================================================"
echo "КЛЮЧ ШИФРОВАНИЯ ТОКЕНОВ (показывается ОДИН РАЗ):"
echo
echo "$KEY"
echo
echo "Сохраните его СЕЙЧАС в менеджере паролей, отдельно от сервера."
echo "Без него базу из резервной копии не прочитать."
echo "================================================================"
