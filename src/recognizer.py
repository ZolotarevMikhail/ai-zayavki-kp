"""Распознавание параметров заявки через ИИ + демо-режим без API.

ИИ нужен только на этом шаге: он превращает текст заявки в список позиций
(наименование, материал, количество, операции). Цифры считает pricing.py.
"""
import base64
import json
import re
import time
from pathlib import Path

from src.config import DEMO_MODE, PROXYAPI_KEY, RECOGNIZER_MODEL

API_URL = "https://api.proxyapi.ru/openai/v1/chat/completions"

# Структура одной позиции, которую понимает расчётный модуль
POSITION_KEYS = ("name", "material", "size", "count", "operations")


def extract_parameters(text: str, file_path: str = "") -> list:
    """Главный вход: текст (и путь для картинок) -> список позиций заявки.

    Демо-режим разбирает простой формат «параметр: значение» без сети.
    Реальный режим отправляет текст или картинку в модель с зрением.
    """
    if DEMO_MODE:
        return _recognize_demo(text)
    return _recognize_ai(text, file_path)


def demo_mode() -> bool:
    """Признак для интерфейса: работаем без ключа или нет."""
    return DEMO_MODE


def _recognize_demo(text: str) -> list:
    """Разбор демо-заявки: строки вида «Труба..., материал: X, кол-во: 5»."""
    positions = []
    for line in text.splitlines():
        line = line.strip()
        if not line or _looks_like_header(line):
            continue
        parts = [part.strip() for part in line.split(",")]
        name = parts[0]
        material, size, count, operations = "", "", "", []
        for part in parts[1:]:
            low = part.lower()
            if "материал" in low and ":" in part:
                material = part.split(":", 1)[1].strip()
            elif "длина" in low or "площадь" in low or "количество шт" in low:
                size = part.split(":", 1)[1].strip() if ":" in part else part
            elif "количество" in low and ":" in part:
                count = _safe_int(part.split(":", 1)[1])
            elif "операции" in low and ":" in part:
                raw = part.split(":", 1)[1]
                operations = [
                    op.strip() for op in raw.split("/") if op.strip()
                ]
        positions.append({
            "name": name,
            "material": material,
            "size": size,
            "count": count,
            "operations": operations,
        })
    return positions


def _looks_like_header(line: str) -> bool:
    """Строки «Заявка №...» и пустые заголовки пропускаем."""
    return bool(re.search(r"заявка|объект|дата", line.lower()))


def _safe_int(value: str) -> int:
    """Количество превращаем в число, а иначе 0 — экран проверки подсветит."""
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def _recognize_ai(text: str, file_path: str) -> list:
    """Реальный режим: запрос к модели через ProxyAPI с повторами."""
    import requests  # отложенный импорт, чтобы демо-режим не требовал сеть

    content = _build_message_content(text, file_path)
    payload = {
        "model": RECOGNIZER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
    }
    last_error = None
    for attempt in range(3):  # ограниченное число попыток, не бесконечно
        if attempt:
            time.sleep(3)  # повторяемая ошибка: пауза, чтобы не долбить API
        try:
            response = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {PROXYAPI_KEY}"},
                json=payload,
                timeout=90,
            )
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"]["content"]
            return _parse_model_answer(answer)
        except requests.HTTPStatusError as error:
            status = error.response.status_code
            if status in (400, 401, 403):
                # ключ/запрос не изменятся при повторе: три платных вызова
                # подряд на одну и ту же ошибку — выбрасываем сразу.
                raise RuntimeError(
                    f"ИИ не принял запрос (HTTP {status}):"
                    " проверьте ключ ProxyAPI и формат заявки"
                ) from error
            last_error = error  # 429/5xx — повторяемая, ждём и пробуем опять
        except Exception as error:  # noqa: BLE001 — покажем пользователю
            last_error = error
    raise RuntimeError(f"ИИ-распознавание не удалось: {last_error}")


def _build_message_content(text: str, file_path: str) -> list:
    """Текст отправляем строкой, картинку — блоком с base64."""
    if text:
        return [{"type": "text", "text": f"Заявка:\n{text}"}]
    suffix = Path(file_path).suffix.lstrip(".").lower()
    if suffix not in ("png", "jpg", "jpeg", "webp"):
        # Пустой .txt/.pdf — не картинка: API-400 на image/txt не допускаем.
        raise ValueError(
            "В файле не нашлось текста заявки."
            " Напишите заявку текстом или пришлите фото."
        )
    encoded = base64.b64encode(Path(file_path).read_bytes()).decode("utf-8")
    media_type = f"image/{'jpeg' if suffix in ('jpg', 'jpeg') else suffix}"
    return [
        {"type": "text",
         "text": "Заявка на картинке. Извлеки позиции по инструкции."},
        {"type": "image_url",
         "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
    ]


def _parse_model_answer(answer: str) -> list:
    """Ответ модели чистим от обрамления и превращаем в список позиций."""
    cleaned = re.sub(r"```(?:json)?|```", "", answer).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("Модель вернула не список позиций")
    raw = json.loads(cleaned[start:end + 1])
    positions = []
    for item in raw:
        positions.append({
            "name": str(item.get("name", "")),
            "material": str(item.get("material", "")),
            "size": str(item.get("size", "")),
            "count": _safe_int(str(item.get("count", 0))),
            "operations": [str(op) for op in item.get("operations", [])],
        })
    return positions


SYSTEM_PROMPT = (
    "Ты помощник по обработке заявок на металлообработку. "
    "Извлеки из заявки все позиции и верни ТОЛЬКО JSON-список без пояснений. "
    "Формат элемента: {\"name\": наименование, \"material\": материал, "
    "\"size\": размер одной единицы, \"count\": количество (число), "
    "\"operations\": [операции для каждой единицы]}. "
    "Если поле не найдено, ставь пустую строку, а count — 0."
    " Ничего не выдумывай."
)
