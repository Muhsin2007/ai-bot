"""
convert_session.py — конвертирует agent_session.session в строку для сервера.
Запусти один раз локально: python convert_session.py
"""
import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")

if not API_ID or not API_HASH:
    print("ОШИБКА: задай TG_API_ID и TG_API_HASH в .env")
    raise SystemExit(1)

if not os.path.exists("agent_session.session"):
    print("ОШИБКА: файл agent_session.session не найден в текущей папке")
    raise SystemExit(1)

# Читаем существующую сессию и конвертируем в строку
with TelegramClient("agent_session", API_ID, API_HASH) as client:
    session_string = StringSession.save(client.session)

print("\n" + "=" * 60)
print("Скопируй строку ниже и добавь на сервер как TG_SESSION:")
print("=" * 60)
print()
print(f"TG_SESSION={session_string}")
print()
print("=" * 60)
print("Никому не показывай эту строку — это доступ к аккаунту!")
print("=" * 60)
