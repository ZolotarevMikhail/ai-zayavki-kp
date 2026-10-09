"""Чтение файлов заявок: текст, PDF, Excel, Word.

Картинки в текст не читаем: их обработает ИИ с зрением.
"""
import pdfplumber
from docx import Document
from openpyxl import load_workbook
from pathlib import Path

# Форматы, которые система принимает на вход
ALLOWED_SUFFIXES = {
    ".txt", ".pdf", ".xlsx", ".xls",
    ".docx", ".png", ".jpg", ".jpeg", ".webp",
}


def parse_file(path: str) -> str:
    """Возвращает текст файла заявки; картинки дают пустую строку."""
    suffix = Path(path).suffix.lower()
    readers = {
        ".txt": _parse_txt,
        ".pdf": _parse_pdf,
        ".xlsx": _parse_xlsx,
        ".xls": _parse_xlsx,
        ".docx": _parse_docx,
    }
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        return ""  # текст из картинки извлечёт ИИ с зрением
    if suffix not in readers:
        raise ValueError(f"Формат {suffix} не поддерживается")
    return readers[suffix](path)


def _parse_txt(path: str) -> str:
    """Просто читаем текст целиком."""
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _parse_pdf(path: str) -> str:
    """Вытаскиваем текст со всех страниц PDF."""
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            parts.append(text)
    return "\n".join(parts)


def _parse_xlsx(path: str) -> str:
    """Каждую строку листа превращаем в текстовую строку."""
    book = load_workbook(path, read_only=True, data_only=True)
    lines = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell) for cell in row if cell is not None]
            if cells:
                lines.append("; ".join(cells))
    book.close()
    return "\n".join(lines)


def _parse_docx(path: str) -> str:
    """Абзацы Word-документа складываем в текст."""
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)
