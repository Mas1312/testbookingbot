#!/bin/bash
# Первичная настройка нового сервера (после переезда): спрашивает токен платформенного бота, пишет .env, создаёт ключ
# шифрования токенов (TOKEN_ENCRYPTION_KEY) ДО первого запуска, запускает сервис. Запускать ВРУЧНУЮ от root, с терминалом:
#   ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/env-setup.sh"
#
# Токен вводится здесь и не отображается: в чат и в git он не попадает. Ключ показывается ОДИН РАЗ в конце —
# сохраните его вне сервера (менеджер паролей). Без ключа базу из копии не прочитать, а токены не восстановить.
# Повторный запуск откажется работать, если .env уже есть (чтобы не затереть ключ).
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }
[ -t 0 ] || { echo "Нужен терминал: ssh -t ..."; exit 1; }

APP=/opt/tg-booking-bot
ENV="$APP/.env"
[ -f "$ENV" ] && { echo "$ENV уже есть. Ничего не меняю."; exit 1; }

read -r -p "Telegram id владельца платформы: " OWNER
[[ "$OWNER" =~ ^[0-9]+$ ]] || { echo "Нужно число"; exit 1; }
read -r -s -p "Токен платформенного бота (@teleslotapp_bot) из BotFather: " TOKEN
echo
[[ "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || { echo "Токен не похож на токен бота. Ничего не изменено."; exit 1; }
read -r -p "Адрес сервиса [https://app.217-60-245-26.sslip.io]: " URL
URL=${URL:-https://app.217-60-245-26.sslip.io}

KEY=$("$APP/venv/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

umask 077
cat > "$ENV" <<EOF
BOT_TOKEN=$TOKEN
WEBAPP_URL=$URL
USE_WEBHOOK=true
OWNER_TG_ID=$OWNER
BUSINESS_NAME=TeleSlot
PLATFORM_BUSINESS_ID=1
TOKEN_ENCRYPTION_KEY=$KEY
EOF
chown booking:booking "$ENV"
chmod 600 "$ENV"
unset TOKEN

systemctl restart caddy
systemctl restart tg-booking
sleep 15
if [ "$(systemctl is-active tg-booking)" != active ]; then
  echo "ВНИМАНИЕ: сервис не запустился: journalctl -u tg-booking -n 50"
  exit 1
fi
echo "Сервис запущен."
curl -s -o /dev/null -w "локально /api/config: %{http_code}\n" "http://127.0.0.1:8000/api/config?business_id=1"

echo
echo "================================================================"
echo "КЛЮЧ ШИФРОВАНИЯ ТОКЕНОВ (показывается ОДИН РАЗ):"
echo
echo "$KEY"
echo
echo "Сохраните его СЕЙЧАС в менеджере паролей, отдельно от сервера."
echo "================================================================"
