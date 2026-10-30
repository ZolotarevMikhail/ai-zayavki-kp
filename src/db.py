"""База SQLite: заявки, распознанные позиции, расчёты, история."""
import json
import sqlite3

from src.config import DB_PATH, UPLOADS_DIR
from werkzeug.utils import secure_filename

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    filename TEXT,
    raw_text TEXT,
    status TEXT,
    channel TEXT DEFAULT 'файл',
    contact_channel TEXT DEFAULT '',
    contact_value TEXT DEFAULT '',
    params_json TEXT,
    calc_json TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def get_connection() -> sqlite3.Connection:
    """Одно подключение с проверкой целостности схемы."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    _add_channel_column(connection)
    return connection


def _add_channel_column(connection: sqlite3.Connection) -> None:
    """Миграция для баз, созданных до появления каналов и контактов."""
    for statement in (
        "ALTER TABLE orders ADD COLUMN channel TEXT DEFAULT 'файл'",
        "ALTER TABLE orders ADD COLUMN contact_channel TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN contact_value TEXT DEFAULT ''",
    ):
        try:
            connection.execute(statement)
            connection.commit()
        except sqlite3.OperationalError:
            pass  # колонка уже есть — это нормально


def save_upload(file_storage) -> str:
    """Файл заявки кладём в data/uploads с безопасным именем."""
    safe_name = secure_filename(file_storage.filename) or "zayavka"
    target = UPLOADS_DIR / safe_name
    # Если файл с таким именем уже есть, дописываем счётчик
    counter = 1
    while target.exists():
        target = UPLOADS_DIR / f"{target.stem}_{counter}{target.suffix}"
        counter += 1
    file_storage.save(target)
    return str(target)


def create_order(
    filename: str, raw_text: str, params: list,
    channel: str = "файл", contact_value: str = "",
) -> int:
    """Новая заявка после распознавания. Возвращаем её номер."""
    connection = get_connection()
    cursor = connection.execute(
        "INSERT INTO orders (created_at, filename, raw_text, status,"
        " channel, contact_channel, contact_value, params_json, calc_json)"
        " VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, NULL)",
        (filename, raw_text, "на проверке", channel,
         channel if contact_value else "", contact_value,
         json.dumps(params, ensure_ascii=False)),
    )
    connection.commit()
    order_id = cursor.lastrowid
    connection.close()
    return order_id


def get_order(order_id: int) -> dict:
    """Одна заявка со распакованными полями или None."""
    connection = get_connection()
    row = connection.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    connection.close()
    if row is None:
        return None
    order = dict(row)
    order["params"] = json.loads(order["params_json"] or "[]")
    order["calc"] = (
        json.loads(order["calc_json"]) if order["calc_json"] else None
    )
    return order


def list_orders() -> list:
    """История заявок для главной страницы."""
    connection = get_connection()
    rows = connection.execute(
        "SELECT id, created_at, filename, status, channel,"
        " contact_channel, contact_value FROM orders ORDER BY id DESC"
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def update_params_and_calc(order_id: int, params: list, calc: dict) -> None:
    """Сотрудник проверил и подтвердил позиции: сохраняем и считаем."""
    connection = get_connection()
    connection.execute(
        "UPDATE orders SET params_json = ?, calc_json = ?,"
        " status = 'рассчитана'"
        " WHERE id = ?",
        (json.dumps(params, ensure_ascii=False),
         json.dumps(calc, ensure_ascii=False), order_id),
    )
    connection.commit()
    connection.close()


def add_session(token: str, role: str) -> None:
    """Вход с одного компьютера: запоминаем, чтобы знать число мест."""
    connection = get_connection()
    connection.execute(
        "INSERT INTO sessions (token, role, created_at)"
        " VALUES (?, ?, datetime('now'))",
        (token, role),
    )
    connection.commit()
    connection.close()


def session_role(token: str) -> str:
    """Роль входа по его метке: admin, staff или пустая строка.

    Сессия живёт 30 дней со входа, дальше «пустая»: место сотрудника
    не занято навсегда, а забытый вход не действует вечно.
    """
    if not token:
        return ""
    connection = get_connection()
    row = connection.execute(
        "SELECT role FROM sessions WHERE token = ?"
        " AND created_at >= datetime('now', '-30 days')",
        (token,)
    ).fetchone()
    connection.close()
    return row["role"] if row else ""


def drop_session(token: str) -> None:
    """Выход с компьютера: место освобождается."""
    if not token:
        return
    connection = get_connection()
    connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
    connection.commit()
    connection.close()


def staff_sessions_count() -> int:
    """Сколько компьютеров сотрудников сейчас вошло."""
    connection = get_connection()
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM sessions WHERE role = 'staff'"
    ).fetchone()
    connection.close()
    return row["total"]


def drop_staff_sessions() -> None:
    """Руководитель освобождает все места: компьютеры требуют вход заново."""
    connection = get_connection()
    connection.execute("DELETE FROM sessions WHERE role = 'staff'")
    connection.commit()
    connection.close()
