#!/usr/bin/env python3
"""Мониторинг доступности Mini App из России.

Зачем: провайдеры в РФ режут TCP до отдельных подсетей хостинга (так было с нашим IP), и с самого
сервера это не видно — снаружи из-за границы всё открывается. Раз в 10 минут спрашиваем публичный
сервис check-host.net, открывается ли порт 443 нашего домена с его российских нод, и при сбое пишем
владельцу в Telegram (тем же ботом №1, что и обычные уведомления).

Как работает решение: сбой = минимум 2 из 3 российских нод не смогли подключиться, и локально
приложение при этом живо. Два сбоя подряд -> одно сообщение; когда снова всё открывается -> сообщение
«восстановилось». Если сам check-host недоступен — состояние не меняем (нет данных != авария).

Только стандартная библиотека. Запуск: systemd timer (см. ops/README.md), пользователь booking.
Ключи из .env: BOT_TOKEN, OWNER_TG_ID, WEBAPP_URL.

  python monitor_reachability.py            обычный запуск (по таймеру)
  python monitor_reachability.py --dry-run  всё проверить и напечатать, ничего не слать и не сохранять
  python monitor_reachability.py --test-alert  отправить тестовое сообщение владельцу
"""
import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP_DIR = os.environ.get("BOOKING_APP_DIR", "/opt/tg-booking-bot")
STATE_PATH = os.environ.get("MONITOR_STATE", "/var/lib/booking-monitor/state.json")
NODES = ["ru1.node.check-host.net", "ru2.node.check-host.net", "ru3.node.check-host.net"]
MIN_FAILED_NODES = 2   # сколько нод должно не достучаться, чтобы считать это сбоем
BAD_RUNS_TO_ALERT = 2  # сколько запусков подряд (по ~10 мин) — сбой, прежде чем писать
LOCAL_URL = "http://127.0.0.1:8000/"


def load_env(path):
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def http_json(url, headers=None, data=None, timeout=20):
    # Свой User-Agent: Cloudflare перед check-host режет стандартный "Python-urllib".
    headers = {"User-Agent": "booking-monitor/1.0", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_local():
    try:
        with urllib.request.urlopen(LOCAL_URL, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_from_russia(host):
    """Возвращает {нода: True|False|None}: True — подключилась, False — нет, None — нет ответа."""
    headers = {"Accept": "application/json"}
    query = urllib.parse.urlencode([("host", f"{host}:443")] + [("node", n) for n in NODES])
    start = http_json(f"https://check-host.net/check-tcp?{query}", headers)
    request_id = start["request_id"]
    result = {}
    for _ in range(15):  # до ~75 с: обычно 15-25 с, но check-host иногда считает дольше
        time.sleep(5)
        raw = http_json(f"https://check-host.net/check-result/{request_id}", headers)
        result = {n: parse_node_result(raw.get(n)) for n in NODES}
        if all(v is not None for v in result.values()):
            break
    return result


def parse_node_result(value):
    """check-tcp: null — ещё считает; [{"address":..,"time":..}] — успех; [{"error":..}] — неудача."""
    if not value:
        return None
    first = value[0]
    if isinstance(first, dict):
        if "time" in first:
            return True
        if "error" in first:
            return False
    return None


def evaluate(nodes, local_ok, state):
    """Чистая функция: по результатам проверки и прошлому состоянию решает, что делать.
    Возвращает (новое_состояние, сообщение_или_None)."""
    state = dict(state)
    answered = {n: v for n, v in nodes.items() if v is not None}
    if len(answered) < 2:
        return state, None  # мало данных — не трогаем состояние

    failed = [n for n, ok in answered.items() if not ok]
    is_bad = len(failed) >= MIN_FAILED_NODES
    was_alerted = bool(state.get("alerted"))

    if is_bad:
        state["bad_runs"] = state.get("bad_runs", 0) + 1
        if state["bad_runs"] >= BAD_RUNS_TO_ALERT and not was_alerted:
            state["alerted"] = True
            names = ", ".join(n.split(".")[0] for n in failed)
            if not local_ok:
                text = ("Сервер: приложение не отвечает на самом сервере (проверьте `systemctl status tg-booking`). "
                        f"Не открывается с нод: {names}.")
            else:
                text = (f"Сайт не открывается из России (ноды: {names}), хотя на сервере приложение работает. "
                        "Похоже на блокировку IP на пути. Что делать: получить новый IP в панели Beget, "
                        "проверить его с ПК без VPN и переключить A-запись teleslotapp.com.")
            return state, "Внимание: " + text
        return state, None

    state["bad_runs"] = 0
    if was_alerted:
        state["alerted"] = False
        return state, "Доступность восстановилась: сайт снова открывается с российских нод."
    return state, None


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    for attempt in range(1, 4):
        try:
            http_json(url, data=data, timeout=15)
            return True
        except Exception as exc:
            logging.warning("отправка в Telegram, попытка %s: %s", attempt, type(exc).__name__)
            time.sleep(2 * attempt)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-alert", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    env = load_env(os.path.join(APP_DIR, ".env"))
    token, owner = env.get("BOT_TOKEN"), env.get("OWNER_TG_ID")
    host = urllib.parse.urlparse(env.get("WEBAPP_URL", "")).hostname
    if not (token and owner and host):
        logging.error("в .env нет BOT_TOKEN / OWNER_TG_ID / WEBAPP_URL")
        return 2

    if args.test_alert:
        ok = send_telegram(token, owner, f"Проверка мониторинга: сообщения приходят. Следим за {host}.")
        print("отправлено" if ok else "НЕ отправлено")
        return 0 if ok else 1

    local_ok = check_local()
    try:
        nodes = check_from_russia(host)
    except Exception as exc:
        detail = getattr(exc, "code", "") or type(exc).__name__
        logging.warning("check-host недоступен (%s) — пропускаю запуск", detail)
        return 0

    state = load_state()
    new_state, message = evaluate(nodes, local_ok, state)
    logging.info("host=%s local=%s nodes=%s bad_runs=%s alerted=%s",
                 host, local_ok, {k.split('.')[0]: v for k, v in nodes.items()},
                 new_state.get("bad_runs"), new_state.get("alerted"))
    if args.dry_run:
        print("сообщение:", message)
        return 0
    if message and not send_telegram(token, owner, message):
        # не сохраняем новое состояние: следующий запуск повторит отправку
        logging.error("не удалось отправить оповещение")
        return 1
    save_state(new_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
