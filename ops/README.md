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
