#!/bin/bash
# Закрывает вход на сервер по паролю: остаётся только вход по SSH-ключу (root по паролю больше войти не сможет).
# Зачем: порт 22 открыт всему интернету и его постоянно подбирают (тысячи попыток), а пароль root когда-то
# попал в чат; с отключённым паролем подобрать или украсть его бессмысленно.
#
# Запускать ВРУЧНУЮ от root. Перед этим убедитесь, что вы заходите по ключу (без ввода пароля):
#   ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/ssh-harden.sh"
# Текущая сессия не прерывается; после скрипта проверьте, что второе окно/новое подключение по ключу работает.
#
# Откат (если что-то пошло не так, например через консоль Beget):
#   rm /etc/ssh/sshd_config.d/00-hardening.conf && systemctl reload ssh
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запускайте от root"; exit 1; }

KEYS=/root/.ssh/authorized_keys
# Защита от самоблокировки: без единого ключа пароль отключать нельзя.
if ! grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-|sk-)' "$KEYS" 2>/dev/null; then
  echo "В $KEYS нет ни одного SSH-ключа: отключать вход по паролю нельзя, вы потеряли бы доступ. Отмена."
  exit 1
fi

CONF=/etc/ssh/sshd_config.d/00-hardening.conf
# 00- — чтобы файл читался первым: в sshd побеждает первое найденное значение (cloud-init пишет свои 50-/60-).
cat > "$CONF" <<'EOF'
# Создано ops/ssh-harden.sh: вход только по ключу.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF

if ! sshd -t; then
  echo "Конфигурация sshd некорректна, откатываю."
  rm -f "$CONF"
  exit 1
fi

systemctl reload ssh 2>/dev/null || systemctl restart ssh

echo "Действующие настройки sshd:"
sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|pubkeyauthentication) '
echo
echo "Готово. НЕ закрывайте это окно, пока не проверите вход по ключу из нового окна."
