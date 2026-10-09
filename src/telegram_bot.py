"""Канал Telegram: бот забирает заявки из личных сообщений бота.

Работает долгим опросом (getUpdates): на сервер ничего открывать не нужно.
Каждое текстовое сообщение превращается в заявку; фото и документы принимаются
в реальном режиме при наличии ключа распознавания. Ответ в чат — номер заявки.
"""
import time

import requests

from src import channels
from src.config import TELEGRAM_BOT_TOKEN

API_BASE = "https://api.telegram.org"
RECONNECT_PAUSE = 10  # секунд тишины после сбоя сети, чтобы не долбить


def configured() -> bool:
    """Настроен ли канал: токен в .env."""
    return bool(TELEGRAM_BOT_TOKEN)


def method_url(name: str) -> str:
    """Адрес метода Telegram API с токеном бота."""
    return f"{API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{name}"


def run_forever() -> None:
    """Основной цикл бота: долгий опрос новых сообщений,
    перезапуск при сбоях."""
    offset = None
    while True:  # бесконечный опрос — это режим работы фонового бота
        try:
            response = requests.get(
                method_url("getUpdates"),
                params={"timeout": 30, "offset": offset or 0},
                timeout=60,
            )
            response.raise_for_status()
            for update in response.json().get("result", []):
                offset = update.get("update_id", 0) + 1
                _handle_update(update)
        except requests.RequestException as error:
            print(f"Telegram: сеть не отвечает ({error}),"
                  f" пауза {RECONNECT_PAUSE} с")
            time.sleep(RECONNECT_PAUSE)


def _handle_update(update: dict) -> None:
    """Одно событие: текст -> заявка, фото/документ -> файловая дорога."""
    message = update.get("message")
    if message is None or not message.get("chat"):
        return
    if message.get("text"):
        _accept_text(message)
        return
    file_path = _download_file(message)
    if file_path:
        channels.process_files([file_path])
        _say(message["chat"]["id"], "Файл принят, заявка создана.")
    else:
        _say(message["chat"]["id"], _text_about_files())


def _accept_text(message: dict) -> None:
    """Текст заявки распознаём (или сохраняем «как есть»)
    и отвечаем номером."""
    chat_id = message["chat"]["id"]
    try:
        order_id = channels.process_text(
            "Telegram", message["text"], contact_value=str(chat_id)
        )
        answer = f"Заявка принята, номер {order_id}. Оператор проверит данные."
    except RuntimeError as error:
        answer = f"Заявку не получилось обработать: {error}"
    _say(chat_id, answer)


def _text_about_files() -> str:
    """Человеческий ответ, когда файл принять пока нельзя."""
    if channels.recognizer.demo_mode():
        return "Фото пока не принимаю: включён демо-режим распознавания."
    return ("Файл не удалось прочитать. Отправьте фото большего размера"
            " или текст.")


def _download_file(message: dict) -> str:
    """Фото или документ из Telegram скачиваем и возвращаем путь."""
    if channels.recognizer.demo_mode():
        return ""
    file_id = ""
    if message.get("photo"):
        file_id = message["photo"][-1]["file_id"]  # самый крупный вариант
    elif message.get("document"):
        file_id = message["document"]["file_id"]
    if not file_id:
        return ""
    info = requests.get(
        method_url("getFile"), params={"file_id": file_id}
    ).json()
    file_api_path = info.get("result", {}).get("file_path", "")
    if not file_api_path:
        return ""
    data = requests.get(
        f"{API_BASE}/file/bot{TELEGRAM_BOT_TOKEN}/{file_api_path}"
    )
    stem = file_api_path.split("/")[-1] or "telegram-file"
    target = _save_bytes(stem, data.content)
    return str(target)


def _save_bytes(stem: str, payload: bytes):
    from pathlib import Path

    from src.config import UPLOADS_DIR

    suffix = "." + stem.rsplit(".", 1)[-1].lower() if "." in stem else ".bin"
    target = UPLOADS_DIR / f"tg_{stem}"
    counter = 1
    while target.exists():
        target = UPLOADS_DIR / f"tg_{Path(stem).stem}_{counter}{suffix}"
        counter += 1
    target.write_bytes(payload)
    return target


def _say(chat_id: int, text: str) -> None:
    """Ответ в чат; сбой связи не должен ломать опрос."""
    try:
        requests.post(
            method_url("sendMessage"),
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
    except requests.RequestException as error:
        print(f"Telegram: не отправился ответ ({error})")


def send_file(chat_id: int, path: str, caption: str = "") -> bool:
    """Файл КП (Word или Excel) в переписку. True — доставку подтвердил API."""
    from pathlib import Path

    try:
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:1024]
        with open(path, "rb") as file:
            response = requests.post(
                method_url("sendDocument"),
                data=fields,
                files={"document": (Path(path).name, file)},
                timeout=60,
            )
        response.raise_for_status()
        return response.json().get("ok", False)
    except requests.RequestException as error:
        print(f"Telegram: файл не отправился ({error})")
        return False
