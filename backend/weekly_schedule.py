"""График работы по дням недели: у каждого дня свои часы, выходной и необязательный перерыв (обед).

Формат (хранится в businesses.weekly_schedule как JSON и так же ходит по API): список из 7 словарей,
индекс 0 — понедельник … 6 — воскресенье (как date.weekday()):

    {"open": true, "start": "09:00", "end": "18:00", "break_start": "13:00", "break_end": "14:00"}

break_* — оба или ни одного (None). Конец дня может быть "24:00". У выходного (open=false) часы запоминаются,
чтобы при повторном включении дня не вводить их заново, но на расчёт слотов не влияют.

Если у бизнеса график по дням не задавался (NULL), все 7 дней считаются рабочими с общими часами
work_start_hour–work_end_hour — ровно как было до появления этой возможности."""
import json
import re
from datetime import date as date_cls

DAYS = 7
DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
DEFAULT_START, DEFAULT_END = "09:00", "18:00"


def parse_time(value) -> int | None:
    """'HH:MM' -> минуты от начала суток (0..1440). 24:00 допустимо только как конец дня."""
    if not isinstance(value, str):
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    hours, minutes = int(m.group(1)), int(m.group(2))
    if minutes > 59 or hours > 24 or (hours == 24 and minutes != 0):
        return None
    return hours * 60 + minutes


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def uniform_week(start_hour: int, end_hour: int) -> list[dict]:
    """Все дни рабочие, одни и те же часы — это и есть график «как раньше»."""
    day = {"open": True, "start": format_time(start_hour * 60), "end": format_time(end_hour * 60),
           "break_start": None, "break_end": None}
    return [dict(day) for _ in range(DAYS)]


def validate_week(week) -> str | None:
    """None — график корректен; иначе текст ошибки для владельца."""
    if not isinstance(week, list) or len(week) != DAYS:
        return "График должен содержать все 7 дней недели"
    any_open = False
    for index, day in enumerate(week):
        name = DAY_NAMES[index]
        if not isinstance(day, dict):
            return f"{name}: неверные данные"
        if not day.get("open"):
            continue
        any_open = True
        start, end = parse_time(day.get("start")), parse_time(day.get("end"))
        if start is None or end is None or start >= 1440:
            return f"{name}: укажите время в формате ЧЧ:ММ"
        if end <= start:
            return f"{name}: время закрытия должно быть позже времени открытия"
        b_start, b_end = day.get("break_start"), day.get("break_end")
        if b_start or b_end:
            bs, be = parse_time(b_start), parse_time(b_end)
            if bs is None or be is None:
                return f"{name}: укажите начало и конец перерыва"
            if be <= bs:
                return f"{name}: перерыв должен заканчиваться позже, чем начинается"
            if bs < start or be > end:
                return f"{name}: перерыв должен быть внутри рабочего времени"
    if not any_open:
        return "Должен быть хотя бы один рабочий день"
    return None


def normalize_week(week) -> list[dict]:
    """Приводит уже проверенный график к каноническому виду (для хранения)."""
    result = []
    for day in week:
        is_open = bool(day.get("open"))
        start = parse_time(day.get("start"))
        end = parse_time(day.get("end"))
        if start is None or end is None or start >= end or start >= 1440:
            start, end = parse_time(DEFAULT_START), parse_time(DEFAULT_END)
        bs, be = parse_time(day.get("break_start")), parse_time(day.get("break_end"))
        has_break = is_open and bs is not None and be is not None and start <= bs < be <= end
        result.append({
            "open": is_open, "start": format_time(start), "end": format_time(end),
            "break_start": format_time(bs) if has_break else None,
            "break_end": format_time(be) if has_break else None,
        })
    return result


def load_week(raw: str | None, start_hour: int, end_hour: int) -> list[dict]:
    """График из БД; битый/пустой JSON не должен ронять расчёт слотов — откатываемся к общим часам."""
    if raw:
        try:
            week = json.loads(raw)
            if validate_week(week) is None:
                return normalize_week(week)
        except (ValueError, TypeError):
            pass
    return uniform_week(start_hour, end_hour)


def dump_week(week: list[dict]) -> str:
    return json.dumps(normalize_week(week), ensure_ascii=False)


def day_config(week: list[dict], day: date_cls) -> dict:
    return week[day.weekday()]


def legacy_hours(week: list[dict]) -> tuple[int, int]:
    """Общие часы для старых полей work_start_hour/work_end_hour: самый ранний старт и самый поздний конец
    среди рабочих дней (с округлением до часа наружу)."""
    opened = [d for d in week if d["open"]]
    start = min(parse_time(d["start"]) for d in opened) // 60
    end_minutes = max(parse_time(d["end"]) for d in opened)
    return start, min(24, -(-end_minutes // 60))
