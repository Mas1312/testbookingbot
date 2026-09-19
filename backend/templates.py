"""Шаблоны ниш для мастера первого запуска: примерные услуги и рабочие часы, которые владелец
выбирает одним касанием и потом правит под себя. Цены и описания — ориентир, не рекомендация."""

NICHE_TEMPLATES = [
    {
        "id": "beauty",
        "icon": "scissors",
        "title": "Красота: мастер или салон",
        "schedule": {"work_start_hour": 10, "work_end_hour": 20, "slot_step_minutes": 30, "days_ahead": 14},
        "services": [
            {"name": "Маникюр", "price": 1500, "duration_min": 90,
             "description": "Обработка, покрытие, уход за кутикулой"},
            {"name": "Педикюр", "price": 2000, "duration_min": 90,
             "description": "Аппаратный или классический, с покрытием по желанию"},
            {"name": "Стрижка", "price": 1500, "duration_min": 60,
             "description": "Мытьё головы, стрижка и укладка"},
            {"name": "Коррекция бровей", "price": 800, "duration_min": 30,
             "description": "Форма подбирается по вашему типу лица"},
            {"name": "Окрашивание", "price": 3500, "duration_min": 120,
             "description": "Одним тоном или мелирование — уточняется на месте"},
        ],
    },
    {
        "id": "rental",
        "icon": "clock",
        "title": "Аренда по часам",
        "schedule": {"work_start_hour": 9, "work_end_hour": 21, "slot_step_minutes": 60, "days_ahead": 14},
        "services": [
            {"name": "Аренда на 1 час", "price": 1000, "duration_min": 60,
             "description": "Инвентарь и краткий инструктаж включены"},
            {"name": "Аренда на 2 часа", "price": 1800, "duration_min": 120,
             "description": "Выгоднее, чем два раза по часу"},
            {"name": "Аренда на 4 часа", "price": 3200, "duration_min": 240,
             "description": "Половина дня — для длинных прогулок и компаний"},
        ],
    },
    {
        "id": "consult",
        "icon": "chat",
        "title": "Консультации и занятия",
        "schedule": {"work_start_hour": 10, "work_end_hour": 20, "slot_step_minutes": 30, "days_ahead": 14},
        "services": [
            {"name": "Пробное занятие", "price": 500, "duration_min": 30,
             "description": "Познакомимся и определим цели"},
            {"name": "Консультация 30 минут", "price": 1500, "duration_min": 30,
             "description": "Короткий разбор одного вопроса"},
            {"name": "Консультация 60 минут", "price": 2500, "duration_min": 60,
             "description": "Подробный разбор и план действий"},
        ],
    },
]

TEMPLATES_BY_ID = {t["id"]: t for t in NICHE_TEMPLATES}
