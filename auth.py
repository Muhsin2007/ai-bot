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
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon import functions
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE    = os.getenv("TG_PHONE", "")

if not API_ID or not API_HASH:
    print("ОШИБКА: задай TG_API_ID и TG_API_HASH в файле .env")
    raise SystemExit(1)

async def main():
    phone = PHONE or input("Номер телефона (+998XXXXXXXXX): ").strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    print("\nКуда отправить код?")
    print("  1 — В приложение Telegram")
    print("  2 — SMS / звонок")
    choice = input("Выбери 1 или 2: ").strip()

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    sent = await client.send_code_request(phone)

    if choice == "2":
        try:
            await client(functions.auth.ResendCodeRequest(
                phone_number=phone,
                phone_code_hash=sent.phone_code_hash
            ))
            print("Ожидай звонок или SMS!")
        except Exception as e:
            print(f"Не удалось запросить SMS/звонок: {e}")
            print("Используй код из приложения Telegram.")
    else:
        print("Открой Telegram — там сообщение от 'Telegram' с кодом.")

    code = input("\nВведи код: ").strip()

    try:
        await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
    except Exception as e:
        if "SessionPasswordNeeded" in str(type(e)):
            password = input("Введи пароль 2FA: ").strip()
            await client.sign_in(password=password)
        else:
            print(f"Ошибка: {e}")
            await client.disconnect()
            return

    session_string = client.session.save()
    await client.disconnect()

    print(f"\nTG_SESSION={session_string}\n")
    print("Добавь эту строку в .env на сервере. Никому не показывай!")

asyncio.run(main())