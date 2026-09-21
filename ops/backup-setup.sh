#!/bin/bash
# Разовая настройка внешнего бэкапа. Запускать ВРУЧНУЮ на сервере от root, в терминале с tty:
#   ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/backup-setup.sh"
# Ключи доступа вводятся здесь, в терминале (не отображаются) и попадают только в /etc/booking-backup.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }
[ -t 0 ] || { echo "Нужен терминал: ssh -t ..."; exit 1; }

CONF=/etc/booking-backup
ENDPOINT="https://s3.ru1.storage.beget.cloud"
REGION="ru1"
DEFAULT_BUCKET="9ac3aa6fd28f-booking-backups"

apt-get install -y rclone >/dev/null
install -d -m 700 -o booking -g booking "$CONF"

read -r -p "Имя бакета [$DEFAULT_BUCKET]: " BUCKET
BUCKET=${BUCKET:-$DEFAULT_BUCKET}
read -r -p "Access key: " ACCESS_KEY
read -r -s -p "Secret key (при вводе не отображается): " SECRET_KEY
echo

umask 077
cat > "$CONF/rclone.conf" <<EOF
[s3]
type = s3
provider = Other
access_key_id = $ACCESS_KEY
secret_access_key = $SECRET_KEY
endpoint = $ENDPOINT
region = $REGION
force_path_style = true
EOF
printf 'BACKUP_BUCKET=%s\nRETENTION_DAYS=30\n' "$BUCKET" > "$CONF/backup.conf"

NEW_PASS=0
if [ ! -s "$CONF/passphrase" ]; then
  head -c 32 /dev/urandom | base64 > "$CONF/passphrase"
  NEW_PASS=1
fi
chown booking:booking "$CONF"/*
chmod 600 "$CONF"/*
unset ACCESS_KEY SECRET_KEY

# Закрываем локальные копии и саму БД от чтения другими пользователями сервера (в них телефоны и токены ботов)
chmod 700 /var/backups/booking
chmod 600 /opt/tg-booking-bot/booking.db || true

echo
echo "Проверка связи с хранилищем:"
if sudo -u booking rclone --config "$CONF/rclone.conf" lsf "s3:$BUCKET" >/dev/null 2>&1; then
  echo "бакет $BUCKET доступен: OK"
else
  echo "ОШИБКА: не удалось открыть бакет $BUCKET. Проверьте ключи (запустите скрипт заново) и имя бакета."
fi

if [ "$NEW_PASS" = 1 ]; then
  echo
  echo "================================================================"
  echo "ФРАЗА ШИФРОВАНИЯ БЭКАПОВ (показывается ОДИН РАЗ):"
  echo
  cat "$CONF/passphrase"
  echo
  echo "Сохраните её СЕЙЧАС в менеджере паролей / на бумаге, отдельно от сервера."
  echo "Без неё бэкапы из хранилища расшифровать НЕЛЬЗЯ."
  echo "================================================================"
fi
