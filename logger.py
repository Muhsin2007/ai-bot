"""
Логирование с ротацией файлов.
Использование: from logger import log
"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name: str = "tat_auto") -> logging.Logger:
    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Не добавляем обработчики повторно (важно при hot-reload)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Файл: 10 МБ × 5 файлов = 50 МБ истории
    fh = RotatingFileHandler(
        "logs/bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)

    # Консоль
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


log = setup_logger()
