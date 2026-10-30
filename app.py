"""Веб-приложение «Заявка → КП»: загрузка, проверка, расчёт, документ.

Запуск: venv/bin/python app.py, потом открыть http://127.0.0.1:5000
"""
from pathlib import Path

import secrets
import time

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)

from src import (channels, config, db, email_in, excel_out, kp_text,
                 mailer, pricing, recognizer, render_kp)
from src import settings as kp_settings
from src import telegram_bot, vk_bot
from src import price_import as price_xlsx
from src.parsers import ALLOWED_SUFFIXES, parse_file


def _secret_key_or_none() -> str:
    """Ключ для всплывающих сообщений: из .env или постоянный случайный."""
    import os
    import secrets

    from src.config import DATA_DIR

    key = os.getenv("SECRET_KEY", "")
    if key:
        return key
    marker = DATA_DIR / "secret_key.txt"
    if marker.exists():
        return marker.read_text(encoding="utf-8")
    key = secrets.token_hex(16)
    marker.write_text(key, encoding="utf-8")
    return key


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # до 20 МБ на файл
app.secret_key = _secret_key_or_none()


@app.context_processor
def inject_globals():
    """Режим работы и роль видны на любой странице."""
    passwords_on = bool(config.ADMIN_PASSWORD or config.STAFF_PASSWORD)
    return {
        "demo_mode": recognizer.demo_mode(),
        "role": _current_role(),
        "passwords_on": passwords_on,
    }


def _access_configured() -> bool:
    """Вход по паролям включён, когда заданы пароли ролей в .env."""
    return bool(config.ADMIN_PASSWORD or config.STAFF_PASSWORD)


def _current_role() -> str:
    """Роль текущего входа: admin, staff или пусто (не вошёл).

    Если пароли в .env не заданы, работает режим MVP: вход не требуется,
    все видят прайс. Заказчик включает пароли, когда созреет.
    """
    if not _access_configured():
        return "admin"
    return db.session_role(request.cookies.get("kp_session", ""))


def _log_in(role: str):
    """Запоминаем вход на этом компьютере меткой в базе и в куки."""
    token = secrets.token_hex(16)
    db.add_session(token, role)
    response = redirect(url_for("index"))
    response.set_cookie("kp_session", token, httponly=True, samesite="Lax")
    return response


