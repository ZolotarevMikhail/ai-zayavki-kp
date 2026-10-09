"""Приём заявок по электронной почте через IMAP.

Канал подключается в .env (IMAP_HOST, IMAP_USER, IMAP_PASSWORD). Модуль
забирает непрочитанные письма, вложения кладёт в data/uploads и возвращает
список файлов для общего конвейера обработки. Письма помечаются прочитанными.
"""
import email
import imaplib
import re
from email.header import decode_header
from pathlib import Path

from src.config import IMAP_HOST, IMAP_PASSWORD, IMAP_USER, UPLOADS_DIR

TIMEOUT_SECONDS = 30


def mail_configured() -> bool:
    """Есть ли в .env доступ к почте."""
    return bool(IMAP_HOST and IMAP_USER and IMAP_PASSWORD)


def fetch_attachments() -> list:
    """Забираем вложения непрочитанных писем.

    Возвращает пары «путь к файлу, адрес отправителя» — контакт нужен
    заявке для обратной связи.
    """
    if not mail_configured():
        return []
    connection = imaplib.IMAP4_SSL(IMAP_HOST, timeout=TIMEOUT_SECONDS)
    saved = []
    try:
        connection.login(IMAP_USER, IMAP_PASSWORD)
        connection.select("INBOX")
        status, data = connection.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Почтовый сервер не вернул список писем")
        for message_id in data[0].split():
            saved.extend(_process_message(connection, message_id))
    finally:
        _quiet_logout(connection)
    return saved


def _process_message(connection, message_id: bytes) -> list:
    """Вложения письма сохраняем вместе с адресом отправителя."""
    status, data = connection.fetch(message_id, "(RFC822)")
    if status != "OK" or not data or data[0] is None:
        return []
    message = email.message_from_bytes(data[0][1])
    sender = _decode_address(message.get("From", ""))
    saved = []
    for part in message.walk():
        filename = part.get_filename()
        if filename and not part.is_multipart():
            path = _save_attachment(part, filename)
            saved.append((path, sender))
    connection.store(message_id, "+FLAGS", "\\Seen")
    return saved


def _decode_address(value: str) -> str:
    """Разбираем шапку «From» вида 'Иван <ivan@x.ru>' в чистый адрес."""
    chunks = decode_header(value)
    decoded = "".join(
        chunk.decode(charset or "utf-8", errors="replace")
        if isinstance(chunk, bytes) else chunk
        for chunk, charset in chunks
    )
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", decoded)
    return match.group(0) if match else decoded.strip()


def _save_attachment(part, filename: str) -> str:
    """Вложение сохраняем в data/uploads с декодированным именем."""
    chunks = decode_header(filename)
    name = "".join(
        chunk.decode(charset or "utf-8", errors="replace")
        if isinstance(chunk, bytes) else chunk
        for chunk, charset in chunks
    )
    allowed = (char for char in name if char.isalnum() or char in "._- ")
    safe_name = "".join(allowed) or "attachment"
    target = UPLOADS_DIR / safe_name
    counter = 1
    while target.exists():
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        target = UPLOADS_DIR / f"{stem}_{counter}{suffix}"
        counter += 1
    payload = part.get_payload(decode=True) or b""
    target.write_bytes(payload)
    return str(target)


def _quiet_logout(connection) -> None:
    """Выход без исключений: обрыв соединения не считается ошибкой."""
    try:
        connection.logout()
    except OSError:
        pass
