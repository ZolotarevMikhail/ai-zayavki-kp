"""Запуск каналов-ботов: Telegram и VK в фоновых потоках.

Основной сервер (app.py) должен быть запущен отдельно. Этот скрипт держит
живыми переписки Telegram и VK. Если канал не настроен (нет токена в .env),
он просто пропускается — никаких ошибок.
"""
import threading
import time

from src import telegram_bot, vk_bot

CHECK_PAUSE = 5


def run_telegram() -> None:
    """Цикл бота Telegram с защитой от падения всего потока."""
    while True:
        try:
            telegram_bot.run_forever()
        except Exception as error:  # noqa: BLE001 — перезапуск цикла
            print(f"Telegram-канал упал и будет перезапущен: {error}")
            time.sleep(CHECK_PAUSE)


def run_vk() -> None:
    """Цикл бота VK с той же защитой."""
    while True:
        try:
            vk_bot.run_forever()
        except Exception as error:  # noqa: BLE001 — перезапуск цикла
            print(f"VK-канал упал и будет перезапущен: {error}")
            time.sleep(CHECK_PAUSE)


def main() -> None:
    """Запуск настроенных каналов; экран объясняет, что живо."""
    threads = []
    if telegram_bot.configured():
        thread = threading.Thread(target=run_telegram, daemon=True)
        thread.start()
        threads.append(thread)
        print("Telegram: канал запущен.")
    else:
        print("Telegram не настроен: заполните TELEGRAM_BOT_TOKEN в .env.")

    if vk_bot.configured():
        thread = threading.Thread(target=run_vk, daemon=True)
        thread.start()
        threads.append(thread)
        print("VK: канал запущен.")
    else:
        print("VK не настроен: заполните VK_ACCESS_TOKEN"
              " и VK_GROUP_ID в .env.")

    if not threads:
        print("Ни один канал не настроен — процесс завершился.")
        return
    print("Каналы работают. Останов: Ctrl+C.")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
