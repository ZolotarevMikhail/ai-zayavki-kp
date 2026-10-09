"""Конфигурация проекта: пути, режим работы, чтение секретов из .env."""
from pathlib import Path

from dotenv import load_dotenv
import os

# Корень проекта и обязательные папки
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
KP_DIR = DATA_DIR / "kp"
DB_PATH = DATA_DIR / "app.db"
PRICE_PATH = DATA_DIR / "price_list.json"
TEMPLATES_DIR = DATA_DIR / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "kp_template.docx"
SETTINGS_PATH = DATA_DIR / "settings.json"

# Секреты: ключ ProxyAPI и доступ к почте — в .env, в код не зашиваем.
load_dotenv(BASE_DIR / ".env")
PROXYAPI_KEY = os.getenv("PROXYAPI_KEY", "")
RECOGNIZER_MODEL = os.getenv("RECOGNIZER_MODEL", "gpt-4o-mini")

# Канал почты: если задан хост, кнопка «Загрузить письма» работает.
IMAP_HOST = os.getenv("IMAP_HOST", "")
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")

# Канал Telegram: токен бота из @BotFather.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Канал VK: токен сообщества и его номер (доступ к сообщениям).
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "")

# Ящик для исходящих писем с КП (SMTP): с ним кнопка «Отправить» сама
# отправляет письмо с файлами Word и Excel.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Ключ доступа к заявкам с внешнего сайта (программная отправка).
API_KEY = os.getenv("API_KEY", "")

# Права доступа: пароль руководителя даёт кнопку «Прайс», пароль
# сотрудника — работу с заявками. STAFF_SEATS — число компьютеров,
# с которых сотрудники могут работать одновременно.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
STAFF_PASSWORD = os.getenv("STAFF_PASSWORD", "")
STAFF_SEATS = int(os.getenv("STAFF_SEATS", "3"))

# Без ключа работаем в демо-режиме: реальных запросов нет, расходов нет.
DEMO_MODE = not bool(PROXYAPI_KEY)

for _folder in (DATA_DIR, UPLOADS_DIR, KP_DIR, TEMPLATES_DIR):
    _folder.mkdir(parents=True, exist_ok=True)
