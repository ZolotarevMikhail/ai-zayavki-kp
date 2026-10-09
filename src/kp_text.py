"""Текстовая версия КП: для экрана, письма и чатов Telegram/VK.

Один источник для всего: таблицу, Excel и переписку собираем из одних
позиций расчёта, шапку берём из настроек компании.
"""


def money(value: float) -> str:
    """Число с пробелами-разделителями: 36688 -> «36 688»."""
    return f"{value:,.0f}".replace(",", " ")


def build(order: dict, settings: dict) -> dict:
    """Собираем КП одним текстовым блоком: шапка, позиции, итоги, условия."""
    calc = order["calc"]
    currency = settings.get("currency", "₽")
    lines = ["КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ"]
    if settings.get("org_name"):
        lines.append(settings["org_name"])
    contacts = " — ".join(
        part for part in (settings.get("org_phone"), settings.get("org_email"))
        if part
    )
    if contacts:
        lines.append(contacts)
    lines.append(f"по заявке № {order['id']} от {order['created_at'][:10]}")
    lines.append("")
    for item in calc["items"]:
        operations = item.get("operations", [])
        row = f"{item['name']} — {item['count']} шт."
        if item["size"]:
            row += f", {item['size']}"
        if item["material"]:
            row += f", материал {item['material']}"
        if operations:
            row += f", работы: {', '.join(operations)}"
        lines.append(row)
        lines.append(f"    цена: {money(item['subtotal'])} {currency}")
    subtotal_all = calc["total"] - calc["delivery"]
    lines.append("")
    lines.append(f"Стоимость материалов и работ: "
                 f"{money(subtotal_all)} {currency}")
    lines.append(f"Доставка: {money(calc['delivery'])} {currency}")
    lines.append(f"ИТОГО: {money(calc['total'])} {currency}")
    lines.append("")
    if settings.get("kp_validity"):
        lines.append(f"Предложение действует: {settings['kp_validity']}.")
    if settings.get("lead_note"):
        lines.append(settings["lead_note"])
    return {"text": "\n".join(lines)}
