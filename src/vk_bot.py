"""Канал VK: бот принимает заявки в сообщениях сообщества.

Работает через Long Poll (groups.getLongPollServer): постоянное соединение
с сервером VK, никаких открытий наружу не нужно. Текст сообщения -> заявка,
ответ в переписку с номером заявки.
"""
import time

import requests

from src import channels
from src.config import VK_ACCESS_TOKEN, VK_GROUP_ID

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.131"
RECONNECT_PAUSE = 10


def configured() -> bool:
    """Настроен ли канал: токен сообщества и его номер."""
    return bool(VK_ACCESS_TOKEN and VK_GROUP_ID)


def run_forever() -> None:
    """Основной цикл: получаем адрес Long Poll и слушаем события. """
    while True:  # фоновый бот живёт, пока запущен процесс
        try:
            poll_server = _get_long_poll_server()
            while True:
                events = _listen(poll_server)
                for event in events:
                    _handle_event(event)
        except Exception as error:  # noqa: BLE001 — бот выживает и повторяет
            print(f"VK: канал не отвечает ({error}),"
                  f" пауза {RECONNECT_PAUSE} с")
            time.sleep(RECONNECT_PAUSE)


def _get_long_poll_server() -> dict:
    """Первое подключение: у VK спрашиваем адрес нашего Long Poll."""
    response = requests.get(
        f"{API_BASE}/groups.getLongPollServer",
        params={"group_id": VK_GROUP_ID, "access_token": VK_ACCESS_TOKEN,
                "v": API_VERSION},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json().get("response")
    if not result:
        raise RuntimeError(
            "VK не дал адрес Long Poll, проверьте токен сообщества"
        )
    return result


def _listen(poll_server: dict) -> list:
    """Ждём новые события; сервер сам держит подключение до 25 секунд."""
    response = requests.get(
        poll_server["server"],
        params={"act": "a_check", "key": poll_server["key"],
                "ts": poll_server["ts"], "wait": 25},
        timeout=40,
    )
    response.raise_for_status()
    data = response.json()
    updates = data.get("updates", [])
    if data.get("ts"):
        poll_server["ts"] = data["ts"]
    if data.get("failed"):  # ключ устарел — пересоздаём адрес
        poll_server.update(_get_long_poll_server())
    return updates


def _handle_event(event: dict) -> None:
    """Событие message_new: текст -> заявка, ответ в переписку."""
    object_body = event.get("object", {})
    message = object_body.get("message", object_body)  # v5.103+: message
    text = message.get("text", "")
    peer_id = message.get("peer_id", 0)
    if not text or not peer_id:
        return
    try:
        order_id = channels.process_text(
            "VK", text, contact_value=str(peer_id)
        )
        answer = f"Заявка принята, номер {order_id}. Оператор проверит данные."
    except RuntimeError as error:
        answer = f"Заявку не получилось обработать: {error}"
    _say(peer_id, answer)


def _say(peer_id: int, text: str) -> None:
    """Ответ в VK от имени сообщества; сетевые сбои не останавливают бота."""
    try:
        requests.post(
            f"{API_BASE}/messages.send",
            params={"peer_id": peer_id, "message": text,
                    "random_id": time.time_ns(), "v": API_VERSION,
                    "access_token": VK_ACCESS_TOKEN},
            timeout=30,
        )
    except requests.RequestException as error:
        print(f"VK: не отправился ответ ({error})")


def send_file(peer_id: int, path: str, text: str = "") -> bool:
    """Файл КП в переписку VK: документы сообщества + вложение в письмо."""
    from pathlib import Path

    try:
        upload_url = _document_upload_url()
        if not upload_url:
            return False
        file_field = _upload_document(upload_url, path)
        if not file_field:
            return False
        attachment = _save_document(file_field, Path(path).name)
        if not attachment:
            return False
        response = requests.post(
            f"{API_BASE}/messages.send",
            params={"peer_id": peer_id, "message": text,
                    "attachment": attachment,
                    "random_id": time.time_ns(), "v": API_VERSION,
                    "access_token": VK_ACCESS_TOKEN},
            timeout=60,
        )
        response.raise_for_status()
        # VK отвечает 200 и на ошибку ({"error": {...}}) — обманывать нельзя.
        payload = response.json()
        if "error" in payload:
            print(f"VK: API вернул ошибку"
                  f" ({payload['error'].get('error_msg', '')})")
            return False
        return bool(payload.get("response"))
    except requests.RequestException as error:
        print(f"VK: файл не отправился ({error})")
        return False


def _document_upload_url() -> str:
    """Адрес сервера VK, куда загружаются документы сообщества."""
    response = requests.get(
        f"{API_BASE}/docs.getWallUploadServer",
        params={"type": "doc", "group_id": VK_GROUP_ID,
                "access_token": VK_ACCESS_TOKEN, "v": API_VERSION},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("response", {}).get("upload_url", "")


def _upload_document(upload_url: str, path: str) -> str:
    """Загружаем сам файл, VK возвращает поле file для сохранения."""
    from pathlib import Path

    with open(path, "rb") as file:
        response = requests.post(
            upload_url,
            files={"file": (Path(path).name, file)},
            timeout=120,
        )
    response.raise_for_status()
    return response.json().get("file", "")


def _save_document(file_field: str, title: str) -> str:
    """Сохраняем документ и получаем метку вида doc<владелец>_<номер>."""
    response = requests.get(
        f"{API_BASE}/docs.save",
        params={"file": file_field, "title": title,
                "access_token": VK_ACCESS_TOKEN, "v": API_VERSION},
        timeout=30,
    )
    response.raise_for_status()
    saved = response.json().get("response") or []
    if not saved:
        return ""
    return f"doc{saved[0]['owner_id']}_{saved[0]['id']}"
