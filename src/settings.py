"""Настройки компании: шапка КП, контакты, срок действия. Правится в JSON."""
import json
from pathlib import Path

from src.config import SETTINGS_PATH

DEFAULT_SETTINGS = {
    "org_name": "Ваша компания",
    "org_phone": "",
    "org_email": "",
    "kp_validity": "10 дней",
    "currency": "₽",
    "lead_note": "Срок изготовления и условия оплаты уточняются при заказе.",
}


def load_settings() -> dict:
    """Читаем настройки, при отсутствии создаём файл по умолчанию."""
    if not Path(SETTINGS_PATH).exists():
        save_settings(DEFAULT_SETTINGS)
    with open(SETTINGS_PATH, encoding="utf-8") as file:
        return json.load(file)


def save_settings(settings: dict) -> None:
    """Сохраняем настройки (этап 2 — правка из интерфейса)."""
    with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
