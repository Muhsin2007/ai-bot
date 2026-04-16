import asyncio
import os
import random
import time
import logging
import json
import re

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import User, DocumentAttributeAudio, DocumentAttributeVideo
from telethon.tl.functions.messages import SendMediaRequest, GetStickerSetRequest
from telethon.tl.types import (
    InputMediaGeoPoint, InputGeoPoint, InputMediaVenue, InputStickerSetShortName,
)

from ai_handler import (
    get_ai_reply, is_asking_location, is_asking_photo, is_asking_credit,
    is_asking_test_drive, is_declining_test_drive, is_asking_price_list, get_model_from_text,
    format_credit_info, mark_customer_interested, MODEL_PRICES,
    is_sticker, get_sticker_emoji, get_sticker_text_reply,
    analyze_sticker_emotion, analyze_text_mood, get_followup_message,
    detect_language, LANG_LABELS,
    analyze_writing_style, apply_human_typo, make_correction_message,
    is_saying_purchased, is_saying_not_interested,
    mark_customer_purchased, mark_customer_not_interested, is_followup_stopped,
    is_ready_to_buy, get_documents_request_message,
    analyze_document_photo, is_asking_nasiya_leasing,
    detect_gender_from_name, detect_gender_from_text, analyze_gender_from_photo,
    get_survey_message,
    detect_competitor, get_competitor_rebuttal,
    is_mentioning_tradein, get_hot_lead_score, HOT_LEAD_THRESHOLD,
    generate_conversation_summary, save_conversation_summary, get_conversation_summary,
)
from storage import load_history, add_message, get_chat_history, save_training_from_manager
from voice_handler import transcribe_voice, synthesize_voice
from sheets_handler import save_test_drive, save_lead, update_lead_contact

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

API_ID   = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")

# ========================
# НАСТРОЙКИ
# ========================
ONLY_PRIVATE               = True
DELAY_MIN                  = 6    # минимальная задержка (сек)
DELAY_MAX                  = 10   # максимальная задержка (сек)
DELAY_PER_CHAR             = 0.06 # +0.06 сек на каждый символ ответа (имитация набора)
IGNORE_CHATS               = []
PAUSE_AFTER_MANUAL_MINUTES = 0      # 0 = бот всегда отвечает, даже если человек уже написал
REPLY_WITH_VOICE           = True   # True = голосом если клиент написал голосом
VOICE_MAX_CHARS            = 300    # не синтезируем длинные ответы — только короткие

MANAGER_USERNAMES = ["@JavoxirTat"]

LOCATION_LAT = 41.271219
LOCATION_LON = 69.238094

# Стикерпаки — настройте в .env или здесь
# Укажите short_name из ссылки t.me/addstickers/ИМЯ
STICKER_PACKS = {
    "happy":    os.getenv("STICKER_PACK_HAPPY", ""),      # радость
    "greeting": os.getenv("STICKER_PACK_GREETING", ""),   # приветствие
    "car":      os.getenv("STICKER_PACK_CAR", ""),        # авто тематика
    "thumbsup": os.getenv("STICKER_PACK_APPROVE", ""),    # одобрение
    "excited":  os.getenv("STICKER_PACK_EXCITED", ""),    # воодушевление
}

# Дожим клиентов — настройки времени (в секундах)
FOLLOWUP_DELAY_1 =  3 * 3600        # 3 часа  — первый дожим
FOLLOWUP_DELAY_2 = 24 * 3600        # 24 часа — второй дожим
FOLLOWUP_DELAY_3 = 72 * 3600        # 72 часа — третий дожим
FOLLOWUP_PERIODIC_MIN = 3 * 86400   # 3 дня — минимум между периодическими
FOLLOWUP_PERIODIC_MAX = 4 * 86400   # 4 дня — максимум между периодическими
FOLLOWUP_MAX_STAGE    = 8           # стадии 4-8 = 5 периодических напоминаний (~2-3 недели)

PHOTOS = {
    "taishan":   ["photos/taishan_1.JPG", "photos/taishan_2.JPG", "photos/taishan_3.JPG"],
    "courage":   ["photos/courage_1.JPG", "photos/courage_2.JPG"],
    "m817":      ["photos/m817_1.JPG", "photos/m817_2.JPG", "photos/m817_3.JPG"],
    "free_plus": ["photos/free_plus_1.JPG", "photos/free_plus_2.JPG", "photos/free_plus_3.JPG"],
    "free_318":  ["photos/free_318_1.JPG", "photos/free_318_2.JPG", "photos/free_318_3.JPG"],
}

MODEL_NAMES = {
    "taishan":   "Voyah Taishan Max+",
    "courage":   "Voyah Courage 650",
    "m817":      "M-Hero M817",
    "free_plus": "Voyah Free+ 2026",
    "free_318":  "Voyah Free 318 2026",
}

# Файлы прайс-листа (первый найденный будет отправлен)
PRICE_LIST_FILES = [
    "price_list.pdf",
    "price_list.jpg",
    "price_list.png",
    "price_list.jpeg",
]

# Пересылка прайса из сохранённых сообщений @Deepaluz
# Запустите бота, зайдите в @Deepaluz, найдите нужное сообщение,
# скопируйте его ID (правой кнопкой → Копировать ссылку, последние цифры)
# и вставьте в .env: PRICE_LIST_MSG_ID=12345
PRICE_LIST_MSG_ID = int(os.getenv("PRICE_LIST_MSG_ID") or "0")

# Файл где хранится сохранённый прайс от менеджера
PRICE_LIST_CACHE_FILE = "price_list_cache.json"