@app.before_request
def require_login():
    """Закрытые страницы только после входа; форма сайта и API открыты."""
    if not _access_configured() or request.endpoint == "static":
        return None
    path = request.path
    if (path.startswith(("/login", "/logout", "/api/", "/new"))
            or path == "/favicon.ico"):
        return None
    if not _current_role():
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    """Один пароль — одна роль: руководитель правит прайс, сотрудник нет."""
    if not _access_configured():
        return redirect(url_for("index"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if config.ADMIN_PASSWORD and password == config.ADMIN_PASSWORD:
            return _log_in("admin")
        if config.STAFF_PASSWORD and password == config.STAFF_PASSWORD:
            seats = kp_settings.load_settings().get(
                "staff_seats", config.STAFF_SEATS
            )
            if db.staff_sessions_count() >= int(seats):
                flash("Свободных мест нет: попросите руководителя"
                      " освободить место на странице прайса.")
                return redirect(url_for("login"))
            return _log_in("staff")
        flash("Пароль не подошёл.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    """Выход с этого компьютера: место освобождается."""
    db.drop_session(request.cookies.get("kp_session", ""))
    response = redirect(url_for("login"))
    response.delete_cookie("kp_session")
    return response


@app.route("/")
def index():
    """Главная: каналы приёма, список заявок (история) и форма загрузки."""
    orders = db.list_orders()
    pending = [order for order in orders if order["status"] == "на проверке"]
    context = {
        "orders": orders,
        "pending": pending,
        "pending_count": len(pending),
        "imap_ready": email_in.mail_configured(),
        "telegram_ready": telegram_bot.configured(),
        "vk_ready": vk_bot.configured(),
    }
    return render_template("index.html", **context)


@app.route("/upload", methods=["POST"])
def upload():
    """Принимаем файл, читаем, распознаём, создаём заявку на проверку."""
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("Выберите файл заявки.")
        return redirect(url_for("index"))
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        flash(f"Формат {suffix or 'файл'} не поддерживается.")
        return redirect(url_for("index"))

    path = db.save_upload(file)
    try:
        text = parse_file(path)
    except Exception as error:  # noqa: BLE001 — битый/неизвестный файл от человека
        flash(f"Файл не удалось прочитать ({error})."
              " Сохрани его в .xlsx/.docx/.pdf и попробуй снова.")
        return redirect(url_for("index"))
    if suffix in (".png", ".jpg", ".jpeg", ".webp") and recognizer.demo_mode():
        flash("Картинки распознаются только в реальном режиме:"
              " нужен ключ ProxyAPI.")
        return redirect(url_for("index"))
    try:
        positions = recognizer.extract_parameters(text, path)
    except (ValueError, RuntimeError) as error:
        flash(f"Распознавание не удалось: {error}")
        return redirect(url_for("index"))

    order_id = db.create_order(file.filename, text, positions)
    return redirect(url_for("review", order_id=order_id))


@app.route("/email/fetch", methods=["POST"])
def email_fetch():
    """Снимаем вложения с почты и пускаем их в общий конвейер заявок."""
    if not email_in.mail_configured():
        flash("Почта не подключена: заполните IMAP_HOST, IMAP_USER,"
              " IMAP_PASSWORD в .env.")
        return redirect(url_for("index"))
    try:
        files = email_in.fetch_attachments()
    except Exception as error:  # noqa: BLE001 — покажем понятную причину
        flash(f"Почта не отвечает: {error}")
        return redirect(url_for("index"))
    created = channels.process_files(files)
    message = (f"Из писем загружены заявки: {created}." if created
               else "Новых писем нет.")
    flash(message)
    return redirect(url_for("index"))


@app.route("/review/<int:order_id>", methods=["GET", "POST"])
def review(order_id):
    """Сотрудник проверяет распознанные данные и подтверждает расчёт."""
    order = db.get_order(order_id)
    if order is None:
        abort(404)
    if request.method == "POST":
        positions = _collect_positions(request.form)
        calc = pricing.calculate(positions)
        db.update_params_and_calc(order_id, positions, calc)
        return redirect(url_for("kp", order_id=order_id))
    order["contact_kind"] = channels.contact_kind(order["contact_value"])
    return render_template("review.html", order=order)


def _collect_positions(form, *_args) -> list:
    """Собираем отредактированные позиции обратно в список словарей."""
    positions = []
    index = 0
    while f"pos{index}_name" in form:
        raw_operations = form.get(f"pos{index}_operations", "").split(",")
        operations = [op.strip() for op in raw_operations if op.strip()]
        positions.append({
            "name": form.get(f"pos{index}_name", ""),
            "material": form.get(f"pos{index}_material", ""),
            "size": form.get(f"pos{index}_size", ""),
            "count": _safe_int(form.get(f"pos{index}_count", "0")),
            "operations": operations,
        })
        index += 1
    return positions


def _safe_int(value: str) -> int:
    """Количество: только целые, иначе 0 (страница проверки подсветит)."""
    digits = "".join(char for char in value or "" if char.isdigit())
    return int(digits) if digits else 0


@app.route("/price", methods=["GET", "POST"])
def price_page():
    """Страница прайса только для руководителей: материалы, работы, итоги.

    Сохранённые значения сразу применяются ко всем новым расчётам.
    Здесь же задаётся число рабочих мест (компьютеров) и освобождаются
    места сотрудников.
    """
    if _current_role() != "admin":
        flash("Прайс правят только руководители.")
        return redirect(url_for("index"))
    price = pricing.load_price_list()
    if request.method == "POST":
        price = _collect_price(request.form)
        pricing.save_price_list(price)
        flash("Прайс сохранён. Новые расчёты пойдут по этим ценам.")
        return redirect(url_for("price_page"))
    return render_template(
        "price.html",
        price=price,
        margin_percent=round((price["margin"] - 1) * 100, 1),
        seats=kp_settings.load_settings().get(
            "staff_seats", config.STAFF_SEATS
        ),
        staff_now=db.staff_sessions_count(),
    )


@app.route("/price/seats", methods=["POST"])
def price_seats():
    """Отдельная форма: только число мест, прайс не трогаем."""
    if _current_role() != "admin":
        flash("Места задаёт только руководитель.")
        return redirect(url_for("index"))
    _save_seats(request.form)
    flash("Число рабочих мест сохранено.")
    return redirect(url_for("price_page"))


def _collect_price(form: dict) -> dict:
    """Собираем правки прайса из страницы: материалы, работы, наценка."""
    materials = {}
    index = 0
    while f"m_name_{index}" in form:
        name = form.get(f"m_name_{index}", "").strip()
        if name:
            materials[name] = {
                "price_per_unit": _float_or_zero(form.get(f"m_price_{index}")),
                "unit": form.get(f"m_unit_{index}", "ед.").strip() or "ед.",
            }
        index += 1
    rates = {}
    index = 0
    while f"r_name_{index}" in form:
        name = form.get(f"r_name_{index}", "").strip()
        if name:
            rates[name] = _float_or_zero(form.get(f"r_value_{index}"))
        index += 1
    margin_percent = _float_or_zero(form.get("margin_percent"))
    return {
        "materials": materials,
        "rates": rates,
        "margin": 1 + margin_percent / 100,
        "delivery": _float_or_zero(form.get("delivery")),
    }


def _save_seats(form: dict) -> None:
    """Число компьютеров сотрудников храним с настройками компании."""
    seats = form.get("staff_seats", "").strip()
    settings = kp_settings.load_settings()
    if seats.isdigit():
        settings["staff_seats"] = int(seats)
    kp_settings.save_settings(settings)


def _float_or_zero(value: str) -> float:
    """Цена: число с запятой или точкой, иначе 0 (подсветится в расчёте)."""
    import re

    digits = re.sub(r"[^\d.,]", "", value or "").replace(",", ".")
    try:
        return round(float(digits), 2)
    except ValueError:
        return 0.0


@app.route("/price/import", methods=["POST"])
def price_import_page():
    """Прайс из Excel бухгалтерии: читаем файл и показываем предпросмотр."""
    if _current_role() != "admin":
        flash("Прайс правят только руководители.")
        return redirect(url_for("index"))
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("Выберите Excel-файл прайса.")
        return redirect(url_for("price_page"))
    if Path(file.filename).suffix.lower() != ".xlsx":
        flash("Нужен файл .xlsx: пересохраните выгрузку бухгалтерии "
              "в новом Excel.")
        return redirect(url_for("price_page"))
    token = secrets.token_hex(8)
    target = config.DATA_DIR / "tmp" / f"price_import_{token}.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    _clear_import_files(keep=target)
    file.save(target)
    try:
        imported = price_xlsx.parse_price(target)
    except Exception as error:  # noqa: BLE001 — покажем понятную причину
        flash(f"Файл не читается: {error}")
        target.unlink(missing_ok=True)
        return redirect(url_for("price_page"))
    if not imported["materials"] and not imported["rates"]:
        flash("В файле не нашлось таблицы с ценами: загрузите выгрузку "
              "прайса или заполните прайс руками.")
        target.unlink(missing_ok=True)
        return redirect(url_for("price_page"))
    preview = price_xlsx.diff(pricing.load_price_list(), imported)
    if not preview["has_changes"]:
        flash("Файл совпадает с действующим прайсом — менять нечего.")
        target.unlink(missing_ok=True)
        return redirect(url_for("price_page"))
    return render_template(
        "price_import.html", preview=preview, token=token
    )


def _clear_import_files(keep) -> None:
    """Старые неподтверждённые выгрузки убираем, оставляем текущую."""
    for old in config.DATA_DIR.joinpath("tmp").glob("price_import_*.xlsx"):
        if old != keep:
            old.unlink(missing_ok=True)


@app.route("/price/apply", methods=["POST"])
def price_apply():
    """Подтверждение предпросмотра: применяем прайс из Excel-файла."""
    if _current_role() != "admin":
        flash("Прайс правят только руководители.")
        return redirect(url_for("index"))
    import re

    token = request.form.get("token", "")
    if not re.fullmatch(r"[0-9a-f]{16}", token):
        flash("Загрузка не подтвердилась: проверьте файл заново.")
        return redirect(url_for("price_page"))
    path = config.DATA_DIR / "tmp" / f"price_import_{token}.xlsx"
    if not path.exists():
        flash("Файл не найден: загрузите прайс из Excel заново.")
        return redirect(url_for("price_page"))
    try:
        imported = price_xlsx.parse_price(path)
    except Exception as error:  # noqa: BLE001 — покажем понятную причину
        flash(f"Файл не читается: {error}")
        path.unlink(missing_ok=True)
        return redirect(url_for("price_page"))
    if not imported["materials"] and not imported["rates"]:
        flash("В файле не нашлось цен: прайс не менялся.")
        path.unlink(missing_ok=True)
        return redirect(url_for("price_page"))
    current = pricing.load_price_list()
    if imported["margin"] is None:
        imported["margin"] = current["margin"]
    if imported["delivery"] is None:
        imported["delivery"] = current["delivery"]
    pricing.save_price_list(imported)
    path.unlink(missing_ok=True)
    flash("Прайс загружен из Excel. Новые расчёты пойдут по этим ценам.")
    return redirect(url_for("price_page"))


@app.route("/price/download")
def price_download():
    """Руководитель получает прайс-лист файлом."""
    if _current_role() != "admin":
        flash("Прайс правят только руководители.")
        return redirect(url_for("index"))
    return send_file(config.PRICE_PATH, as_attachment=True)


@app.route("/price/free_seats", methods=["POST"])
def price_free_seats():
    """Освобождаем все места сотрудников: компьютеры потребуют вход."""
    if _current_role() != "admin":
        flash("Доступно только руководителям.")
        return redirect(url_for("index"))
    db.drop_staff_sessions()
    flash("Места сотрудников освобождены, на компьютерах нужен вход заново.")
    return redirect(url_for("price_page"))


@app.route("/kp/<int:order_id>")
def kp(order_id):
    """Готовый расчёт: таблица, итоги, ссылка на скачивание КП."""
    order = db.get_order(order_id)
    if order is None:
        abort(404)
    if order["calc"] is None:
        return redirect(url_for("review", order_id=order_id))
    return render_template("kp.html", order=order)


def _kp_order_or_redirect(order_id):
    """Общий вход для страниц КП: заявка есть и расчёт сделан."""
    order = db.get_order(order_id)
    if order is None:
        abort(404)
    if order["calc"] is None:
        return None, redirect(url_for("review", order_id=order_id))
    return order, None


@app.route("/kp/<int:order_id>/doc")
def kp_doc(order_id):
    """Окно КП: текст, таблица, отправка почтой/ Telegram/VK и печать."""
    order, redirect_back = _kp_order_or_redirect(order_id)
    if redirect_back is not None:
        return redirect_back
    settings = kp_settings.load_settings()
    order["contact_kind"] = channels.contact_kind(order["contact_value"])
    return render_template(
        "kp_doc.html",
        order=order,
        settings=settings,
        document=kp_text.build(order, settings),
        smtp_ready=mailer.smtp_configured(),
    )


@app.route("/kp/<int:order_id>/excel")
def kp_excel(order_id):
    """Excel-файл таблицы КП для клиента."""
    order, redirect_back = _kp_order_or_redirect(order_id)
    if redirect_back is not None:
        return redirect_back
    path = excel_out.build(order)
    return send_file(path, as_attachment=True)


@app.route("/kp/<int:order_id>/send", methods=["POST"])
def kp_send(order_id):
    """Отправляем КП файлами в чат Telegram или VK, откуда пришла заявка."""
    order, redirect_back = _kp_order_or_redirect(order_id)
    if redirect_back is not None:
        return redirect_back
    channel = ("Telegram" if request.form.get("channel") == "telegram"
               else "VK")
    flash(channels.send_kp(order, channel))
    return redirect(url_for("kp_doc", order_id=order_id))


@app.route("/kp/<int:order_id>/send_email", methods=["POST"])
def kp_send_email(order_id):
    """Письмо с КП: текст и автоматически приложенные Word и Excel."""
    order, redirect_back = _kp_order_or_redirect(order_id)
    if redirect_back is not None:
        return redirect_back
    address = request.form.get("to", "").strip()
    if "@" not in address:
        flash("Впишите правильный адрес почты заказчика.")
        return redirect(url_for("kp_doc", order_id=order_id))
    subject = request.form.get("subject", "").strip()
    letter_text = request.form.get("body", "").strip()
    word_path, excel_path = channels.kp_files(order)
    try:
        mailer.send_letter(
            address, subject, letter_text, [word_path, excel_path]
        )
        flash(f"Письмо с файлами Word и Excel отправлено на {address}.")
    except Exception as error:  # noqa: BLE001 — покажем понятную причину
        flash(f"Письмо не ушло: {error}")
        # Тема и текст — набранная оператором работа: вернём их на форму,
        # иначе при сбое сети он набирает письмо заново.
        return redirect(url_for("kp_doc", order_id=order_id,
                                keep_subj=subject, keep_body=letter_text))
    return redirect(url_for("kp_doc", order_id=order_id))


@app.route("/kp/<int:order_id>/download")
def download(order_id):
    """Генерируем DOCX коммерческого предложения и отдаём файл."""
    order = db.get_order(order_id)
    if order is None or order["calc"] is None:
        abort(404)
    path = render_kp.build_docx(order)
    return send_file(path, as_attachment=True)


# Лимитер публичной формы: не больше N заявок с одного адреса в час.
# Форма без входа — иначе её можно долбить и выжигать баланс ProxyAPI
# (каждая заявка в реальном режиме = платный вызов ИИ).
RATE_MAX, RATE_WINDOW = 10, 3600
_rate_hits = {}


def _too_fast() -> bool:
    """True — с этого адреса заявок за час уже RATE_MAX."""
    ip = request.remote_addr or "неизвестно"
    now = time.time()
    fresh = [t for t in _rate_hits.get(ip, []) if now - t < RATE_WINDOW]
    if len(fresh) >= RATE_MAX:
        _rate_hits[ip] = fresh
        return True
    _rate_hits[ip] = fresh + [now]
    return False


@app.route("/new", methods=["GET", "POST"])
def site_form():
    """Публичная форма заявки для вставки на сайт компании."""
    if request.method == "POST":
        if _too_fast():
            flash("Слишком много заявок с одного адреса."
                  " Напишите нам на почту или позвоните.")
            return redirect(url_for("site_form"))
        text = _form_text(request.form)
        if not text:
            flash("Опишите заявку текстом: что нужно сделать.")
            return redirect(url_for("site_form"))
        contact = request.form.get("client_contact", "").strip()
        try:
            order_id = channels.process_text(
                "Сайт", text, contact_value=contact
            )
        except RuntimeError as error:
            flash(f"Заявку не получилось обработать: {error}")
            return redirect(url_for("site_form"))
        return render_template("new_done.html", order_id=order_id)
    return render_template("new.html")


def _form_text(form) -> str:
    """Собираем текст заявки из полей формы: описание и имя клиента."""
    name = form.get("client_name", "").strip()
    description = form.get("description", "").strip()
    header = "Заявка с сайта."
    parts = [header, description]
    if name:
        parts.append(f"Клиент: {name}.")
    return "\n".join(parts)


@app.route("/api/new_order", methods=["POST"])
def api_new_order():
    """Приём заявки с внешнего сайта: JSON + заголовок X-Api-Key."""
    if not config.API_KEY:
        return {"error": "API отключён: ключ не задан в .env"}, 503
    if request.headers.get("X-Api-Key") != config.API_KEY:
        return {"error": "Ключ доступа неверный"}, 401
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return {"error": "Пустое поле text"}, 400
    contact = str(data.get("contact", "")).strip()
    try:
        order_id = channels.process_text(
            "API сайта", text, contact_value=contact
        )
    except RuntimeError as error:
        # Внешний сайт ждёт JSON, не страницу 500.
        return {"error": str(error)}, 502
    return {"order_id": order_id}, 201


@app.route("/reply/<int:order_id>", methods=["POST"])
def reply(order_id):
    """Ответ оператора заказчику через тот канал, откуда пришла заявка."""
    order = db.get_order(order_id)
    if order is None:
        abort(404)
    message = request.form.get("message", "").strip()
    if not message:
        flash("Напишите текст вопроса заказчику.")
        return redirect(url_for("review", order_id=order_id))
    result = channels.send_reply(order, message)
    if result:
        flash(result)
    else:
        fallback = order.get("contact_value") or "из карточки"
        flash(f"Прямой ответ по этому каналу не отправляется: "
              f"свяжитесь по реквизитам {fallback}.")
    return redirect(url_for("review", order_id=order_id))


@app.errorhandler(404)
def not_found(*_args):
    """Простая страница для несуществующих заявок."""
    return render_template("not_found.html"), 404


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
