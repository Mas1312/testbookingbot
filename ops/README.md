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
