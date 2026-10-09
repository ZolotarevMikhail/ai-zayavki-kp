"""Единая точка приёма заявок из всех каналов + ответ заказчику.

Канал приносит либо файл (путь), либо текст сообщения. Дальше всё идёт
одной дорогой: разбор файла, распознавание, запись в базу. Новый канал
подключается вызовом process_file или process_text — конвейер не меняется.
Каждая заявка хранит обратный контакт заказчика, если канал его даёт.
"""
from pathlib import Path

from src import db, recognizer
from src.parsers import ALLOWED_SUFFIXES, parse_file

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def process_file(path: str, channel: str = "файл",
                 contact_value: str = "") -> int | None:
    """Один файл -> заявка. Возвращаем номер или None, если файл
    обработать нельзя."""
    suffix = Path(path).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return None
    text = parse_file(path)
    if suffix in IMAGE_SUFFIXES and recognizer.demo_mode():
        return None  # картинки распознаются только в реальном режиме
    try:
        positions = recognizer.extract_parameters(text, path)
    except (ValueError, RuntimeError, OSError):
        return None
    return db.create_order(
        Path(path).name, text, positions, channel=channel,
        contact_value=contact_value,
    )


def process_files(items: list, channel: str = "файл") -> int:
    """Обрабатываем список файлов (строки или пары «путь, контакт»)."""
    created = 0
    for item in items:
        contact_value = ""
        if isinstance(item, (tuple, list)):
            path, contact_value = item[0], item[1] if len(item) > 1 else ""
        else:
            path = item
        if process_file(path, channel=channel, contact_value=contact_value):
            created += 1
    return created


def process_text(channel: str, text: str,
                 contact_value: str = "") -> int | None:
    """Текстовое сообщение из переписки -> заявка. Возвращаем номер."""
    if not text or not text.strip():
        return None
    try:
        positions = recognizer.extract_parameters(text)
    except (ValueError, RuntimeError) as error:
        raise RuntimeError(f"Распознавание не удалось: {error}")
    if not positions:
        # Распознаватель не нашёл позиций: заявка сохранится одной строкой,
        # сотрудник на экране проверки уточнит параметры.
        positions = [{
            "name": text.strip()[:200],
            "material": "",
            "size": "",
            "count": 0,
            "operations": [],
        }]
    label = f"{channel}: {channel_message_from(text)}"
    contact = text_contact(contact_value, text)
    return db.create_order(
        label, text, positions, channel=channel, contact_value=contact,
    )


def text_contact(contact_value: str, text: str) -> str:
    """Контакт для заявки: явный контакт, иначе контакт из текста."""
    if contact_value:
        return contact_value
    found = find_contact_in_text(text)
    return found


def find_contact_in_text(text: str) -> str:
    """Вытаскиваем из текста контакт: телефон или адрес почты."""
    import re

    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    if email:
        return email.group(0)
    phone = re.search(r"(\+?\d[\d\s()-]{9,18}\d)", text)
    return phone.group(1).strip() if phone else ""


def send_reply(order: dict, text: str) -> str:
    """Ответ заказчику из системы. Возвращаем результат или ''.

    Telegram и VK отвечаем от имени ботов прямо в переписку. Для почты и
    сайта интерфейс сам даёт ссылку (mailto/tel) — прямая отправка не
    предусмотрена, это честно показываем оператору.
    """
    if not text.strip():
        return ""
    channel = order.get("contact_channel") or ""
    target = order.get("contact_value") or ""
    if channel == "Telegram" and target:
        import telegram_bot

        if not telegram_bot.configured():
            return "Telegram не настроен: заполните TELEGRAM_BOT_TOKEN в .env."
        telegram_bot._say(int(target), text)
        return "Ответ отправлен в Telegram."
    if channel == "VK" and target:
        import vk_bot

        if not vk_bot.configured():
            return ("VK не настроен: заполните VK_ACCESS_TOKEN"
                    " и VK_GROUP_ID в .env.")
        vk_bot._say(int(target), text)
        return "Ответ отправлен во ВКонтакте."
    return ""


def kp_files(order: dict) -> tuple:
    """Файлы КП: Word — основной документ, Excel — таблица к нему.

    Оба готовятся каждый раз заново по свежим данным расчёта.
    """
    from src import excel_out, render_kp

    return render_kp.build_docx(order), excel_out.build(order)


def send_kp(order: dict, channel: str) -> str:
    """КП уходит «как есть»: документ Word и таблица Excel.

    Прямо отправить получается только Telegram и VK, потому что оттуда
    известен адрес собеседника. Почта отправляется на отдельной кнопке
    через ящик оператора (SMTP) или вручную из его почтовой программы.
    """
    if channel not in ("Telegram", "VK"):
        return ("Прямую отправку поддерживают только Telegram и VK."
                " По почте — кнопка «Отправить».")
    if (order.get("contact_channel") or "") != channel:
        return (f"Заявка пришла не из {channel}:"
                " переписки с заказчиком там нет.")
    target = order.get("contact_value") or ""
    if not target:
        return "У заявки нет адресата в этом канале."
    word_path, excel_path = kp_files(order)
    sent = _send_files(
        channel, int(target), word_path, excel_path, order["id"]
    )
    if sent:
        return f"КП № {order['id']} отправлено в {channel}: Word и Excel."
    return (f"Файлы в {channel} не ушли: проверьте, что токен задан в .env"
            " и запущен run_channels.py.")


def _send_files(channel: str, target: int, word_path: str,
                excel_path: str, order_id: int) -> bool:
    """Загружаем оба файла в переписку выбранного канала."""
    caption = f"Коммерческое предложение № {order_id}"
    if channel == "Telegram":
        import telegram_bot

        if not telegram_bot.configured():
            return False
        first = telegram_bot.send_file(target, word_path, caption=caption)
        return bool(first and telegram_bot.send_file(target, excel_path))
    import vk_bot

    if not vk_bot.configured():
        return False
    first = vk_bot.send_file(target, word_path, text=caption)
    return bool(first and vk_bot.send_file(target, excel_path))


def contact_kind(value: str) -> str:
    """Тип реквизитов: почта, телефон, переписка или просто текст."""
    value = (value or "").strip().lower()
    if "@" in value and "." in value.split("@")[-1]:
        return "email"
    if value.startswith("+") or value.isdigit():
        return "phone"
    return "chat"


def channel_message_from(text: str) -> str:
    """Короткий ярлык заявки: первые слова текста."""
    words = text.strip().split()[:5]
    label = " ".join(words)
    return (label[:60] + "…") if len(text) > 60 else label
