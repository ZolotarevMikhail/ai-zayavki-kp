"""Отправка писем с КП через SMTP: текст письма плюс файлы Word и Excel.

Доступ к ящику оператора задаётся в .env (SMTP_HOST, SMTP_PORT, SMTP_USER,
SMTP_PASSWORD). Пока доступа нет — интерфейс подставляет письмо в почтовую
программу, файла прикрепляются вручную после скачивания.
"""
import smtplib
from email.message import EmailMessage
from pathlib import Path

from src import config


def smtp_configured() -> bool:
    """Письма с файлами уходят сами, если доступ к ящику задан в .env."""
    return bool(
        config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD
    )


def send_letter(to: str, subject: str, body: str, attachments: list) -> None:
    """Одно письмо: текст КП и прикреплённые файлы Word и Excel."""
    message = EmailMessage()
    message["From"] = config.SMTP_USER
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    for path in attachments:
        message.add_attachment(
            Path(path).read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=Path(path).name,
        )
    if config.SMTP_PORT == 587:
        _via_starttls(message)
    else:
        _via_ssl(message)


def _via_ssl(message: EmailMessage) -> None:
    """Ящики, принимающие письма сразу по защищённому соединению (465)."""
    with smtplib.SMTP_SSL(
        config.SMTP_HOST, config.SMTP_PORT, timeout=30
    ) as server:
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(message)


def _via_starttls(message: EmailMessage) -> None:
    """Ящики, которые сначала открывают соединение (587), потом защищают."""
    with smtplib.SMTP(
        config.SMTP_HOST, config.SMTP_PORT, timeout=30
    ) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(message)
