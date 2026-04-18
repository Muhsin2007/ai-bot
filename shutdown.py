"""
Graceful shutdown + автоматический бэкап каждые 6 часов.

Использование в main.py:
    from shutdown import shutdown_event, setup_shutdown_handlers, backup_worker, finalize
    setup_shutdown_handlers()
    asyncio.create_task(backup_worker())
    ...
    await finalize(client)
"""
import asyncio
import logging
import os
import signal
import time
import zipfile
from datetime import datetime

log = logging.getLogger("tat_auto")

shutdown_event = asyncio.Event()

# Файлы для бэкапа (берём всё что существует)
_BACKUP_FILES = [
    "data/tat_auto.db",
    "client_data.json",
    "chat_history.json",
    "appointments.json",
    "summaries.json",
    "prices.json",
]

BACKUP_INTERVAL_H = 6
BACKUP_KEEP_DAYS  = 30


def setup_shutdown_handlers(loop=None):
    """Перехватывает SIGINT (Ctrl+C) и SIGTERM (kill) для корректного завершения.

    Принимает loop явно (передавать asyncio.get_running_loop() из main()).
    Это гарантирует что signal-handler использует правильный event loop
    и не зависит от asyncio.get_event_loop() в Python 3.10+.
    """
    # Захватываем loop один раз — при установке обработчиков, а не при сигнале
    _loop = loop
    if _loop is None:
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = asyncio.get_event_loop()

    def _on_signal(sig, frame):
        log.info("Сигнал %s — завершаю работу...", sig)
        # call_soon_threadsafe безопасен из потока сигнала
        try:
            _loop.call_soon_threadsafe(shutdown_event.set)
        except RuntimeError:
            shutdown_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    log.info("Обработчики завершения установлены (SIGINT, SIGTERM)")


async def backup_worker():
    """Фоновая задача: бэкап каждые 6 часов, хранит 30 дней."""
    os.makedirs("backups", exist_ok=True)

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=BACKUP_INTERVAL_H * 3600,
            )
            # Если shutdown — выходим
            break
        except asyncio.TimeoutError:
            pass  # Время вышло — делаем бэкап

        await _do_backup()


def _do_backup_sync(path: str):
    """Синхронная часть бэкапа — запускается в thread executor."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _BACKUP_FILES:
            if os.path.exists(f):
                z.write(f)
    size_kb = os.path.getsize(path) // 1024

    # Удаляем старше 30 дней
    cutoff  = time.time() - BACKUP_KEEP_DAYS * 86400
    removed = 0
    for fname in os.listdir("backups"):
        fpath = os.path.join("backups", fname)
        if fname.endswith(".zip") and os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            removed += 1
    return size_kb, removed


async def _do_backup():
    """Создаёт ZIP-архив с БД и JSON-файлами. Файловые операции вынесены в thread."""
    ts   = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = f"backups/{ts}.zip"
    try:
        # asyncio.to_thread не блокирует event loop во время архивации
        size_kb, removed = await asyncio.to_thread(_do_backup_sync, path)
        log.info("Бэкап создан: %s (%d КБ)", path, size_kb)
        if removed:
            log.info("Удалено старых бэкапов: %d", removed)
    except Exception as e:
        log.error("Ошибка бэкапа: %s", e)


async def finalize(client):
    """Вызывается при завершении — сохраняет состояние и отключается."""
    log.info("Финализация...")
    # Делаем последний бэкап
    await _do_backup()
    try:
        await client.disconnect()
    except Exception:
        pass
    log.info("Бот остановлен.")
