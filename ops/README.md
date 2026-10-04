# ops — служебные скрипты сервера

## Мониторинг доступности (`monitor_reachability.py`)
Раз в 10 минут спрашивает check-host.net, открывается ли порт 443 домена с трёх российских нод
(Москва x2, СПб), и при сбое пишет владельцу (`OWNER_TG_ID`) в Telegram от бота №1.
Сбой = минимум 2 из 3 нод не подключились, дважды подряд. Когда снова открывается — сообщение «восстановилось».
Если check-host сам недоступен, состояние не меняется.

Ограничение: ноды check-host — дата-центры, а не домашние провайдеры. Они поймали нашу блокировку (старый IP
резался из Москвы и Казахстана), но это не гарантия для каждой сети.

Установка на сервере (от root):

    cp /opt/tg-booking-bot/ops/booking-monitor.{service,timer} /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now booking-monitor.timer

Проверка: `systemctl list-timers booking-monitor.timer`, `journalctl -u booking-monitor -n 20`.
Ручной пробный запуск без сообщений: `sudo -u booking python3 ops/monitor_reachability.py --dry-run`.
Тестовое сообщение владельцу: `--test-alert`.

## Оплата подписки (`payment-setup.sh`)
Оплата идёт внутри `@teleslotapp_bot` через Telegram Payments + ЮKassa (`backend/billing.py`). Нужен платёжный токен:
в @BotFather -> бот TeleSlot -> Bot Settings -> Payments -> ЮKassa («тест» для проверки, потом «платежи»).

    ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/payment-setup.sh"

Скрипт спрашивает токен (не отображается), пишет `PAYMENT_PROVIDER_TOKEN` в `.env` и перезапускает сервис; тот же
скрипт переключает тест на боевой токен или отключает оплату (пустой ввод). Цена и срок — `SUBSCRIPTION_PRICE_RUB`
(по умолчанию 990) и `SUBSCRIPTION_DAYS` (30) в `.env`. После перезапуска вебхуки перерегистрируются сами
(в `allowed_updates` есть `pre_checkout_query`). Оплаты — таблица `payments`, срок — `businesses.paid_until`.
Блокировки за неоплату нет: только учёт и напоминания за 3 дня и после окончания.

## Шифрование токенов ботов (`token-key-setup.sh`)
Токены ботов в базе хранятся зашифрованными (`backend/token_crypto.py`), ключ — `TOKEN_ENCRYPTION_KEY` в `.env`.
Включение (один раз, вручную): `ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/token-key-setup.sh"` — скрипт ставит
зависимость, делает копию базы, создаёт ключ, перезапускает сервис (токены зашифровываются при старте) и показывает ключ
ОДИН РАЗ: его нужно сохранить вне сервера. Без ключа базу из резервной копии прочитать нельзя, а токены не восстановить.
Защищает от утечки файла базы или копии из хранилища, но не от взлома самого сервера (там есть и база, и ключ).
Ad-hoc скрипты, читающие токены, должны ходить через `database.get_business()`, а не `SELECT bot_token` напрямую.
В `.env` по-прежнему лежит `BOT_TOKEN` прод-бота (нужен мониторингу), права файла 600.

## Вход по паролю (`ssh-harden.sh`)
Отключает вход на сервер по паролю (остаётся только SSH-ключ) и запрещает root вход по паролю. Скрипт не запускается,
если в `authorized_keys` нет ключей, проверяет `sshd -t`, перезагружает sshd без обрыва текущей сессии. После запуска
проверьте вход по ключу из нового окна. Откат: `rm /etc/ssh/sshd_config.d/00-hardening.conf && systemctl reload ssh`
(при потере доступа — через консоль Beget).

## Внешний бэкап (`backup_offsite.py`)
Каждую ночь (03:40) снимок БД -> gzip -> шифрование (openssl AES-256, PBKDF2) -> S3-хранилище Beget (бакет в СПб),
затем контрольное восстановление: файл скачивается обратно и сверяется sha256. Копии старше 30 дней удаляются.
При любой ошибке владельцу приходит сообщение в Telegram.

Разовая настройка (вручную, ключи вводятся в терминале и в чат/репозиторий не попадают):

    ssh -t root@<сервер> "bash /opt/tg-booking-bot/ops/backup-setup.sh"
    cp /opt/tg-booking-bot/ops/booking-backup.{service,timer} /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now booking-backup.timer

Скрипт настройки покажет ОДИН РАЗ фразу шифрования — её обязательно сохранить вне сервера, без неё копии не открыть.
Ключи S3 лежат только в `/etc/booking-backup/rclone.conf` (доступ только у пользователя booking).

Пробный прогон без загрузки: `sudo -u booking python3 ops/backup_offsite.py --dry-run`.
Восстановление: `sudo -u booking python3 ops/backup_offsite.py --restore booking-<дата>.db.gz.enc /tmp/restored.db`,
затем остановить сервис, подложить файл как `booking.db` (владелец booking, права 600), запустить сервис.
Ограничение: хранилище и сервер у одного провайдера (Beget); раз в месяц-два стоит скачать копию себе.
