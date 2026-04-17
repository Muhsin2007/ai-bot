"""
auth.py — ЗАПУСТИ ОДИН РАЗ ЛОКАЛЬНО на своём компьютере.

Что делает:
  Авторизуется в Telegram (спрашивает телефон + код из SMS/приложения)
  и выводит строку сессии (TG_SESSION).

Эту строку нужно добавить как переменную окружения TG_SESSION на сервере
(Railway / Render / VPS / Docker Compose env).

Использование:
  python auth.py
"""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE    = os.getenv("TG_PHONE", "")    # можно заранее задать в .env

if not API_ID or not API_HASH:
    print("ОШИБКА: задай TG_API_ID и TG_API_HASH в файле .env")
    raise SystemExit(1)

print("=" * 55)
print("  TAT AUTO — генерация строки сессии Telegram")
print("=" * 55)
print("Введи номер телефона в формате +998XXXXXXXXX")
print("Затем введи код из Telegram (SMS или приложение)\n")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    client.start(phone=PHONE or None)
    session_string = client.session.save()

print("\n" + "=" * 55)
print("  ГОТОВО! Скопируй строку ниже:")
print("=" * 55)
print(f"\nTG_SESSION={session_string}\n")
print("=" * 55)
print("Добавь эту строку в переменные окружения на сервере.")
print("НЕ публикуй её в GitHub и не показывай никому!")
print("=" * 55)
