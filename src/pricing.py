"""Расчёт стоимости строго по формулам и прайс-листу. ИИ здесь не участвует."""
import json
from pathlib import Path

from src.config import PRICE_PATH

# Прайс по умолчанию создаётся при первом запуске из этого файла
DEFAULT_PRICE = {
    "materials": {
        "AISI 304": {"price_per_unit": 850.0, "unit": "ед."},
        "AISI 321": {"price_per_unit": 990.0, "unit": "ед."},
        "ALUM": {"price_per_unit": 540.0, "unit": "ед."},
    },
    "rates": {
        "резка": 120.0,
        "сварка": 450.0,
        "шлифовка": 180.0,
        "сверловка": 220.0,
    },
    "margin": 1.25,          # наценка к себестоимости
    "delivery": 1500.0,      # доставка одним платежом на заявку
}


def load_price_list() -> dict:
    """Читаем прайс, при отсутствии создаём копию по умолчанию."""
    if not Path(PRICE_PATH).exists():
        save_price_list(DEFAULT_PRICE)
    with open(PRICE_PATH, encoding="utf-8") as file:
        return json.load(file)


def save_price_list(price: dict) -> None:
    """Сохраняем прайс (этап 2 — правка из интерфейса)."""
    with open(PRICE_PATH, "w", encoding="utf-8") as file:
        json.dump(price, file, ensure_ascii=False, indent=2)


def calculate(positions: list) -> dict:
    """Считаем стоимость каждой позиции и итог. Формула одна для всех:

    позиция = (материал + операции) * наценка;
    итог = сумма позиций + доставка.
    """
    price = load_price_list()
    materials, rates = price["materials"], price["rates"]
    margin, delivery = price["margin"], price["delivery"]

    items, unknown_materials, unknown_operations = [], [], []
    total = 0.0
    for number, position in enumerate(positions, start=1):
        count = int(position.get("count") or 0)
        material = position.get("material", "")
        operations = position.get("operations", [])

        material_price = materials.get(material, {}).get("price_per_unit", 0.0)
        if material and material not in materials:
            unknown_materials.append(material)
        material_cost = material_price * count

        operation_cost, missing = 0.0, []
        for operation in operations:
            rate = rates.get(operation)
            if rate is None:
                unknown_operations.append(operation)
                missing.append(operation)
                continue
            operation_cost += rate * count

        subtotal = (material_cost + operation_cost) * margin
        subtotal = round(subtotal, 2)
        total += subtotal
        items.append({
            "number": number,
            "name": position.get("name", ""),
            "material": material,
            "size": position.get("size", ""),
            "count": count,
            "operations": operations,
            "material_cost": round(material_cost, 2),
            "operation_cost": round(operation_cost, 2),
            "subtotal": subtotal,
        })

    total = round(total + delivery, 2)
    return {
        "items": items,
        "margin": margin,
        "delivery": round(delivery, 2),
        "total": total,
        "unknown_materials": unknown_materials,
        "unknown_operations": unknown_operations,
    }
