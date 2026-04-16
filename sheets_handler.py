"""
Google Sheets интеграция для TAT AUTO.

Сохраняет:
1. Лиды (новые клиенты)
2. Записи на тест-драйв
3. Покупки

Настройка:
1. Создайте Google Service Account: https://console.cloud.google.com
2. Скачайте JSON ключ и сохраните как 'google_credentials.json'
3. Создайте Google Sheets таблицу и дайте доступ service account
4. Добавьте GOOGLE_SHEET_ID в .env
"""

import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
CREDS_FILE = "google_credentials.json"

# Листы в таблице
SHEET_LEADS = "Лиды"
SHEET_TEST_DRIVES = "Тест-драйвы"
SHEET_PURCHASES = "Покупки"


def _get_sheets_client():
    """Возвращает клиент Google Sheets."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        if not os.path.exists(CREDS_FILE):
            logger.warning(f"Файл {CREDS_FILE} не найден. Google Sheets отключён.")
            return None

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        return gc
    except ImportError:
        logger.warning("gspread не установлен. Запустите: pip install gspread google-auth")
        return None
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None


def _get_or_create_sheet(gc, sheet_name: str):
    """Получает или создаёт лист в таблице."""
    try:
        spreadsheet = gc.open_by_key(SHEET_ID)
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except Exception:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
            # Добавляем заголовки
            headers = _get_headers(sheet_name)
            if headers:
                worksheet.append_row(headers)
        return worksheet
    except Exception as e:
        logger.error(f"Ошибка получения листа '{sheet_name}': {e}")
        return None


def _get_headers(sheet_name: str) -> list:
    headers_map = {
        SHEET_LEADS: ["Дата", "Имя", "Chat ID", "Первое сообщение", "Статус"],
        SHEET_TEST_DRIVES: ["Дата записи", "Имя", "Телефон", "Модель", "Дата/время", "Chat ID", "Статус"],
        SHEET_PURCHASES: ["Дата", "Имя", "Телефон", "Модель", "Сумма", "Тип оплаты", "Chat ID"],
    }
    return headers_map.get(sheet_name, [])


def _save_local_fallback(data: dict, category: str):
    """Сохраняет локально если Google Sheets недоступен."""
    filename = f"local_{category}.json"
    records = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception:
            records = []
    records.append(data)
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        logger.info(f"Сохранено локально в {filename}: {data}")
    except Exception as e:
        logger.error(f"Ошибка локального сохранения: {e}")


def save_test_drive(name: str, phone: str, model: str, datetime_str: str, chat_id: int):
    """Сохраняет запись на тест-драйв в Google Sheets."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    row = [now, name, phone, model, datetime_str, str(chat_id), "Новый"]

    data = {
        "date": now, "name": name, "phone": phone,
        "model": model, "datetime": datetime_str, "chat_id": chat_id
    }

    gc = _get_sheets_client()
    if not gc or not SHEET_ID:
        logger.warning("Google Sheets недоступен — сохраняю локально")
        _save_local_fallback(data, "test_drives")
        return

    ws = _get_or_create_sheet(gc, SHEET_TEST_DRIVES)
    if ws:
        ws.append_row(row)
        logger.info(f"Тест-драйв сохранён в Google Sheets: {name} — {model} — {datetime_str}")
    else:
        _save_local_fallback(data, "test_drives")


def save_lead(name: str, chat_id: int, first_message: str):
    """Сохраняет нового лида."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    row = [now, name, str(chat_id), first_message[:200], "Новый"]

    data = {
        "date": now, "name": name,
        "chat_id": chat_id, "first_message": first_message
    }

    gc = _get_sheets_client()
    if not gc or not SHEET_ID:
        _save_local_fallback(data, "leads")
        return

    ws = _get_or_create_sheet(gc, SHEET_LEADS)
    if ws:
        # Проверяем не существует ли уже
        try:
            existing = ws.col_values(3)  # колонка Chat ID
            if str(chat_id) in existing:
                return  # уже есть
        except Exception:
            pass
        ws.append_row(row)
        logger.info(f"Лид сохранён: {name} (chat {chat_id})")
    else:
        _save_local_fallback(data, "leads")


def save_purchase(name: str, phone: str, model: str, amount: int, payment_type: str, chat_id: int):
    """Сохраняет покупку."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    row = [now, name, phone, model, f"{amount:,} сум", payment_type, str(chat_id)]

    data = {
        "date": now, "name": name, "phone": phone,
        "model": model, "amount": amount, "payment_type": payment_type, "chat_id": chat_id
    }

    gc = _get_sheets_client()
    if not gc or not SHEET_ID:
        _save_local_fallback(data, "purchases")
        return

    ws = _get_or_create_sheet(gc, SHEET_PURCHASES)
    if ws:
        ws.append_row(row)
        logger.info(f"Покупка сохранена: {name} — {model}")
    else:
        _save_local_fallback(data, "purchases")


def get_all_test_drives() -> list:
    """Возвращает все записи на тест-драйв."""
    gc = _get_sheets_client()
    if not gc or not SHEET_ID:
        # Читаем локальные
        if os.path.exists("local_test_drives.json"):
            with open("local_test_drives.json", "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    ws = _get_or_create_sheet(gc, SHEET_TEST_DRIVES)
    if ws:
        return ws.get_all_records()
    return []


def update_lead_contact(chat_id: int, name: str, phone: str):
    """Обновляет имя и телефон существующего лида."""
    gc = _get_sheets_client()
    if not gc or not SHEET_ID:
        # Обновляем локально
        filename = "local_leads.json"
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    records = json.load(f)
                for r in records:
                    if str(r.get("chat_id")) == str(chat_id):
                        if name:
                            r["name"] = name
                        if phone:
                            r["phone"] = phone
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
                logger.info(f"Лид обновлён локально: {name} {phone}")
            except Exception as e:
                logger.error(f"Ошибка обновления лида локально: {e}")
        return

    ws = _get_or_create_sheet(gc, SHEET_LEADS)
    if not ws:
        return

    try:
        # Ищем строку с этим chat_id (колонка 3)
        col_values = ws.col_values(3)
        for i, val in enumerate(col_values):
            if val == str(chat_id):
                row_num = i + 1
                # Обновляем имя (колонка 2) и телефон (колонка 5 — добавим)
                if name:
                    ws.update_cell(row_num, 2, name)
                if phone:
                    ws.update_cell(row_num, 5, phone)
                logger.info(f"Лид обновлён в Sheets: {name} {phone}")
                return
    except Exception as e:
        logger.error(f"Ошибка обновления лида в Sheets: {e}")
