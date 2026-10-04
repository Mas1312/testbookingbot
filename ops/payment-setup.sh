#!/bin/bash
# Подключение платёжного токена (Telegram Payments + ЮKassa) к серверу. Запускать ВРУЧНУЮ от root, с терминалом:
#   ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/payment-setup.sh"
# Токен берётся в @BotFather: бот TeleSlot -> Bot Settings -> Payments -> ЮKassa (сначала «тест», потом «платежи»).
# Токен вводится здесь, не отображается и попадает только в /opt/tg-booking-bot/.env (chmod 600), не в чат и не в git.
# Повторный запуск заменяет токен (например, когда переходите с тестового на боевой). Пустой ввод — отключить оплату.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }
[ -t 0 ] || { echo "Нужен терминал: ssh -t ..."; exit 1; }

ENV=/opt/tg-booking-bot/.env
[ -f "$ENV" ] || { echo "Не найден $ENV"; exit 1; }

read -r -s -p "Платёжный токен от BotFather (пусто = отключить оплату): " TOKEN
echo
if [ -n "$TOKEN" ] && ! [[ "$TOKEN" =~ ^[0-9]+:(TEST|LIVE):[A-Za-z0-9_-]+$ ]]; then
  echo "Токен не похож на платёжный (ожидается вид 123456789:TEST:... или 123456789:LIVE:...). Ничего не изменено."
  exit 1
fi

cp "$ENV" "$ENV.bak-payment-$(date +%Y%m%d%H%M)"
umask 077
grep -v '^PAYMENT_PROVIDER_TOKEN=' "$ENV" > "$ENV.tmp" || true
printf 'PAYMENT_PROVIDER_TOKEN=%s\n' "$TOKEN" >> "$ENV.tmp"
chown --reference="$ENV" "$ENV.tmp"
chmod 600 "$ENV.tmp"
mv "$ENV.tmp" "$ENV"
unset TOKEN

systemctl restart tg-booking
sleep 8
if [ "$(systemctl is-active tg-booking)" = active ]; then
  echo "Готово: сервис перезапущен."
  if grep -q '^PAYMENT_PROVIDER_TOKEN=.\+' "$ENV"; then
    MODE=$(grep -o '^PAYMENT_PROVIDER_TOKEN=[0-9]*:\(TEST\|LIVE\)' "$ENV" | sed 's/.*://')
    echo "Оплата ВКЛЮЧЕНА, режим: $MODE"
  else
    echo "Оплата ОТКЛЮЧЕНА (кнопка «Оформить подписку» ведёт в поддержку)."
  fi
else
  echo "ВНИМАНИЕ: сервис не запустился. Откат: cp $ENV.bak-payment-* $ENV (последняя копия) и systemctl restart tg-booking"
  exit 1
fi