def load_price_list_cache() -> dict:
    if os.path.exists(PRICE_LIST_CACHE_FILE):
        try:
            with open(PRICE_LIST_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_price_list_cache(chat_id: int, msg_id: int):
    data = {"chat_id": chat_id, "msg_id": msg_id}
    with open(PRICE_LIST_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
# ========================

history            = load_history()
manual_sent: dict[int, float] = {}
user_queues: dict[int, asyncio.Queue] = {}
user_tasks:  dict[int, asyncio.Task]  = {}
test_drive_sessions: dict[int, dict]  = {}
_startup_ts: float = 0.0

# ── Дедупликация сообщений ──
_processed_msg_ids: set[int] = set()
_PROCESSED_IDS_MAX = 500   # храним последние 500 ID

# Счётчик сообщений клиента без ответа бота
_unanswered_count: dict[int, int] = {}
_UNANSWERED_ALERT_THRESHOLD = 3

# Антиспам: время последнего исходящего сообщения боту клиенту
_last_bot_sent: dict[int, float] = {}
OUTGOING_COOLDOWN_MINUTES = 15   # не писать клиенту чаще раз в 15 минут (проактивно)

# ── In-memory кэш client_data (снижает I/O в 20-30 раз) ──
_client_data_cache: dict = {}
_client_data_dirty: bool = False

# Кэш стикеров: emotion -> список документов
_sticker_cache: dict[str, list] = {}
# Время последнего сообщения клиента для дожима
last_client_msg_time: dict[int, float] = {}

CLIENT_DATA_FILE = "client_data.json"


def _restore_last_msg_times():
    """При старте восстанавливаем время последнего сообщения из сохранённых данных."""
    data = load_client_data()
    for chat_id_str, info in data.items():
        chat_id = int(chat_id_str)
        # Приоритет: реальное время сообщения → время последнего дожима
        last_at = info.get("last_msg_at") or info.get("followup_sent_at")
        if last_at:
            last_client_msg_time[chat_id] = last_at
    logger.info(f"Восстановлено {len(last_client_msg_time)} записей времени для дожима")


# ─────────────────────────────────────────
# Данные клиентов
# ─────────────────────────────────────────

def load_client_data() -> dict:
    global _client_data_cache
    if _client_data_cache:
        return _client_data_cache
    if os.path.exists(CLIENT_DATA_FILE):
        try:
            with open(CLIENT_DATA_FILE, "r", encoding="utf-8") as f:
                _client_data_cache = json.load(f)
                return _client_data_cache
        except Exception:
            pass
    _client_data_cache = {}
    return _client_data_cache


def save_client_data(data: dict, force: bool = False):
    global _client_data_cache, _client_data_dirty
    _client_data_cache = data
    if force:
        _flush_client_data_sync()
    else:
        _client_data_dirty = True


def _flush_client_data_sync():
    global _client_data_dirty
    try:
        tmp = CLIENT_DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_client_data_cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CLIENT_DATA_FILE)
        _client_data_dirty = False
    except Exception as e:
        logger.error(f"Ошибка сохранения данных клиента: {e}")


async def _client_data_flush_worker():
    """Сбрасывает изменённый кэш на диск каждые 30 секунд."""
    while True:
        await asyncio.sleep(30)
        if _client_data_dirty:
            _flush_client_data_sync()


def get_client_info(chat_id: int) -> dict:
    data = load_client_data()
    return dict(data.get(str(chat_id), {}))   # копия — не мутируем кэш напрямую


def set_client_info(chat_id: int, force_save: bool = False, **kwargs):
    data = load_client_data()
    key  = str(chat_id)
    if key not in data:
        data[key] = {}
    data[key].update(kwargs)
    # Важные изменения (покупка, телефон, имя) — пишем на диск сразу
    important = {"phone", "real_name", "purchased", "survey_sent", "takeover_until"}
    force_save = force_save or bool(important & set(kwargs.keys()))
    save_client_data(data, force=force_save)


def is_new_client(chat_id: int) -> bool:
    info = get_client_info(chat_id)
    return not info.get("greeted", False)


def get_client_style_score(chat_id: int) -> float:
    """Возвращает накопленную оценку небрежности письма клиента (0.0–1.0)."""
    info = get_client_info(chat_id)
    return info.get("style_score", 0.0)


def update_client_style(chat_id: int, text: str) -> float:
    """
    Обновляет оценку стиля клиента через экспоненциальное скользящее среднее.
    Новый замер имеет вес 30%, история — 70%.
    """
    current   = get_client_style_score(chat_id)
    new_score = analyze_writing_style(text)
    smoothed  = current * 0.7 + new_score * 0.3
    set_client_info(chat_id, style_score=round(smoothed, 3))
    return smoothed


def get_client_lang(chat_id: int) -> str:
    """Возвращает текущий язык клиента (по умолчанию 'ru')."""
    info = get_client_info(chat_id)
    return info.get("lang", "ru")


def update_client_lang(chat_id: int, text: str) -> str:
    """
    Определяет язык текста и обновляет его для клиента если изменился.
    Возвращает актуальный код языка.
    """
    detected = detect_language(text)
    current  = get_client_lang(chat_id)
    if detected != current:
        set_client_info(chat_id, lang=detected)
        logger.info(f"[chat {chat_id}] Язык: {LANG_LABELS.get(current)} → {LANG_LABELS.get(detected)}")
    return detected


def get_client_gender(chat_id: int) -> str | None:
    """Возвращает 'm'/'f'/None если уверенность >= 0.55."""
    info = get_client_info(chat_id)
    score = info.get("gender_score", 0.0)   # положительный = мужской, отрицательный = женский
    if score >=  0.55:
        return "m"
    if score <= -0.55:
        return "f"
    return None


def add_gender_signal(chat_id: int, gender: str | None, confidence: float, source: str):
    """
    Добавляет сигнал пола.  gender='m'→+, 'f'→−, None→игнор.
    score хранится в [-1, 1].
    """
    if not gender or confidence <= 0:
        return
    info  = get_client_info(chat_id)
    score = info.get("gender_score", 0.0)
    delta = confidence if gender == "m" else -confidence
    # Экспоненциальное сглаживание: новый сигнал весит 40%
    new_score = round(score * 0.6 + delta * 0.4, 3)
    new_score = max(-1.0, min(1.0, new_score))
    set_client_info(chat_id, gender_score=new_score)
    resolved = "m" if new_score >= 0.55 else ("f" if new_score <= -0.55 else "?")
    logger.info(f"[gender] chat={chat_id} {source}: {gender} conf={confidence:.2f} → score={new_score:.2f} ({resolved})")


async def try_detect_gender_from_photo(client_tg, chat_id: int):
    """Скачивает аватар и анализирует пол через Claude Vision."""
    try:
        img_bytes = await client_tg.download_profile_photo(chat_id, file=bytes)
        if not img_bytes:
            return
        gender, conf = await asyncio.to_thread(analyze_gender_from_photo, img_bytes)
        if gender:
            add_gender_signal(chat_id, gender, conf, "photo")
    except Exception as e:
        logger.debug(f"[gender photo] chat={chat_id}: {e}")


# ─────────────────────────────────────────
# Профиль клиента (Feature 2)
# ─────────────────────────────────────────

_PURPOSE_KW = {
    "family":     ["семья", "дети", "ребёнок", "ребенок", "жена", "муж", "oila", "bola", "farzand"],
    "business":   ["работа", "офис", "бизнес", "фирма", "ish", "biznes", "ofis"],
    "long_trips": ["трасса", "дальн", "поездк", "регион", "yo'l", "uzoq", "safar"],
    "city":       ["город", "shahar", "city", "в городе"],
}
_FUEL_KW = {
    "electric": ["электр", "elektr", "electric", "зарядк"],
    "hybrid":   ["гибрид", "hybrid", "gibrid"],
}
_TIMELINE_KW = {
    "now":        ["сейчас", "сегодня", "hozir", "bugun", "срочно", "tez orada"],
    "soon":       ["месяц", "oy", "month", "скоро", "tez"],
    "researching":["думаю", "смотрю", "присматрива", "o'ylayman", "qarayapman"],
}
_OBJECTION_KW = {
    "price":      ["дорого", "дорог", "qimmat", "expensive", "дороговато"],
    "think":      ["подумаю", "подумать", "o'ylab", "посмотрю", "позже"],
    "competitor": ["конкурент", "другой салон", "boshqa", "alternative"],
    "uncertainty":["не уверен", "сомнева", "ishonch", "не знаю"],
}


def extract_profile_signals(text: str) -> dict:
    t = text.lower()
    signals = {}

    # Бюджет — ищем числа рядом с "млн/миллион"
    bm = re.search(r'(\d[\d\s]*)\s*(млн|миллион|million|mln)', t)
    if bm:
        signals["budget_hint"] = bm.group(0).strip()

    # Цель
    purposes = [p for p, kws in _PURPOSE_KW.items() if any(kw in t for kw in kws)]
    if purposes:
        signals["purposes"] = purposes

    # Топливо
    for fuel, kws in _FUEL_KW.items():
        if any(kw in t for kw in kws):
            signals["fuel_pref"] = fuel
            break

    # Сроки
    for tl, kws in _TIMELINE_KW.items():
        if any(kw in t for kw in kws):
            signals["timeline"] = tl
            break

    # Возражения
    objs = [o for o, kws in _OBJECTION_KW.items() if any(kw in t for kw in kws)]
    if objs:
        signals["objections"] = objs

    return signals


def update_client_profile(chat_id: int, text: str):
    signals = extract_profile_signals(text)
    if not signals:
        return
    info = get_client_info(chat_id)
    profile = info.get("sales_profile", {})

    if "budget_hint" in signals:
        profile["budget_hint"] = signals["budget_hint"]
    if "purposes" in signals:
        profile["purposes"] = list(set(profile.get("purposes", [])) | set(signals["purposes"]))
    if "fuel_pref" in signals:
        profile["fuel_pref"] = signals["fuel_pref"]
    if "timeline" in signals:
        profile["timeline"] = signals["timeline"]
    if "objections" in signals:
        profile["objections_raised"] = list(set(profile.get("objections_raised", [])) | set(signals["objections"]))

    set_client_info(chat_id, sales_profile=profile)


def get_formatted_profile(chat_id: int) -> str:
    info = get_client_info(chat_id)
    profile = info.get("sales_profile", {})
    if not profile:
        return ""

    purpose_map = {
        "family": "семья/дети", "business": "бизнес",
        "long_trips": "дальние поездки", "city": "город",
    }
    timeline_map = {
        "now": "готов купить сейчас",
        "soon": "планирует в ближайший месяц",
        "researching": "ещё присматривается",
    }
    objection_map = {
        "price": "говорил что дорого",
        "think": "хочет подумать",
        "competitor": "смотрит конкурентов",
        "uncertainty": "сомневается",
    }

    lines = ["[ПРОФИЛЬ КЛИЕНТА]"]
    if profile.get("budget_hint"):
        lines.append(f"Бюджет: {profile['budget_hint']}")
    if profile.get("purposes"):
        lines.append("Цель: " + ", ".join(purpose_map.get(p, p) for p in profile["purposes"]))
    if profile.get("fuel_pref"):
        lines.append(f"Предпочтение: {profile['fuel_pref']}")
    if profile.get("timeline"):
        lines.append(f"Сроки: {timeline_map.get(profile['timeline'], profile['timeline'])}")
    if profile.get("objections_raised"):
        lines.append("Возражения: " + ", ".join(objection_map.get(o, o) for o in profile["objections_raised"]))

    return "\n".join(lines) if len(lines) > 1 else ""


def has_phone(chat_id: int) -> bool:
    info = get_client_info(chat_id)
    return bool(info.get("phone"))


# ─────────────────────────────────────────
# Извлечение имени и телефона
# ─────────────────────────────────────────

def extract_phone(text: str) -> str | None:
    patterns = [
        r'(\+998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',
        r'(998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',
        r'(0\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',
        r'(\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            phone = re.sub(r'[\s\-]', '', match.group(1))
            if not phone.startswith('+'):
                if phone.startswith('998'):
                    phone = '+' + phone
                elif phone.startswith('0'):
                    phone = '+998' + phone[1:]
                else:
                    phone = '+998' + phone
            return phone
    return None


def extract_name(text: str) -> str | None:
    """Извлекает имя из текста и сохраняет его точно как написал клиент."""
    text = text.strip()
    if len(text.split()) <= 3 and not any(c.isdigit() for c in text):
        skip_words = {"меня", "зовут", "я", "мое", "моё", "имя", "это", "привет",
                      "здравствуйте", "салом", "salom", "ассалому", "алейкум",
                      "assalomu", "alaykum", "добрый", "день", "вечер", "утро"}
        words = [w for w in text.split() if w.lower() not in skip_words]
        if words:
            return " ".join(words).strip()   # сохраняем как есть, без изменения регистра

    patterns = [
        r'меня зовут\s+([А-ЯЁа-яёA-Za-z]+(?:\s+[А-ЯЁа-яёA-Za-z]+)?)',
        r'я\s+([А-ЯЁа-яёA-Za-z]{3,})',
        r'зовут\s+([А-ЯЁа-яёA-Za-z]+)',
        r'ismi[m]?\s+([A-Za-zА-ЯЁа-яё]+)',
        r'mening ismim\s+([A-Za-zА-ЯЁа-яё]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()    # сохраняем как есть
    return None


# ─────────────────────────────────────────
# Стикеры
# ─────────────────────────────────────────

async def load_sticker_pack(client, pack_name: str) -> list:
    """Загружает стикеры из пака по short_name."""
    if not pack_name:
        return []
    try:
        result = await client(GetStickerSetRequest(
            stickerset=InputStickerSetShortName(short_name=pack_name),
            hash=0,
        ))
        return result.documents
    except Exception as e:
        logger.warning(f"Не удалось загрузить стикерпак '{pack_name}': {e}")
        return []


async def preload_sticker_packs(client):
    """Предзагружает стикерпаки при старте."""
    for emotion, pack_name in STICKER_PACKS.items():
        if pack_name:
            docs = await load_sticker_pack(client, pack_name)
            if docs:
                _sticker_cache[emotion] = docs
                logger.info(f"Стикерпак '{pack_name}' загружен: {len(docs)} стикеров [{emotion}]")


async def send_sticker(client, chat_id: int, emotion: str = "happy") -> bool:
    """
    Отправляет случайный стикер из пака по эмоции.
    Возвращает True если стикер отправлен.
    Fallback: ищет в соседних эмоциях.
    """
    # Пробуем точное совпадение, потом fallback
    fallback_order = [emotion, "happy", "greeting", "thumbsup", "excited"]
    for emo in fallback_order:
        docs = _sticker_cache.get(emo)
        if docs:
            sticker = random.choice(docs)
            try:
                await client.send_file(chat_id, sticker)
                return True
            except Exception as e:
                logger.warning(f"Ошибка отправки стикера: {e}")
                return False
    return False  # Нет стикеров — отправитель должен использовать текст


# ─────────────────────────────────────────
# Прайс-лист
# ─────────────────────────────────────────

PRICE_LIST_TEXT = """📋 *ПРАЙС-ЛИСТ TAT AUTO*

🚗 *VOYAH COURAGE 650*
💰 473 000 000 сум
Электро | 650 км | 313 л.с. | Чёрный, Серый, Белый

🚗 *VOYAH FREE 318 2026*
💰 504 000 000 сум
Последовательный гибрид | 1200 км | 489 л.с. | Серый, Чёрный, Белый

🚗 *VOYAH FREE+ 2026*
💰 553 000 000 сум
Последовательный гибрид | 1357 км | 476 л.с. | Фисташка, Чёрный, Белый

🚙 *M-HERO M817*
💰 817 065 000 сум
Последовательный гибрид | 1300 км | 551 л.с. | Чёрный, Болотный

🏆 *VOYAH TAISHAN MAX+*
💰 от 1 060 965 000 сум
Гибрид PHEV | 1400 км | 517 л.с.

━━━━━━━━━━━━━━
✅ Гарантия: 3 года / 100 000 км
💳 Кредит от 22.9% (Банк ОФБ)
📍 г. Ташкент, ул. Шота Руставели 77

Хотите подробнее о любой модели или расчёт кредита? 😊"""


NOTIFY_MANAGER = "@Deepaluz"


async def notify_manager(client, subject: str, chat_id: int, tg_name: str,
                         tg_username: str | None, extra: str = ""):
    """Универсальная отправка уведомления менеджеру @Deepaluz."""
    info  = get_client_info(chat_id)
    name  = info.get("real_name") or tg_name
    phone = info.get("phone", "не указан")
    tg_link = f"@{tg_username}" if tg_username else f"tg://user?id={chat_id}"

    msg = (
        f"{subject}\n\n"
        f"👤 Имя: {name}\n"
        f"📱 Телефон: {phone}\n"
        f"💬 Telegram: {tg_link}\n"
        f"🆔 Chat ID: `{chat_id}`"
    )
    if extra:
        msg += f"\n{extra}"
    try:
        await client.send_message(NOTIFY_MANAGER, msg, parse_mode="md")
        logger.info(f"Менеджер уведомлён [{subject[:30]}] клиент {name} ({chat_id})")
    except Exception as e:
        logger.error(f"Ошибка уведомления менеджера: {e}")


async def notify_manager_ready_to_buy(client, chat_id: int, tg_name: str, tg_username: str | None):
    """Клиент готов к покупке и оформлению документов."""
    from ai_handler import load_customer_status
    cs    = load_customer_status()
    model = cs.get(str(chat_id), {}).get("interested_model", "не указано")
    model_display = MODEL_NAMES.get(model, model)
    await notify_manager(
        client,
        subject="🔥 *ГОРЯЧИЙ КЛИЕНТ — ГОТОВ К ПОКУПКЕ*",
        chat_id=chat_id, tg_name=tg_name, tg_username=tg_username,
        extra=f"🚗 Модель: {model_display}\n\n⚡️ Запросил оформление — свяжитесь как можно скорее!",
    )


async def notify_manager_test_drive(client, chat_id: int, tg_name: str, tg_username: str | None,
                                    model: str, dt: str, phone: str):
    """Клиент записался на тест-драйв."""
    await notify_manager(
        client,
        subject="🚗 *ЗАПИСЬ НА ТЕСТ-ДРАЙВ*",
        chat_id=chat_id, tg_name=tg_name, tg_username=tg_username,
        extra=f"🚗 Модель: {model}\n📅 Дата/время: {dt}\n📱 Телефон: {phone}",
    )


PRICE_CHANNEL   = "deeeepal"   # @deeeepal
PRICE_CHANNEL_MSG_ID = 3       # https://t.me/deeeepal/3

PRICES_FILE = "prices.json"

def load_price_summary() -> str:
    """Загружает текст цен из файла (менеджер обновляет через /цены)."""
    if os.path.exists(PRICES_FILE):
        try:
            with open(PRICES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("text", _DEFAULT_PRICE_TEXT)
        except Exception:
            pass
    return _DEFAULT_PRICE_TEXT

def save_price_summary(text: str):
    try:
        with open(PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump({"text": text, "updated_at": time.time()}, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения цен: {e}")

_DEFAULT_PRICE_TEXT = (
    "Актуальные цены:\n\n"
    "Voyah Courage 650 — 473 000 000 сум\n"
    "Voyah Free 318 2026 — 504 000 000 сум\n"
    "Voyah Free+ 2026 — 553 000 000 сум\n"
    "M-Hero M817 — 817 065 000 сум\n"
    "Voyah Taishan Max+ — от 1 060 965 000 сум\n\n"
    "По кредиту, рассрочке или любой модели — спрашивайте!"
)


async def send_price_list(client, chat_id: int, event=None):
    """Скачивает прайс из канала @deeeepal и отправляет без указания источника."""
    forwarded = False

    # Вариант 1: скачиваем файл из @deeeepal и отправляем напрямую (без "Forwarded from")
    try:
        msg = await client.get_messages(PRICE_CHANNEL, ids=PRICE_CHANNEL_MSG_ID)
        if msg and msg.media:
            file_bytes = await client.download_media(msg, file=bytes)
            await client.send_file(chat_id, file_bytes)
            forwarded = True
            logger.info(f"Прайс-лист отправлен из @{PRICE_CHANNEL}/3 (без пересылки)")
    except Exception as e:
        logger.warning(f"Не удалось получить прайс из @{PRICE_CHANNEL}: {e}")

    # Вариант 2 (fallback): кэш менеджера — тоже скачиваем и отправляем
    if not forwarded:
        cache    = load_price_list_cache()
        src_chat = cache.get("chat_id")
        src_msg  = cache.get("msg_id") or PRICE_LIST_MSG_ID
        if src_chat and src_msg:
            try:
                msg = await client.get_messages(src_chat, ids=src_msg)
                if msg and msg.media:
                    file_bytes = await client.download_media(msg, file=bytes)
                    await client.send_file(chat_id, file_bytes)
                    forwarded = True
                    logger.info(f"Прайс-лист отправлен из кэша")
            except Exception as e:
                logger.warning(f"Не удалось получить прайс из кэша: {e}")

    # Вариант 3 (fallback): локальный файл
    if not forwarded:
        for price_file in PRICE_LIST_FILES:
            if os.path.exists(price_file):
                try:
                    await client.send_file(chat_id, price_file)
                    forwarded = True
                    logger.info(f"Прайс-лист отправлен как файл: {price_file}")
                    break
                except Exception as e:
                    logger.error(f"Ошибка отправки файла прайса: {e}")

    # Всегда отправляем текст с ценами отдельным сообщением
    await client.send_message(chat_id, load_price_summary())
    logger.info(f"Текст цен отправлен в чат {chat_id}")


# ─────────────────────────────────────────
# Дожим клиентов
# ─────────────────────────────────────────

def get_followup_stage(chat_id: int) -> int:
    """Возвращает текущую стадию дожима клиента."""
    info = get_client_info(chat_id)
    return info.get("followup_stage", 0)


def set_followup_stage(chat_id: int, stage: int):
    """Сохраняет стадию дожима и вычисляет время следующего периодического."""
    now = time.time()
    kwargs = {"followup_stage": stage, "followup_sent_at": now}
    # Для периодических стадий (4+) — рандомим когда следующий раз
    if stage >= 4:
        next_delay = random.uniform(FOLLOWUP_PERIODIC_MIN, FOLLOWUP_PERIODIC_MAX)
        kwargs["followup_next_at"] = now + next_delay
    set_client_info(chat_id, **kwargs)


def should_send_followup(chat_id: int) -> int:
    """
    Проверяет нужно ли отправить дожим.
    Стадии 1-3: быстрые (3ч / 24ч / 72ч).
    Стадии 4-8: периодические раз в 3-4 дня.
    Возвращает номер стадии или 0 если не нужно.
    """
    info = get_client_info(chat_id)
    if not info.get("greeted"):
        return 0

    # Купил или отказался — дожим не нужен
    if is_followup_stopped(chat_id):
        return 0

    stage = info.get("followup_stage", 0)
    if stage >= FOLLOWUP_MAX_STAGE:
        return 0  # Все стадии пройдены

    last_msg = last_client_msg_time.get(chat_id, 0)
    if not last_msg:
        return 0

    now     = time.time()
    elapsed = now - last_msg
    sent_at = info.get("followup_sent_at", 0)

    # Быстрые стадии (первые 3 дня)
    if stage == 0 and elapsed >= FOLLOWUP_DELAY_1:
        return 1
    elif stage == 1 and elapsed >= FOLLOWUP_DELAY_2 and now - sent_at >= FOLLOWUP_DELAY_1:
        return 2
    elif stage == 2 and elapsed >= FOLLOWUP_DELAY_3 and now - sent_at >= FOLLOWUP_DELAY_2:
        return 3

    # Периодические стадии 4-8: раз в 3-4 дня
    elif stage >= 3:
        next_at = info.get("followup_next_at", 0)
        # Первый переход на периодику: если прошло 3 дня после стадии 3
        if stage == 3 and not next_at and now - sent_at >= FOLLOWUP_PERIODIC_MIN:
            return 4
        # Последующие: по расписанию next_at
        if next_at and now >= next_at:
            return stage + 1

    return 0


async def followup_worker(client):
    """Фоновая задача — проверяет и отправляет дожим каждые 15 минут."""
    logger.info("Дожим-воркер запущен")
    while True:
        await asyncio.sleep(15 * 60)  # проверяем каждые 15 минут
        try:
            data = load_client_data()
            for chat_id_str, info in data.items():
                chat_id = int(chat_id_str)

                if is_paused(chat_id):
                    continue

                stage = should_send_followup(chat_id)
                if stage == 0:
                    continue

                client_name = info.get("real_name") or info.get("tg_name", "")
                from ai_handler import load_customer_status
                cs = load_customer_status()
                cs_entry = cs.get(chat_id_str, {})
                interested_model = cs_entry.get("interested_model", "")
                lang = get_client_lang(chat_id)
                gender = get_client_gender(chat_id)
                profile = get_formatted_profile(chat_id)

                # Добавляем задачу узнать имя/телефон если не знаем
                missing = []
                if not info.get("real_name"):
                    missing.append("имя")
                if not info.get("phone"):
                    missing.append("номер телефона")
                if missing:
                    profile += f"\n[ЗАДАЧА: вежливо узнай {' и '.join(missing)} клиента]"

                try:
                    if not can_send_to_client(chat_id):
                        logger.info(f"[Антиспам] пропускаем {chat_id} — писали недавно")
                        continue
                    # Стадии 1-2: шаблонное сообщение; стадии 3+: AI генерирует живой текст
                    if stage <= 2:
                        msg = get_followup_message(stage, client_name, interested_model, lang)
                        await client.send_message(chat_id, msg)
                        mark_sent_to_client(chat_id)
                    else:
                        msgs = get_chat_history(history, chat_id)
                        # Добавляем подсказку от имени assistant чтобы AI продолжил
                        followup_msgs = msgs + [{
                            "role": "user",
                            "content": (
                                "[внутренняя задача: клиент молчит уже несколько дней. "
                                "Напиши ему вежливое сообщение — начни с приветствия 'Здравствуйте', "
                                "обратись по имени если оно известно, напомни о себе и задай "
                                "один важный вопрос связанный с его интересами.]"
                            )
                        }]
                        reply = await get_ai_reply(
                            followup_msgs, chat_id,
                            client_name=client_name, mood="neutral",
                            followup_stage=stage, lang=lang,
                            gender=gender, profile=profile,
                        )
                        if reply:
                            await client.send_message(chat_id, reply)
                            mark_sent_to_client(chat_id)
                            add_message(history, chat_id, "assistant", reply)
                        else:
                            # fallback на шаблон
                            msg = get_followup_message(stage, client_name, interested_model, lang)
                            await client.send_message(chat_id, msg)
                            mark_sent_to_client(chat_id)

                    set_followup_stage(chat_id, stage)
                    logger.info(f"[Дожим стадия {stage}] -> {client_name or chat_id_str}")
                except Exception as e:
                    logger.error(f"Ошибка отправки дожима [{chat_id}]: {e}")

            # ── Опрос после покупки (через 7 дней) ──
            try:
                import time as _time
                from ai_handler import load_customer_status, get_survey_message
                cs = load_customer_status()
                for chat_id_str, info in data.items():
                    chat_id = int(chat_id_str)
                    cs_entry = cs.get(chat_id_str, {})
                    purchased_at = cs_entry.get("purchased_at")
                    survey_sent = info.get("survey_sent", False)
                    if cs_entry.get("purchased") and purchased_at and not survey_sent:
                        days_since = (_time.time() - purchased_at) / 86400
                        if days_since >= 7:
                            lang = get_client_lang(chat_id)
                            model = cs_entry.get("purchased_model", "")
                            survey_msg = get_survey_message(lang, model)
                            try:
                                await client.send_message(chat_id, survey_msg)
                                set_client_info(chat_id, survey_sent=True)
                                logger.info(f"[Опрос] отправлен -> {chat_id_str}")
                            except Exception as e:
                                logger.error(f"Ошибка отправки опроса [{chat_id}]: {e}")
            except Exception as e:
                logger.error(f"Ошибка в опросе после покупки: {e}")

        except Exception as e:
            logger.error(f"Ошибка в followup_worker: {e}")


# ─────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────

def is_paused(chat_id: int) -> bool:
    if chat_id in manual_sent:
        if time.time() - manual_sent[chat_id] < PAUSE_AFTER_MANUAL_MINUTES * 60:
            return True
    return False


def is_taken_over(chat_id: int) -> bool:
    """Менеджер взял клиента на себя — бот молчит до истечения takeover_until."""
    info = get_client_info(chat_id)
    until = info.get("takeover_until", 0)
    return time.time() < until


async def get_display_name(event) -> str:
    try:
        sender = await event.get_sender()
        if isinstance(sender, User):
            parts = [sender.first_name or "", sender.last_name or ""]
            name  = " ".join(p for p in parts if p).strip()
            return name or "Клиент"
    except Exception:
        pass
    return "Клиент"


async def get_username(event) -> str | None:
    try:
        sender = await event.get_sender()
        if isinstance(sender, User) and sender.username:
            return f"@{sender.username}"
    except Exception:
        pass
    return None


async def should_handle(event) -> bool:
    if event.out:
        return False
    chat_id = event.chat_id
    if chat_id in IGNORE_CHATS:
        return False

    username = await get_username(event)
    if username and username in MANAGER_USERNAMES:
        return False

    try:
        chat = await event.get_chat()
        if hasattr(chat, "username") and chat.username:
            if f"@{chat.username}" in IGNORE_CHATS:
                return False
            if f"@{chat.username}" in MANAGER_USERNAMES:
                return False
    except Exception:
        pass

    # Только личные чаты
    if not event.is_private:
        return False

    if is_paused(chat_id):
        logger.info(f"[chat {chat_id}] Пауза после ручного ответа")
        return False
    if is_taken_over(chat_id):
        logger.info(f"[chat {chat_id}] Менеджер взял клиента — бот молчит")
        return False
    return True


def is_voice_message(event) -> bool:
    if not event.message.media:
        return False
    doc = getattr(event.message.media, "document", None)
    if not doc:
        return False
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeAudio) and attr.voice:
            return True
        if isinstance(attr, DocumentAttributeVideo) and getattr(attr, "round_message", False):
            return True
    return False


# ─────────────────────────────────────────
# Обучение на переписках менеджеров
# ─────────────────────────────────────────

async def learn_from_manager_chat(client, username: str):
    logger.info(f"Обучение на переписке с {username}...")
    try:
        entity = await client.get_entity(username)
        messages_buffer = []

        async for message in client.iter_messages(entity, limit=500):
            if message.text:
                messages_buffer.append({
                    "text": message.text,
                    "out": message.out,
                })

        messages_buffer.reverse()
        count = 0

        for i in range(len(messages_buffer) - 1):
            msg      = messages_buffer[i]
            next_msg = messages_buffer[i + 1]

            if not msg["out"] and next_msg["out"]:
                client_question = msg["text"]
                manager_answer  = next_msg["text"]

                if len(client_question) < 3 or len(manager_answer) < 5:
                    continue

                skip_phrases = ["уточню", "@deepaluz", "напишите напрямую"]
                if any(p in manager_answer.lower() for p in skip_phrases):
                    continue

                save_training_from_manager(client_question, manager_answer, source=username)
                count += 1

        logger.info(f"Обучение с {username}: {count} пар")
        return count
    except Exception as e:
        logger.error(f"Ошибка обучения на чате {username}: {e}")
        return 0


async def run_training(client):
    total = 0
    for username in MANAGER_USERNAMES:
        count = await learn_from_manager_chat(client, username)
        total += count
    logger.info(f"Обучение завершено. Всего: {total} примеров.")


async def load_old_chats(client, limit_per_chat: int = 100, max_chats: int = 500):
    """
    При старте обходит все личные диалоги и загружает историю сообщений в память бота.
    Это позволяет боту знать о клиентах, с которыми общался раньше.
    """
    logger.info("Загрузка истории старых чатов...")
    loaded = 0
    skipped = 0

    async for dialog in client.iter_dialogs(limit=max_chats):
        # Только личные чаты с живыми людьми
        if not dialog.is_user:
            continue
        entity = dialog.entity
        if getattr(entity, "bot", False):
            continue  # пропускаем ботов
        if getattr(entity, "deleted", False):
            continue

        chat_id = dialog.id

        # Если уже загружено — пропускаем, не перезаписываем
        if get_chat_history(history, chat_id):
            skipped += 1
            continue

        try:
            msgs = []
            async for msg in client.iter_messages(chat_id, limit=limit_per_chat):
                if not msg.text:
                    continue
                role = "assistant" if msg.out else "user"
                msgs.append((msg.date.timestamp(), role, msg.text))

            if not msgs:
                continue

            msgs.sort(key=lambda x: x[0])  # хронологически

            for _, role, text in msgs:
                add_message(history, chat_id, role, text)

            # Обновляем client_data если клиент ещё не зарегистрирован
            name_parts = [entity.first_name or "", entity.last_name or ""]
            tg_name = " ".join(p for p in name_parts if p).strip() or "Клиент"
            tg_username = f"@{entity.username}" if getattr(entity, "username", None) else None
            last_ts = msgs[-1][0]

            existing = get_client_info(chat_id)
            update_kwargs = {"tg_name": tg_name, "last_msg_at": last_ts}
            if tg_username:
                update_kwargs["tg_username"] = tg_username
            if not existing.get("greeted"):
                update_kwargs["greeted"] = True
            set_client_info(chat_id, **update_kwargs)

            loaded += 1
        except Exception as e:
            logger.warning(f"Не удалось загрузить чат {chat_id}: {e}")

    logger.info(f"Старые чаты загружены: {loaded} новых, {skipped} уже были в памяти.")


async def reply_to_missed_messages(client, hours: int = 5):
    """
    При старте находит все личные чаты, где клиент написал нам за последние N часов,
    а мы так и не ответили. Анализирует сообщения через AI и отвечает.
    """
    import time as _t
    cutoff = _t.time() - hours * 3600
    logger.info(f"Поиск пропущенных сообщений за последние {hours}ч...")
    replied = 0

    async for dialog in client.iter_dialogs(limit=500):
        if not dialog.is_user:
            continue
        entity = dialog.entity
        if getattr(entity, "bot", False):
            continue
        if getattr(entity, "deleted", False):
            continue

        chat_id = dialog.id

        # Пропускаем клиентов под takeover
        if is_taken_over(chat_id):
            continue

        # Последнее сообщение в диалоге — если оно от нас, значит уже ответили
        last_msg = dialog.message
        if not last_msg:
            continue
        if last_msg.out:
            continue  # последнее — наше → уже ответили

        msg_ts = last_msg.date.timestamp()
        if msg_ts < cutoff:
            continue  # написал больше N часов назад — не трогаем

        # Собираем все подряд сообщения от клиента (с момента нашего последнего ответа)
        missed_texts = []
        try:
            async for msg in client.iter_messages(chat_id, limit=30):
                if msg.date.timestamp() < cutoff:
                    break
                if msg.out:
                    break  # дошли до нашего сообщения — стоп
                if msg.text:
                    missed_texts.append(msg.text)
        except Exception as e:
            logger.warning(f"Ошибка чтения пропущенных [{chat_id}]: {e}")
            continue

        if not missed_texts:
            continue

        missed_texts.reverse()  # хронологически

        # Добавляем пропущенные сообщения в историю
        for txt in missed_texts:
            add_message(history, chat_id, "user", txt)

        combined = " | ".join(missed_texts)
        logger.info(f"[Пропущено] chat={chat_id} msg='{combined[:80]}'")

        # Получаем данные клиента
        info = get_client_info(chat_id)
        name_parts = [entity.first_name or "", entity.last_name or ""]
        tg_name = " ".join(p for p in name_parts if p).strip() or "Клиент"
        real_name = info.get("real_name") or tg_name
        lang = info.get("lang", "ru")
        gender = get_client_gender(chat_id)

        # Обновляем профиль по пропущенным сообщениям
        for txt in missed_texts:
            try_extract_contact(chat_id, txt)
            update_client_profile(chat_id, txt)
            detected_lang = detect_language(txt)
            if detected_lang != lang:
                set_client_info(chat_id, lang=detected_lang)
                lang = detected_lang

        # Перечитываем после обновления
        info = get_client_info(chat_id)
        real_name = info.get("real_name") or tg_name
        profile = get_formatted_profile(chat_id)

        # Добавляем в профиль задачу узнать имя/телефон если не знаем
        missing = []
        if not info.get("real_name"):
            missing.append("имя")
        if not info.get("phone"):
            missing.append("номер телефона")
        if missing:
            profile += f"\n[ЗАДАЧА: вежливо узнай {' и '.join(missing)} клиента в ходе диалога]"

        messages = get_chat_history(history, chat_id)

        try:
            # Небольшая задержка между ответами чтобы не спамить разом
            await asyncio.sleep(random.uniform(2, 5))

            async with client.action(chat_id, "typing"):
                reply = await get_ai_reply(
                    messages, chat_id,
                    client_name=real_name, mood="neutral",
                    lang=lang, gender=gender, profile=profile
                )
                if reply:
                    delay = min(DELAY_MIN + len(reply) * DELAY_PER_CHAR + random.uniform(0, 1.5), DELAY_MAX)
                    await asyncio.sleep(delay)

            if reply:
                # Кредит — добавляем таблицу к ответу
                model_key = get_model_from_text(combined)
                if model_key:
                    mark_customer_interested(chat_id, model_key)
                if is_asking_credit(combined) and model_key and model_key in MODEL_PRICES:
                    set_client_info(chat_id, asked_credit=True)
                    reply += "\n\n" + format_credit_info(MODEL_PRICES[model_key], 30, 36)

                if not can_send_to_client(chat_id, cooldown_minutes=30):
                    logger.info(f"[Антиспам] пропускаем missed {chat_id}")
                    continue
                await client.send_message(chat_id, reply)
                mark_sent_to_client(chat_id)
                add_message(history, chat_id, "assistant", reply)
                set_client_info(chat_id, last_msg_at=_t.time())
                logger.info(f"[Пропущено -> ответил] {real_name}: {reply[:80]}")
                replied += 1

                # Прайс-лист
                if is_asking_price_list(combined):
                    set_client_info(chat_id, asked_price=True)
                    await asyncio.sleep(1)
                    await send_price_list(client, chat_id)

                # Локация
                if is_asking_location(combined):
                    set_client_info(chat_id, asked_location=True)
                    await asyncio.sleep(1)
                    await send_location(client, chat_id)

                # Фото модели
                if is_asking_photo(combined) and model_key:
                    await asyncio.sleep(1)
                    await send_photos(client, chat_id, model_key, real_name)

        except Exception as e:
            logger.error(f"Ошибка ответа на пропущенное [{chat_id}]: {e}")

    logger.info(f"Пропущенные сообщения обработаны: ответил {replied} клиентам.")


# ─────────────────────────────────────────
# Утренний брифинг менеджеру
# ─────────────────────────────────────────

async def morning_brief_worker(client):
    """Каждый день в 9:00 отправляет @Deepaluz сводку по клиентам."""
    import datetime
    sent_date: str = ""

    while True:
        await asyncio.sleep(60)   # проверяем каждую минуту
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")

        if now.hour != 9 or sent_date == today:
            continue

        sent_date = today
        try:
            from ai_handler import load_customer_status, is_followup_stopped
            all_data   = load_client_data()
            cs         = load_customer_status()
            now_ts     = time.time()

            new_overnight, hot, cold, testdrives_today = [], [], [], []

            for cid_str, info in all_data.items():
                if not info.get("greeted"):
                    continue
                cid        = int(cid_str)
                cs_entry   = cs.get(cid_str, {})
                last_msg   = info.get("last_msg_at", 0)
                name       = info.get("real_name") or info.get("tg_name", cid_str)
                phone      = info.get("phone", "")
                model      = MODEL_NAMES.get(cs_entry.get("interested_model",""), "")
                label      = f"• {name}" + (f" ({phone})" if phone else f" [id:{cid_str}]")
                if model:
                    label += f" — {model}"

                # Написали за последние 8 часов (ночь)
                if last_msg and now_ts - last_msg < 8 * 3600:
                    new_overnight.append(label)

                # Горячие лиды
                if get_hot_lead_score(info) >= HOT_LEAD_THRESHOLD and not cs_entry.get("purchased"):
                    hot.append(label)

                # Молчат 3+ дней и не купили
                if (last_msg and now_ts - last_msg > 3 * 86400
                        and not cs_entry.get("purchased")
                        and not is_followup_stopped(cid)):
                    cold.append(label)

                # Тест-драйв записан
                if info.get("asked_testdrive") and not cs_entry.get("purchased"):
                    testdrives_today.append(label)

            lines = [f"☀️ *Доброе утро! Сводка TAT AUTO на {now.strftime('%d.%m.%Y')}*\n"]

            if new_overnight:
                lines.append(f"🌙 *Писали ночью ({len(new_overnight)}):*")
                lines += new_overnight[:10]
            if hot:
                lines.append(f"\n🔥 *Горячие лиды ({len(hot)}) — позвонить сегодня:*")
                lines += hot[:10]
            if testdrives_today:
                lines.append(f"\n🚗 *Записаны на тест-драйв ({len(testdrives_today)}):*")
                lines += testdrives_today[:10]
            if cold:
                lines.append(f"\n🧊 *Молчат 3+ дней ({len(cold)}) — нужен контакт:*")
                lines += cold[:10]

            if len(lines) == 1:
                lines.append("Всё спокойно, новых активностей нет.")

            lines.append(f"\n/stats — полная статистика")

            await client.send_message(NOTIFY_MANAGER, "\n".join(lines), parse_mode="md")
            logger.info(f"[Брифинг] отправлен на {today}")

        except Exception as e:
            logger.error(f"[Брифинг] ошибка: {e}")


# ─────────────────────────────────────────
# Каталог моделей (текстовые кнопки)
# ─────────────────────────────────────────

_MODEL_CATALOG_KEYWORDS = [
    "какие модели", "все модели", "что есть", "ассортимент", "каталог",
    "какие авто", "какие машины", "что у вас", "что имеется", "покажи модели",
    "qanday modellar", "barcha modellar", "nima bor", "katalog",
    "what models", "show models", "what cars",
]

_MODEL_CATALOG_TEXT = {
    "ru": (
        "У нас в наличии следующие модели:\n\n"
        "1️⃣  Voyah Free 318 (2026) — 318 л.с., пневмоподвеска, от {price_318}\n"
        "2️⃣  Voyah Free+ (2026) — мощный SUV, от {price_freeplus}\n"
        "3️⃣  Voyah Taishan Max+ — флагманский пикап, от {price_taishan}\n"
        "4️⃣  Voyah Courage 650 — кроссовер, от {price_courage}\n"
        "5️⃣  M-Hero M817 — внедорожник премиум, от {price_m817}\n\n"
        "Напишите номер или название модели — расскажу подробнее, покажу фото и посчитаю кредит."
    ),
    "uz": (
        "Bizda quyidagi modellar mavjud:\n\n"
        "1️⃣  Voyah Free 318 (2026) — 318 ot kuchi, pnevmotizimli, {price_318} dan\n"
        "2️⃣  Voyah Free+ (2026) — kuchli SUV, {price_freeplus} dan\n"
        "3️⃣  Voyah Taishan Max+ — flagman pikap, {price_taishan} dan\n"
        "4️⃣  Voyah Courage 650 — krossover, {price_courage} dan\n"
        "5️⃣  M-Hero M817 — premium yoʻl tanlamaydi, {price_m817} dan\n\n"
        "Raqam yoki model nomini yozing — batafsil aytib beraman, rasm va kredit hisobini koʻrsataman."
    ),
    "en": (
        "Our current lineup:\n\n"
        "1️⃣  Voyah Free 318 (2026) — 318 hp, air suspension, from {price_318}\n"
        "2️⃣  Voyah Free+ (2026) — powerful SUV, from {price_freeplus}\n"
        "3️⃣  Voyah Taishan Max+ — flagship pickup, from {price_taishan}\n"
        "4️⃣  Voyah Courage 650 — crossover, from {price_courage}\n"
        "5️⃣  M-Hero M817 — premium off-roader, from {price_m817}\n\n"
        "Type a number or model name and I'll give you full details, photos and a credit calculation."
    ),
}

# Быстрый выбор по номеру из каталога
_CATALOG_NUMBER_MAP = {
    "1": "free_318", "1️⃣": "free_318",
    "2": "free_plus", "2️⃣": "free_plus",
    "3": "taishan",   "3️⃣": "taishan",
    "4": "courage",   "4️⃣": "courage",
    "5": "m817",      "5️⃣": "m817",
}


def is_asking_catalog(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in _MODEL_CATALOG_KEYWORDS)


def get_catalog_text(lang: str = "ru") -> str:
    from ai_handler import MODEL_PRICES
    def fmt(p): return f"{p//1_000_000}млн" if p >= 1_000_000 else f"{p//1_000}тыс"
    tpl = _MODEL_CATALOG_TEXT.get(lang, _MODEL_CATALOG_TEXT["ru"])
    return tpl.format(
        price_318     = fmt(MODEL_PRICES.get("free_318", 0)),
        price_freeplus= fmt(MODEL_PRICES.get("free_plus", 0)),
        price_taishan = fmt(MODEL_PRICES.get("taishan", 0)),
        price_courage = fmt(MODEL_PRICES.get("courage", 0)),
        price_m817    = fmt(MODEL_PRICES.get("m817", 0)),
    )


# ─────────────────────────────────────────
# Тест-драйв
# ─────────────────────────────────────────

async def handle_test_drive_flow(client, event, chat_id: int, text: str, name: str):
    session = test_drive_sessions.get(chat_id, {})
    step = session.get("step", 0)

    if step == 0:
        known = get_client_info(chat_id)
        known_name = known.get("real_name") or name
        known_phone = known.get("phone", "")

        from ai_handler import load_customer_status
        cs = load_customer_status()
        known_model_key = cs.get(str(chat_id), {}).get("interested_model", "")
        known_model = MODEL_NAMES.get(known_model_key, "") if known_model_key else ""

        session = {"name": known_name}

        if known_model:
            session["model"] = known_model
            session["step"]  = 2   # сразу к телефону
            test_drive_sessions[chat_id] = session
            hint = f"\n\nУ нас записан номер: {known_phone}\nПодтвердите или введите актуальный:" if known_phone else "\n\nВаш номер телефона для подтверждения записи?"
            await event.reply(f"Запишу вас на тест-драйв {known_model}!{hint}")
        else:
            session["step"] = 1   # спрашиваем модель
            test_drive_sessions[chat_id] = session
            await event.reply(
                f"Запишу вас на тест-драйв!\n\n"
                f"👤 {known_name}\n"
                f"Какую модель хотите попробовать?\n\n"
                f"Voyah Courage 650\n"
                f"Voyah Free+ 2026\n"
                f"Voyah Free 318 2026\n"
                f"M-Hero M817\n"
                f"Voyah Taishan Max+"
            )
    elif step == 1:
        # Получили модель — спрашиваем телефон
        session["model"] = text
        session["step"]  = 2
        test_drive_sessions[chat_id] = session
        known_phone = get_client_info(chat_id).get("phone", "")
        hint = f"У нас записан номер: {known_phone}\nПодтвердите или введите актуальный:" if known_phone else "Ваш номер телефона для подтверждения записи?"
        await event.reply(hint)
    elif step == 2:
        # Валидируем телефон
        phone = extract_phone(text)
        if not phone:
            await event.reply("Не смог распознать номер. Введите в формате +998 XX XXX XX XX")
            return  # остаёмся на шаге 2
        session["phone"] = phone
        session["step"]  = 3
        test_drive_sessions[chat_id] = session
        set_client_info(chat_id, phone=phone)
        await event.reply("Принято! Какая дата и время вам удобны?")
    elif step == 3:
        session["datetime"] = text
        session["step"]     = 0
        del test_drive_sessions[chat_id]

        await asyncio.to_thread(
            save_test_drive,
            name=session.get("name", name),
            phone=session.get("phone", ""),
            model=session.get("model", ""),
            datetime_str=text,
            chat_id=chat_id,
        )

        await event.reply(
            f"✅ *Тест-драйв записан!*\n\n"
            f"👤 {session.get('name', name)}\n"
            f"🚗 {session.get('model', '')}\n"
            f"📱 {session.get('phone', '')}\n"
            f"📅 {text}\n\n"
            f"Менеджер подтвердит запись. Ждём вас:\n"
            f"📍 г. Ташкент, ул. Шота Руставели 77"
        )

        # Уведомляем менеджера о записи на тест-драйв
        tg_username_raw = getattr(await event.get_sender(), "username", None)
        asyncio.create_task(notify_manager_test_drive(
            client, chat_id=chat_id,
            tg_name=session.get("name", name),
            tg_username=tg_username_raw,
            model=session.get("model", ""),
            dt=text,
            phone=session.get("phone", ""),
        ))


# ─────────────────────────────────────────
# Медиа
# ─────────────────────────────────────────

async def send_location(client, chat_id: int):
    try:
        await client(SendMediaRequest(
            peer=await client.get_input_entity(chat_id),
            media=InputMediaVenue(
                geo_point=InputGeoPoint(lat=LOCATION_LAT, long=LOCATION_LON),
                title="TAT AUTO",
                address="г. Ташкент, ул. Шота Руставели 77",
                provider="",
                venue_id="",
                venue_type="",
            ),
            message="",
            random_id=random.randint(1, 2**63),
        ))
    except Exception as e:
        logger.error(f"Ошибка отправки локации: {e}")


async def send_photos(client, chat_id: int, model_key: str | None, name: str):
    try:
        if model_key and model_key in PHOTOS:
            photos     = [p for p in PHOTOS[model_key] if os.path.exists(p)]
            model_name = MODEL_NAMES.get(model_key, model_key)

            if photos:
                for i, photo in enumerate(photos, 1):
                    await client.send_file(chat_id, photo, caption=f"📸 {model_name} — {i}/{len(photos)}")
                    await asyncio.sleep(0.8)
            else:
                await client.send_message(chat_id, f"📸 Фото {model_name} скоро добавим! Приглашаем в салон 🚗")
        else:
            sent = 0
            for mk, paths in PHOTOS.items():
                existing = [p for p in paths if os.path.exists(p)]
                if existing:
                    await client.send_file(chat_id, existing[0], caption=f"🚗 {MODEL_NAMES.get(mk, mk)}")
                    await asyncio.sleep(0.8)
                    sent += 1
            if sent == 0:
                await client.send_message(chat_id, "📸 Приглашаем в салон!\n📍 г. Ташкент, ул. Шота Руставели 77")
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")


# ─────────────────────────────────────────
# Голос
# ─────────────────────────────────────────

async def handle_voice(event, client, chat_id: int, name: str) -> str | None:
    voice_path = f"temp_voice_{chat_id}_{int(time.time())}.ogg"
    try:
        await client.download_media(event.message, file=voice_path)
        text = await asyncio.to_thread(transcribe_voice, voice_path)
        return text
    except Exception as e:
        logger.error(f"Ошибка голосового: {e}")
        await event.reply("Не смог распознать голосовое. Напишите текстом 🙏")
        return None
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)


# ─────────────────────────────────────────
# Документы клиента (фото паспорта / прописки)
# ─────────────────────────────────────────

def is_photo_message(event) -> bool:
    """True если сообщение содержит фото или документ-изображение/PDF."""
    msg = event.message
    if not msg.media:
        return False
    # Telethon: MessageMediaPhoto
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
    if isinstance(msg.media, MessageMediaPhoto):
        return True
    if isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        mime = getattr(doc, "mime_type", "") or ""
        return mime.startswith("image/") or mime == "application/pdf"
    return False


# Сообщения для клиента о статусе документов (ru / uz / en)
_DOC_ACK = {
    "ru": {
        "received":  "Получил! 📄 Проверяю документы...",
        "ok":        "Всё в порядке! ✅ Документы приняты, передаю менеджеру — скоро свяжемся с вами.",
        "bad":       "Хм, с документом есть проблема: {issues}\nПожалуйста, пришлите чёткое фото. 🙏",
        "other":     "Это не похоже на паспорт или прописку. Пришлите, пожалуйста, нужные документы. 🙏",
        "error":     "Не удалось обработать фото. Попробуйте ещё раз или напишите менеджеру напрямую.",
        "contract":  "📄 Ваш договор готов! Проверьте и подпишите.",
    },
    "uz": {
        "received":  "Qabul qildim! 📄 Hujjatlarni tekshiryapman...",
        "ok":        "Hammasi joyida! ✅ Hujjatlar qabul qilindi, menejerga uzatdim — tez orada bog'lanamiz.",
        "bad":       "Hujjat bilan muammo bor: {issues}\nIltimos, aniq rasm yuboring. 🙏",
        "other":     "Bu pasport yoki ro'yxatga olish emas. Iltimos, kerakli hujjatlarni yuboring. 🙏",
        "error":     "Rasmni qayta ishlab bo'lmadi. Qayta urinib ko'ring yoki to'g'ridan-to'g'ri menejerlarga yozing.",
        "contract":  "📄 Shartnomangiz tayyor! Tekshirib imzolang.",
    },
    "en": {
        "received":  "Got it! 📄 Checking your documents...",
        "ok":        "All good! ✅ Documents received, forwarding to manager — we'll be in touch soon.",
        "bad":       "There's an issue with the document: {issues}\nPlease send a clear photo. 🙏",
        "other":     "This doesn't look like a passport or registration. Please send the required documents. 🙏",
        "error":     "Couldn't process the photo. Please try again or contact the manager directly.",
        "contract":  "📄 Your contract is ready! Please review and sign.",
    },
}


async def handle_client_document(client, event, chat_id: int, name: str, tg_username: str | None):
    """
    Скачивает фото/документ, анализирует через Claude Vision,
    при успехе — пересылает менеджеру @Deepaluz.
    """
    lang = get_client_lang(chat_id)
    msgs = _DOC_ACK.get(lang, _DOC_ACK["ru"])

    # Сообщаем клиенту что получили
    await event.reply(msgs["received"])

    # Скачиваем файл в память
    try:
        img_bytes = await client.download_media(event.message, file=bytes)
    except Exception as e:
        logger.error(f"[doc] Ошибка скачивания: {e}")
        await client.send_message(chat_id, msgs["error"])
        return

    # Определяем MIME-тип
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
    media = event.message.media
    if isinstance(media, MessageMediaPhoto):
        media_type = "image/jpeg"
    elif isinstance(media, MessageMediaDocument):
        media_type = getattr(media.document, "mime_type", "image/jpeg") or "image/jpeg"
    else:
        media_type = "image/jpeg"

    # Анализируем через Claude Vision (в отдельном потоке — синхронная функция)
    try:
        result = await asyncio.to_thread(analyze_document_photo, img_bytes, media_type)
    except Exception as e:
        logger.error(f"[doc] Ошибка анализа: {e}")
        await client.send_message(chat_id, msgs["error"])
        return

    doc_type = result.get("doc_type", "other")
    valid    = result.get("valid", False)
    issues   = result.get("issues", "")
    summary  = result.get("summary", "")

    logger.info(f"[doc] chat={chat_id} type={doc_type} valid={valid} issues={issues!r}")

    if doc_type == "other":
        await client.send_message(chat_id, msgs["other"])
        return

    if not valid:
        await client.send_message(chat_id, msgs["bad"].format(issues=issues or "нечёткое фото"))
        return

    # Документ принят — уведомляем клиента
    await client.send_message(chat_id, msgs["ok"])

    # Пересылаем менеджеру с пометкой и chat_id для обратной связи
    info     = get_client_info(chat_id)
    cl_name  = info.get("real_name") or name
    tg_link  = f"@{tg_username[1:]}" if tg_username and tg_username.startswith("@") else f"tg://user?id={chat_id}"
    phone    = info.get("phone", "не указан")

    caption = (
        f"📄 *ДОКУМЕНТ КЛИЕНТА*\n\n"
        f"👤 {cl_name}\n"
        f"📱 {phone}\n"
        f"💬 {tg_link}\n"
        f"🆔 Chat ID: `{chat_id}`\n"
        f"📋 Тип: {doc_type} — {summary}\n\n"
        f"_Чтобы отправить договор клиенту, прикрепи файл и подпиши_ `#договор_{chat_id}`"
    )
    try:
        await client.send_file(
            NOTIFY_MANAGER,
            file=img_bytes,
            caption=caption,
            parse_mode="md",
        )
        logger.info(f"[doc] Документ переслан менеджеру (chat={chat_id})")
    except Exception as e:
        logger.error(f"[doc] Ошибка пересылки менеджеру: {e}")


# ─────────────────────────────────────────
# Приветствие и сбор контактов
# ─────────────────────────────────────────

async def handle_greeting(client, event, chat_id: int, tg_name: str):
    set_client_info(chat_id, greeted=True, tg_name=tg_name)

    await asyncio.to_thread(
        save_lead,
        name=tg_name,
        chat_id=chat_id,
        first_message="[Новый клиент]"
    )

    # Отправляем стикер-приветствие если есть
    await send_sticker(client, chat_id, "greeting")

    greeting = "Добро пожаловать в TAT AUTO! Чем могу помочь?"
    await event.reply(greeting)
    logger.info(f"[{tg_name}] Приветствие отправлено")


def try_extract_contact(chat_id: int, text: str) -> bool:
    found = False
    info  = get_client_info(chat_id)

    if not info.get("phone"):
        phone = extract_phone(text)
        if phone:
            set_client_info(chat_id, phone=phone)
            info["phone"] = phone
            found = True
            logger.info(f"[chat {chat_id}] Телефон сохранён: {phone}")

    if not info.get("real_name"):
        name = extract_name(text)
        if name and len(name) >= 2:
            set_client_info(chat_id, real_name=name)
            found = True
            logger.info(f"[chat {chat_id}] Имя сохранено: {name}")
            # Определяем пол по имени
            g, gc = detect_gender_from_name(name)
            if g:
                add_gender_signal(chat_id, g, gc, "name")

    if found:
        updated_info = get_client_info(chat_id)
        if updated_info.get("phone") or updated_info.get("real_name"):
            asyncio.create_task(asyncio.to_thread(
                update_lead_contact,
                chat_id=chat_id,
                name=updated_info.get("real_name") or updated_info.get("tg_name", ""),
                phone=updated_info.get("phone", ""),
            ))

    return found


# ─────────────────────────────────────────
# Основная логика ответа
# ─────────────────────────────────────────

def can_send_to_client(chat_id: int, cooldown_minutes: int = OUTGOING_COOLDOWN_MINUTES) -> bool:
    """Проверяет можно ли писать клиенту (антиспам для проактивных сообщений)."""
    last = _last_bot_sent.get(chat_id, 0)
    return time.time() - last > cooldown_minutes * 60

def mark_sent_to_client(chat_id: int):
    _last_bot_sent[chat_id] = time.time()


async def _update_summary_bg(chat_id: int, history: dict):
    """Фоновое обновление summary диалога."""
    try:
        msgs = get_chat_history(history, chat_id)
        summary = await generate_conversation_summary(chat_id, msgs)
        if summary:
            save_conversation_summary(chat_id, summary)
            logger.info(f"[Summary] обновлён для {chat_id}")
    except Exception as e:
        logger.error(f"[Summary] ошибка: {e}")


async def reply_to_user(
    client, event, text: str, tg_name: str, is_voice: bool,
    mood: str = "neutral"
):
    chat_id = event.chat_id

    # Считаем сообщения без ответа
    _unanswered_count[chat_id] = _unanswered_count.get(chat_id, 0) + 1
    if _unanswered_count[chat_id] >= _UNANSWERED_ALERT_THRESHOLD:
        _unanswered_count[chat_id] = 0
        info_ua = get_client_info(chat_id)
        name_ua = info_ua.get("real_name") or info_ua.get("tg_name", str(chat_id))
        asyncio.create_task(notify_manager(
            client,
            subject="⚠️ *КЛИЕНТ ПИШЕТ — БОТ НЕ ОТВЕЧАЕТ*",
            chat_id=chat_id, tg_name=name_ua, tg_username=None,
            extra=f"Клиент написал {_UNANSWERED_ALERT_THRESHOLD}+ сообщений подряд без ответа бота.",
        ))

    # Обновляем время последнего сообщения для дожима (и в памяти, и на диске)
    now = time.time()
    last_client_msg_time[chat_id] = now
    # Сбрасываем стадию дожима — клиент снова активен
    info = get_client_info(chat_id)
    if info.get("followup_stage", 0) > 0:
        set_client_info(chat_id, followup_stage=0, followup_sent_at=None, followup_next_at=None)
    # Сохраняем время ответа на диск (чтобы пережить перезапуск)
    set_client_info(chat_id, last_msg_at=now)

    # ── Определяем / обновляем язык клиента ──
    lang = update_client_lang(chat_id, text)

    # ── Определяем пол по грамматике текста ──
    g, gc = detect_gender_from_text(text)
    if g:
        add_gender_signal(chat_id, g, gc, "grammar")

    # ── Обновляем профиль клиента ──
    update_client_profile(chat_id, text)

    # ── Шаг 1: Первое сообщение — приветствие ──
    if is_new_client(chat_id):
        await handle_greeting(client, event, chat_id, tg_name)
        try_extract_contact(chat_id, text)
        # Запускаем анализ аватара в фоне
        asyncio.create_task(try_detect_gender_from_photo(client, chat_id))
        # Не останавливаемся — если клиент сразу написал вопрос, продолжаем обработку ниже

    # ── Шаг 2: Пробуем извлечь контакт ──
    try_extract_contact(chat_id, text)

    # ── Проверяем: купил или отказался — останавливаем дожим ──
    if is_saying_purchased(text):
        model_key = get_model_from_text(text)
        mark_customer_purchased(chat_id, model_key or "неизвестная модель")
        set_client_info(chat_id, followup_stage=FOLLOWUP_MAX_STAGE)
        logger.info(f"[chat {chat_id}] Клиент купил авто — дожим остановлен")
    elif is_saying_not_interested(text):
        mark_customer_not_interested(chat_id)
        set_client_info(chat_id, followup_stage=FOLLOWUP_MAX_STAGE)
        logger.info(f"[chat {chat_id}] Клиент не интересуется — дожим остановлен")

    # ── Шаг 3: Тест-драйв ──
    if is_declining_test_drive(text):
        # Клиент отказался — закрываем сессию если была открыта
        if chat_id in test_drive_sessions:
            del test_drive_sessions[chat_id]
        # Продолжаем в обычный AI-диалог (не return)
    elif chat_id in test_drive_sessions and test_drive_sessions[chat_id].get("step", 0) > 0:
        await handle_test_drive_flow(client, event, chat_id, text, tg_name)
        return
    elif is_asking_test_drive(text):
        set_client_info(chat_id, asked_testdrive=True)
        await handle_test_drive_flow(client, event, chat_id, text, tg_name)
        return

    # ── Шаг 4: Готов купить — запрашиваем документы ──
    if is_ready_to_buy(text):
        client_info = get_client_info(chat_id)
        # Запрашиваем документы и уведомляем менеджера только один раз
        if not client_info.get("docs_requested"):
            set_client_info(chat_id, docs_requested=True)
            mark_customer_interested(chat_id, get_model_from_text(text) or
                                     client_info.get("interested_model", "неизвестно"))
            doc_msg = get_documents_request_message(lang)
            async with client.action(chat_id, "typing"):
                typing_delay = min(DELAY_MIN + len(doc_msg) * DELAY_PER_CHAR + random.uniform(0, 1), DELAY_MAX)
                await asyncio.sleep(typing_delay)
            await event.reply(doc_msg)
            add_message(history, chat_id, "user", text)
            add_message(history, chat_id, "assistant", doc_msg)
            logger.info(f"[chat {chat_id}] Документы запрошены")
            # Уведомляем менеджера — параллельно, не блокируем ответ клиенту
            tg_username = await get_username(event)
            asyncio.create_task(
                notify_manager_ready_to_buy(client, chat_id, tg_name, tg_username)
            )
            return

    # ── Шаг 4.5: Каталог моделей ──
    stripped = text.strip()
    # Выбор по номеру из каталога
    if stripped in _CATALOG_NUMBER_MAP and get_client_info(chat_id).get("catalog_shown"):
        chosen_key = _CATALOG_NUMBER_MAP[stripped]
        mark_customer_interested(chat_id, chosen_key)
        await send_photos(client, chat_id, chosen_key, real_name)
        model_name = MODEL_NAMES.get(chosen_key, chosen_key)
        add_message(history, chat_id, "user", f"Интересует {model_name}")
        msgs = get_chat_history(history, chat_id)[-20:]
        async with client.action(chat_id, "typing"):
            reply = await get_ai_reply(msgs, chat_id, client_name=real_name,
                                       mood=mood, lang=lang, gender=gender, profile=profile)
            if reply:
                await asyncio.sleep(min(DELAY_MIN + len(reply)*DELAY_PER_CHAR, DELAY_MAX))
        if reply:
            await event.reply(reply)
            add_message(history, chat_id, "assistant", reply)
        return

    if is_asking_catalog(text):
        catalog = get_catalog_text(lang)
        set_client_info(chat_id, catalog_shown=True)
        add_message(history, chat_id, "user", text)
        add_message(history, chat_id, "assistant", catalog)
        await event.reply(catalog)
        return

    # ── Шаг 5: Прайс-лист ──
    if is_asking_price_list(text):
        set_client_info(chat_id, asked_price=True)
        add_message(history, chat_id, "user", text)
        messages = get_chat_history(history, chat_id)
        client_info = get_client_info(chat_id)
        real_name = client_info.get("real_name") or tg_name
        gender = get_client_gender(chat_id)
        profile = get_formatted_profile(chat_id)

        async with client.action(chat_id, "typing"):
            reply_task = asyncio.create_task(
                get_ai_reply(messages, chat_id, client_name=real_name, mood=mood, lang=lang, gender=gender, profile=profile)
            )
            reply = await reply_task
            if reply:
                typing_delay = min(DELAY_MIN + len(reply) * DELAY_PER_CHAR + random.uniform(0, 1), DELAY_MAX)
                await asyncio.sleep(typing_delay)

        if reply:
            await event.reply(reply)
            add_message(history, chat_id, "assistant", reply)

        await asyncio.sleep(1)
        await send_price_list(client, chat_id, event)
        return

    # ── Шаг 6: Обычный диалог с AI ──
    client_info = get_client_info(chat_id)
    real_name   = client_info.get("real_name") or tg_name
    gender      = get_client_gender(chat_id)
    profile     = get_formatted_profile(chat_id)

    # Если не знаем имя или телефон — добавляем задачу в профиль
    missing = []
    if not client_info.get("real_name"):
        missing.append("имя")
    if not client_info.get("phone"):
        missing.append("номер телефона")
    if missing:
        profile += f"\n[ЗАДАЧА: в ходе диалога вежливо узнай {' и '.join(missing)} клиента — один вопрос за раз]"

    # Обновляем оценку стиля письма клиента
    style_score = update_client_style(chat_id, text)

    # ── Конкурент упомянут — добавляем аргумент в профиль ──
    competitor_key = detect_competitor(text)
    if competitor_key:
        rebuttal = get_competitor_rebuttal(competitor_key)
        comp_name = {"byd":"BYD","haval":"Haval","chery":"Chery","geely":"Geely",
                     "toyota":"Toyota","kia":"Kia","hyundai":"Hyundai",
                     "exeed":"Exeed","omoda":"Omoda"}.get(competitor_key, competitor_key.upper())
        profile += (
            f"\n[КОНКУРЕНТ: клиент упомянул {comp_name}. "
            f"Используй этот аргумент в ответе: {rebuttal} "
            f"Не говори плохо о конкуренте прямо — говори о преимуществах Voyah/M-Hero.]"
        )
        logger.info(f"[chat {chat_id}] Конкурент: {comp_name}")

    # ── Trade-in ──
    if is_mentioning_tradein(text) and not client_info.get("tradein_notified"):
        set_client_info(chat_id, tradein_notified=True)
        profile += (
            "\n[TRADE-IN: клиент, возможно, готов сдать старый авто. "
            "Спроси какая у него машина, год, состояние. Скажи что рассматриваем зачёт.]"
        )
        tg_username = await get_username(event)
        asyncio.create_task(notify_manager(
            client,
            subject="🔄 *КЛИЕНТ ИНТЕРЕСУЕТСЯ TRADE-IN*",
            chat_id=chat_id, tg_name=real_name, tg_username=tg_username,
            extra="Клиент упомянул свой старый автомобиль — возможен зачёт.",
        ))

    add_message(history, chat_id, "user", text)
    # Лимит истории: последние 20 сообщений + few-shot примеры
    messages = get_chat_history(history, chat_id)[-20:]

    model_key = get_model_from_text(text)
    if model_key:
        mark_customer_interested(chat_id, model_key)

    # ── Горячий лид: много сигналов за одну сессию → срочно уведомить менеджера ──
    fresh_info = get_client_info(chat_id)
    hot_score = get_hot_lead_score(fresh_info)
    if hot_score >= HOT_LEAD_THRESHOLD and not fresh_info.get("hot_lead_notified"):
        set_client_info(chat_id, hot_lead_notified=True)
        tg_username = await get_username(event)
        asyncio.create_task(notify_manager(
            client,
            subject="🔥🔥 *ГОРЯЧИЙ ЛИД — ПОЗВОНИТЕ СРОЧНО*",
            chat_id=chat_id, tg_name=real_name, tg_username=tg_username,
            extra=(
                f"Клиент набрал {hot_score} сигналов готовности к покупке.\n"
                f"Спросил: цену ✅, кредит ✅, тест-драйв ✅\n\n"
                f"⚡️ Рекомендуется связаться лично прямо сейчас!"
            ),
        ))
        logger.info(f"[HOT LEAD] chat={chat_id} score={hot_score}")

    async with client.action(chat_id, "typing"):
        reply = await get_ai_reply(
            messages, chat_id,
            client_name=real_name, mood=mood, lang=lang,
            gender=gender, profile=profile,
        )
        if reply:
            typing_delay = min(
                DELAY_MIN + len(reply) * DELAY_PER_CHAR + random.uniform(0, 1.5),
                DELAY_MAX
            )
            await asyncio.sleep(typing_delay)

    if not reply:
        logger.warning(f"AI не вернул ответ для [{real_name}]")
        # Fallback — короткое сообщение чтобы клиент не ждал в пустоте
        try:
            fallback = {
                "ru": "Секунду, уточняю информацию...",
                "uz": "Bir daqiqa, ma'lumotni aniqlayman...",
                "en": "One moment, let me check...",
            }.get(lang, "Секунду...")
            await event.reply(fallback)
        except Exception:
            pass
        return

    # Расчёт кредита
    if is_asking_credit(text) and model_key and model_key in MODEL_PRICES:
        set_client_info(chat_id, asked_credit=True)
        reply += "\n\n" + format_credit_info(MODEL_PRICES[model_key], 30, 36)

    # Насия / лизинг — уведомляем менеджера (один раз на диалог)
    if is_asking_nasiya_leasing(text):
        set_client_info(chat_id, asked_nasiya=True)
        client_info2 = get_client_info(chat_id)
        if not client_info2.get("nasiya_notified"):
            set_client_info(chat_id, nasiya_notified=True)
            tg_username = await get_username(event)
            from ai_handler import load_customer_status
            cs = load_customer_status()
            m_key = cs.get(str(chat_id), {}).get("interested_model", "")
            m_display = MODEL_NAMES.get(m_key, m_key or "не указана")
            asyncio.create_task(notify_manager(
                client,
                subject="💼 *КЛИЕНТ ИНТЕРЕСУЕТСЯ НАСИЯ / ЛИЗИНГОМ*",
                chat_id=chat_id, tg_name=real_name, tg_username=tg_username,
                extra=f"🚗 Модель: {m_display}\n\n⚡️ Нужен расчёт насия/лизинга — свяжитесь с клиентом!",
            ))

    # Иногда вносим опечатку — подстраиваемся под стиль клиента
    reply_to_send, correct_word = apply_human_typo(reply, style_score)
    if correct_word:
        logger.info(f"[Опечатка] стиль={style_score:.2f} слово='{correct_word}'")

    # Отправка ответа
    if is_voice and REPLY_WITH_VOICE and len(reply_to_send) <= VOICE_MAX_CHARS:
        voice_reply_path = f"temp_reply_{chat_id}_{int(time.time())}.ogg"
        voice_ok = False
        try:
            await asyncio.to_thread(synthesize_voice, reply_to_send, voice_reply_path)
            if os.path.exists(voice_reply_path) and os.path.getsize(voice_reply_path) > 0:
                await client.send_file(chat_id, voice_reply_path, voice_note=True)
                voice_ok = True
        except Exception as e:
            logger.error(f"Синтез голоса: {e}")
        finally:
            if os.path.exists(voice_reply_path):
                os.remove(voice_reply_path)
        if not voice_ok:
            await event.reply(reply_to_send)
    else:
        await event.reply(reply_to_send)
    mark_sent_to_client(chat_id)

    # Если была опечатка — иногда отправляем исправление с задержкой (60% шанс)
    if correct_word and random.random() < 0.6:
        correction = make_correction_message(correct_word)
        await asyncio.sleep(random.uniform(4, 10))
        await client.send_message(chat_id, correction)

    add_message(history, chat_id, "assistant", reply)   # в историю сохраняем правильный текст
    logger.info(f"[Агент -> {real_name}] {reply[:100]}...")

    # Обновляем summary диалога в фоне каждые 10 сообщений
    msg_count = len(get_chat_history(history, chat_id))
    if msg_count % 10 == 0:
        asyncio.create_task(_update_summary_bg(chat_id, history))

    # Отправляем стикер если клиент очень позитивный
    if mood in ("excited", "happy") and random.random() < 0.4:
        await asyncio.sleep(0.5)
        await send_sticker(client, chat_id, mood)

    if is_asking_location(text):
        set_client_info(chat_id, asked_location=True)
        await asyncio.sleep(1)
        await send_location(client, chat_id)

    if is_asking_photo(text):
        await asyncio.sleep(1)
        await send_photos(client, chat_id, model_key, real_name)

    # Сбрасываем счётчик — бот ответил
    _unanswered_count[chat_id] = 0


# ─────────────────────────────────────────
# Воркер
# ─────────────────────────────────────────

async def user_worker(client, chat_id: int):
    queue = user_queues[chat_id]
    while True:
        item = await queue.get()
        if item is None:
            break
        event, text, name, is_voice, mood = item
        try:
            await reply_to_user(client, event, text, name, is_voice, mood=mood)
        except Exception as e:
            logger.error(f"Ошибка воркера: {e}")
        finally:
            queue.task_done()


def enqueue(client, chat_id: int, event, text: str, name: str, is_voice: bool, mood: str = "neutral"):
    if chat_id not in user_queues:
        user_queues[chat_id] = asyncio.Queue()
    user_queues[chat_id].put_nowait((event, text, name, is_voice, mood))
    task = user_tasks.get(chat_id)
    if task is None or task.done():
        user_tasks[chat_id] = asyncio.create_task(user_worker(client, chat_id))


# ─────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────

async def main():
    client = TelegramClient("agent_session", API_ID, API_HASH)

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        # ── Дедупликация: одно сообщение обрабатываем только раз ──
        msg_id = event.message.id
        if msg_id in _processed_msg_ids:
            return
        _processed_msg_ids.add(msg_id)
        if len(_processed_msg_ids) > _PROCESSED_IDS_MAX:
            # Удаляем самые старые (set не упорядочен — просто срезаем до половины)
            oldest = list(_processed_msg_ids)[:_PROCESSED_IDS_MAX // 2]
            for old_id in oldest:
                _processed_msg_ids.discard(old_id)

        # Фильтруем старые сообщения — они уже обработаны reply_to_missed_messages
        if _startup_ts > 0:
            msg_ts = event.message.date.timestamp()
            if msg_ts < _startup_ts:
                return

        # ── Команды от менеджера (@Deepaluz) ──
        sender_username = await get_username(event)
        if sender_username and sender_username.lower() == NOTIFY_MANAGER.lower():
            text_raw = (event.raw_text or "").strip()

            # ── /takeover {chat_id} — взять клиента на себя на 24ч ──
            takeover_m = re.match(r'/takeover\s+(\d+)', text_raw.strip(), re.IGNORECASE)
            if takeover_m:
                target_id = int(takeover_m.group(1))
                until = time.time() + 24 * 3600
                set_client_info(target_id, takeover_until=until)
                info2 = get_client_info(target_id)
                cl_name = info2.get("real_name") or info2.get("tg_name", str(target_id))
                await event.reply(f"Взял {cl_name} на себя. Бот молчит 24ч в этом чате.")
                logger.info(f"[takeover] {target_id} взят менеджером на 24ч")
                return

            # ── /release {chat_id} — вернуть клиента боту досрочно ──
            release_m = re.match(r'/release\s+(\d+)', text_raw.strip(), re.IGNORECASE)
            if release_m:
                target_id = int(release_m.group(1))
                set_client_info(target_id, takeover_until=0)
                info2 = get_client_info(target_id)
                cl_name = info2.get("real_name") or info2.get("tg_name", str(target_id))
                await event.reply(f"Бот снова работает с {cl_name}.")
                return

            # ── /client {chat_id} — карточка клиента ──
            client_m = re.match(r'/client\s+(\d+)', text_raw.strip(), re.IGNORECASE)
            if client_m:
                target_id = int(client_m.group(1))
                info2 = get_client_info(target_id)
                from ai_handler import load_customer_status
                cs2 = load_customer_status()
                cs_info = cs2.get(str(target_id), {})
                profile2 = info2.get("sales_profile", {})

                name2    = info2.get("real_name") or info2.get("tg_name", "—")
                phone2   = info2.get("phone", "не указан")
                lang2    = info2.get("lang", "ru")
                model2   = cs_info.get("interested_model") or cs_info.get("purchased_model", "—")
                stage2   = info2.get("followup_stage", 0)
                takeover2= "да" if is_taken_over(target_id) else "нет"
                docs2    = "да" if info2.get("docs_requested") else "нет"
                purchased2 = "да" if cs_info.get("purchased") else "нет"

                purpose_map = {"family":"семья","business":"бизнес","long_trips":"трасса","city":"город"}
                purposes2 = ", ".join(purpose_map.get(p,p) for p in profile2.get("purposes",[])) or "—"
                budget2  = profile2.get("budget_hint", "—")
                timeline2= {"now":"сейчас","soon":"скоро","researching":"присматривается"}.get(profile2.get("timeline",""), "—")
                objs2    = ", ".join(profile2.get("objections_raised", [])) or "—"

                # Последние 3 сообщения
                hist = get_chat_history(history, target_id)[-3:]
                last_msgs = "\n".join(f"  [{m['role']}]: {m['content'][:80]}" for m in hist) or "  нет"

                card = (
                    f"Карточка клиента\n\n"
                    f"Имя: {name2}\n"
                    f"Телефон: {phone2}\n"
                    f"Язык: {lang2}\n"
                    f"Модель: {model2}\n"
                    f"Документы запрошены: {docs2}\n"
                    f"Купил: {purchased2}\n"
                    f"Дожим стадия: {stage2}\n"
                    f"На менеджере: {takeover2}\n\n"
                    f"Профиль:\n"
                    f"  Бюджет: {budget2}\n"
                    f"  Цель: {purposes2}\n"
                    f"  Сроки: {timeline2}\n"
                    f"  Возражения: {objs2}\n\n"
                    f"Последние сообщения:\n{last_msgs}"
                )
                await client.send_message(event.chat_id, card)
                return

            # ── /stats — дашборд по клиентам ──
            if text_raw.strip().lower() in ("/stats", "/стат", "/статистика"):
                now_ts = time.time()
                all_data = load_client_data()
                total = hot = cold = active_today = purchased = 0
                hot_names, cold_names = [], []

                for cid_str, info in all_data.items():
                    if not info.get("greeted"):
                        continue
                    total += 1
                    if info.get("purchased"):
                        purchased += 1
                        continue
                    last_msg = info.get("last_msg_at", 0)
                    name = info.get("real_name") or info.get("tg_name", cid_str)
                    phone = info.get("phone", "")
                    label = f"{name}" + (f" ({phone})" if phone else f" [id:{cid_str}]")

                    if last_msg and now_ts - last_msg < 86400:
                        active_today += 1
                    if info.get("docs_requested"):
                        hot += 1
                        hot_names.append(label)
                    elif last_msg and now_ts - last_msg > 3 * 86400 and not is_followup_stopped(int(cid_str)):
                        cold += 1
                        cold_names.append(label)

                lines = [
                    "📊 *Статистика TAT AUTO*\n",
                    f"Всего клиентов: {total}",
                    f"Купили: {purchased}",
                    f"Активны сегодня: {active_today}",
                    f"Горячие (готовы к оформлению): {hot}",
                    f"Молчат 3+ дней: {cold}",
                ]
                if hot_names:
                    lines.append("\n🔥 *Горячие:*")
                    lines += [f"  • {n}" for n in hot_names[:10]]
                if cold_names:
                    lines.append("\n🧊 *Молчат 3+ дней:*")
                    lines += [f"  • {n}" for n in cold_names[:10]]

                await client.send_message(event.chat_id, "\n".join(lines), parse_mode="md")
                return

            # ── /лиды [категория] — клиенты по интересам ──
            leads_m = re.match(r'/лиды(?:\s+(.+))?', text_raw.strip(), re.IGNORECASE)
            if leads_m:
                cat = (leads_m.group(1) or "").strip().lower()
                all_data = load_client_data()
                from ai_handler import load_customer_status
                cs_all = load_customer_status()

                # Категории с флагами в client_data
                categories = {
                    "локация":   ("asked_location",  "📍 Спрашивали локацию"),
                    "тест":      ("asked_testdrive",  "🚗 Запись на тест-драйв"),
                    "тест-драйв":("asked_testdrive",  "🚗 Запись на тест-драйв"),
                    "прайс":     ("asked_price",      "💰 Просили прайс-лист"),
                    "кредит":    ("asked_credit",     "🏦 Интересовались кредитом"),
                    "насия":     ("asked_nasiya",     "📋 Насия / лизинг"),
                    "лизинг":    ("asked_nasiya",     "📋 Насия / лизинг"),
                    "документы": ("docs_requested",   "📄 Готовы к оформлению"),
                }

                def _fmt_client(cid_str, info):
                    name = info.get("real_name") or info.get("tg_name", cid_str)
                    phone = info.get("phone", "")
                    model = cs_all.get(cid_str, {}).get("interested_model", "")
                    model_label = MODEL_NAMES.get(model, model) if model else ""
                    parts = [f"• {name}"]
                    if phone:
                        parts.append(f"тел: {phone}")
                    if model_label:
                        parts.append(f"модель: {model_label}")
                    parts.append(f"id: {cid_str}")
                    return "  ".join(parts)

                if cat and cat in categories:
                    flag, label = categories[cat]
                    matched = [(cid, info) for cid, info in all_data.items() if info.get(flag)]
                    lines = [f"{label} ({len(matched)} чел.):\n"]
                    if matched:
                        lines += [_fmt_client(cid, info) for cid, info in matched[:30]]
                    else:
                        lines.append("Пока никто не спрашивал.")
                    await client.send_message(event.chat_id, "\n".join(lines))
                else:
                    # Все категории сразу
                    lines = ["📋 *Лиды по категориям:*\n"]
                    for key, (flag, label) in categories.items():
                        if key in ("тест-драйв", "лизинг"):
                            continue  # дубликаты
                        matched = [cid for cid, info in all_data.items() if info.get(flag)]
                        lines.append(f"{label}: {len(matched)} чел.")
                    lines.append("\nДля списка: /лиды локация | тест | прайс | кредит | насия | документы")
                    await client.send_message(event.chat_id, "\n".join(lines), parse_mode="md")
                return

            # ── /вопросы — список неизвестных вопросов ──
            if text_raw.strip().lower() in ("/вопросы", "/questions", "/q"):
                from ai_handler import load_unknown_questions
                questions = load_unknown_questions()
                pending = [q for q in questions if q.get("status") == "pending"]
                if not pending:
                    await event.reply("✅ Нет неотвеченных вопросов!")
                    return
                lines = [f"❓ *Вопросы без ответа ({len(pending)}):*\n"]
                for i, q in enumerate(pending[:10], 1):
                    lines.append(f"{i}. {q['question'][:120]}")
                lines.append(f"\nЧтобы ответить: /ответ [номер] [ответ]")
                await client.send_message(event.chat_id, "\n".join(lines), parse_mode="md")
                return

            # ── /ответ N текст — ответить на вопрос из /вопросы ──
            answer_m = re.match(r'/ответ\s+(\d+)\s+(.+)', text_raw.strip(), re.DOTALL | re.IGNORECASE)
            if answer_m:
                from ai_handler import load_unknown_questions, UNKNOWN_QUESTIONS_FILE
                from storage import save_training_example
                idx = int(answer_m.group(1)) - 1
                answer_text = answer_m.group(2).strip()
                questions = load_unknown_questions()
                pending = [q for q in questions if q.get("status") == "pending"]
                if 0 <= idx < len(pending):
                    question_text = pending[idx]["question"]
                    # Сохраняем в обучающие данные
                    save_training_example(question_text, answer_text, source="manager_correction")
                    # Помечаем как решённый
                    for q in questions:
                        if q.get("question") == question_text and q.get("status") == "pending":
                            q["status"] = "answered"
                            q["answer"] = answer_text
                            break
                    try:
                        import json as _json
                        with open(UNKNOWN_QUESTIONS_FILE, "w", encoding="utf-8") as f:
                            _json.dump(questions, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        logger.error(f"Ошибка сохранения ответа: {e}")
                    await event.reply(f"✅ Ответ сохранён и добавлен в обучение!\n\nВопрос: {question_text[:100]}\nОтвет: {answer_text[:100]}")
                else:
                    await event.reply(f"❌ Вопрос #{idx+1} не найден. Список: /вопросы")
                return

            # ── /цены — обновить текст цен ──
            if text_raw.lower().startswith("/цены") or text_raw.lower().startswith("/price"):
                new_price_text = text_raw.split(None, 1)[1].strip() if len(text_raw.split(None, 1)) > 1 else ""
                if new_price_text:
                    save_price_summary(new_price_text)
                    await event.reply("✅ Цены обновлены! Теперь буду отправлять клиентам этот текст.")
                else:
                    current = load_price_summary()
                    await event.reply(f"Текущий текст цен:\n\n{current}\n\nЧтобы обновить: /цены [новый текст]")
                return

            # Менеджер отправил прайс-лист: любой файл/фото с подписью #прайс
            if "#прайс" in text_raw.lower() or "#price" in text_raw.lower():
                if event.message.media:
                    save_price_list_cache(chat_id=event.chat_id, msg_id=event.message.id)
                    await event.reply("✅ Прайс-лист сохранён! Теперь буду пересылать его клиентам.")
                    logger.info(f"Прайс сохранён: chat={event.chat_id} msg={event.message.id}")
                else:
                    await event.reply("❌ Прикрепи файл или фото с подписью #прайс")
                return

            # Менеджер отправил договор клиенту: файл с подписью #договор_{chat_id}
            contract_match = re.search(r"#договор[_\-](\d+)", text_raw, re.IGNORECASE)
            if contract_match:
                target_chat_id = int(contract_match.group(1))
                if event.message.media:
                    lang = get_client_lang(target_chat_id)
                    contract_msg = _DOC_ACK.get(lang, _DOC_ACK["ru"])["contract"]
                    try:
                        await client.send_file(
                            target_chat_id,
                            file=await client.download_media(event.message, file=bytes),
                            caption=contract_msg,
                        )
                        await event.reply(f"✅ Договор отправлен клиенту (chat_id={target_chat_id})")
                        logger.info(f"[договор] Переслан клиенту chat={target_chat_id}")
                    except Exception as e:
                        logger.error(f"[договор] Ошибка пересылки клиенту {target_chat_id}: {e}")
                        await event.reply(f"❌ Не удалось отправить договор клиенту {target_chat_id}: {e}")
                else:
                    await event.reply("❌ Прикрепи файл договора с пометкой #договор_{chat_id}")
                return

            # ── Ни одна команда не совпала — отвечаем через AI без задержки ──
            if text_raw and not text_raw.startswith("#"):
                add_message(history, event.chat_id, "user", text_raw)
                messages = get_chat_history(history, event.chat_id)
                async with client.action(event.chat_id, "typing"):
                    reply = await get_ai_reply(
                        messages, event.chat_id,
                        client_name="менеджер", mood="neutral", lang="ru"
                    )
                if reply:
                    add_message(history, event.chat_id, "assistant", reply)
                    await event.reply(reply)
            return

        if not await should_handle(event):
            return

        chat_id = event.chat_id
        name    = await get_display_name(event)

        # ── Стикер ──
        if is_sticker(event):
            emoji   = get_sticker_emoji(event)
            emotion = analyze_sticker_emotion(emoji)
            client_info = get_client_info(chat_id)
            client_name = client_info.get("real_name") or name

            logger.info(f"[{name}] 🎭 Стикер emoji={emoji!r} emotion={emotion}")

            # Обновляем время последнего сообщения
            last_client_msg_time[chat_id] = time.time()
            if client_info.get("followup_stage", 0) > 0:
                set_client_info(chat_id, followup_stage=0)

            await asyncio.sleep(random.uniform(1, 3))

            # Пробуем ответить стикером
            sticker_sent = await send_sticker(client, chat_id, emotion)

            # Всегда добавляем текстовую реакцию
            text_reply = get_sticker_text_reply(emotion, client_name)
            await event.reply(text_reply)
            return

        # ── Голосовое ──
        if is_voice_message(event):
            text = await handle_voice(event, client, chat_id, name)
            if not text:
                return
            mood = analyze_text_mood(text)
            logger.info(f"[{name}] 🎤 {text} [{mood}]")
            enqueue(client, chat_id, event, text, name, is_voice=True, mood=mood)
            return

        # ── Фото / документ ──
        if is_photo_message(event):
            client_info = get_client_info(chat_id)
            caption = (event.message.message or "").lower()
            _doc_keywords = ("прописк", "propisk", "ro'yxat")
            caption_is_doc = any(kw in caption for kw in _doc_keywords)

            if client_info.get("docs_requested") or caption_is_doc:
                # Если клиент сам прислал документ без запроса — активируем режим
                if not client_info.get("docs_requested"):
                    set_client_info(chat_id, docs_requested=True)
                tg_username = await get_username(event)
                logger.info(f"[{name}] 📄 Прислал документ (caption_is_doc={caption_is_doc})")
                last_client_msg_time[chat_id] = time.time()
                asyncio.create_task(
                    handle_client_document(client, event, chat_id, name, tg_username)
                )
            return

        # ── Текст ──
        text = (event.raw_text or "").strip()
        if not text:
            return

        mood = analyze_text_mood(text)
        logger.info(f"[{name}] 💬 {text} [{mood}]")
        enqueue(client, chat_id, event, text, name, is_voice=False, mood=mood)

    print("=" * 60)
    print("  TAT AUTO — AI Агент v4.0")
    print("  Стикеры | Дожим | Прайс-лист | Настроение")
    print("=" * 60)

    await client.start()
    me = await client.get_me()
    logger.info(f"Запущен как: {me.first_name} (@{me.username})")

    # Загружаем стикерпаки
    # Восстанавливаем время последнего сообщения из client_data (для дожима после перезапуска)
    _restore_last_msg_times()

    logger.info("Загрузка стикерпаков...")
    await preload_sticker_packs(client)

    logger.info("Загрузка истории старых чатов...")
    await load_old_chats(client)

    logger.info("Ответ на пропущенные сообщения (последние 5ч)...")
    await reply_to_missed_messages(client, hours=5)

    global _startup_ts
    _startup_ts = time.time()
    logger.info(f"Старт завершён, игнорируем сообщения до {_startup_ts:.0f}")

    logger.info("Обучение на переписках менеджеров...")
    await run_training(client)

    # ── Хендлер Избранного: @username или телефон → написать клиенту ──
    me_id = me.id

    @client.on(events.NewMessage(outgoing=True))
    async def on_saved(event):
        # Только Избранное (Saved Messages = сообщение самому себе)
        if event.chat_id != me_id:
            return
        text = (event.raw_text or "").strip()
        if not text:
            return

        # Парсим @username или номер телефона (+998... / 998... / 90...)
        username_m = re.match(r'@(\w+)(.*)', text, re.DOTALL | re.IGNORECASE)
        phone_m    = re.match(r'(\+?[\d][\d\s\-]{7,14})(.*)', text, re.DOTALL)

        target_str  = None
        custom_text = ""

        if username_m:
            target_str  = "@" + username_m.group(1)
            custom_text = (username_m.group(2) or "").strip()
        elif phone_m:
            target_str  = re.sub(r'[\s\-]', '', phone_m.group(1))
            custom_text = (phone_m.group(2) or "").strip()
        else:
            return  # не username и не телефон — обычная заметка, игнорируем

        # Ищем клиента в нашей базе по username или телефону
        target_chat_id = None
        all_data = load_client_data()
        for cid_str, info in all_data.items():
            if target_str.startswith("@"):
                stored = info.get("tg_username", "")
                if stored and stored.lower() == target_str.lower():
                    target_chat_id = int(cid_str)
                    break
            else:
                stored_phone = re.sub(r'[\s\-\+]', '', info.get("phone", ""))
                clean        = re.sub(r'[\s\-\+]', '', target_str)
                if stored_phone and (stored_phone == clean or stored_phone.endswith(clean[-9:])):
                    target_chat_id = int(cid_str)
                    break

        # Если не нашли в базе — пробуем через Telegram API
        if target_chat_id is None:
            try:
                entity = await client.get_entity(target_str)
                target_chat_id = entity.id
            except Exception as e:
                await client.send_message("me", f"❌ Клиент не найден: {target_str}\n{e}")
                logger.warning(f"[Избранное] не нашёл {target_str}: {e}")
                return

        if custom_text:
            # Менеджер написал сообщение после ника — отправляем его дословно
            try:
                await client.send_message(target_chat_id, custom_text)
                mark_sent_to_client(target_chat_id)
                add_message(history, target_chat_id, "assistant", custom_text)
                set_client_info(target_chat_id, last_msg_at=time.time())
                await client.send_message("me", f"✅ Отправлено {target_str}:\n{custom_text[:120]}")
                logger.info(f"[Избранное] кастомное -> {target_str}: {custom_text[:60]}")
            except Exception as e:
                await client.send_message("me", f"❌ Ошибка отправки {target_str}: {e}")
        else:
            # Ника или номера достаточно — AI генерирует сообщение
            info      = get_client_info(target_chat_id)
            real_name = info.get("real_name") or info.get("tg_name", "Клиент")
            lang      = get_client_lang(target_chat_id)
            gender    = get_client_gender(target_chat_id)
            profile   = get_formatted_profile(target_chat_id)
            messages  = get_chat_history(history, target_chat_id)

            try:
                async with client.action(target_chat_id, "typing"):
                    reply = await get_ai_reply(
                        messages, target_chat_id,
                        client_name=real_name, mood="neutral",
                        lang=lang, gender=gender, profile=profile,
                    )
                    if reply:
                        delay = min(DELAY_MIN + len(reply) * DELAY_PER_CHAR + random.uniform(0, 1.5), DELAY_MAX)
                        await asyncio.sleep(delay)

                if reply:
                    await client.send_message(target_chat_id, reply)
                    mark_sent_to_client(target_chat_id)
                    add_message(history, target_chat_id, "assistant", reply)
                    set_client_info(target_chat_id, last_msg_at=time.time())
                    await client.send_message("me", f"✅ AI написал {target_str}:\n{reply[:120]}")
                    logger.info(f"[Избранное] AI -> {target_str}: {reply[:60]}")
                else:
                    await client.send_message("me", f"⚠️ AI не сгенерировал ответ для {target_str}")
            except Exception as e:
                await client.send_message("me", f"❌ Ошибка AI для {target_str}: {e}")
                logger.error(f"[Избранное] AI ошибка -> {target_str}: {e}")

    # Запускаем фоновые задачи
    asyncio.create_task(followup_worker(client))
    asyncio.create_task(_client_data_flush_worker())
    asyncio.create_task(morning_brief_worker(client))
    logger.info("Воркеры запущены")

    logger.info("Жду сообщений...\n")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
