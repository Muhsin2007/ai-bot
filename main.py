"""
TAT AUTO — Telegram Sales Bot
Работает как пользователь (Telethon user account).

Ключевые правила:
  • Бот НИКОГДА не пишет первым — только отвечает на входящие.
  • Группы полностью игнорируются.
  • Opted-out клиенты никогда не получают ответ.
  • Прайс отправляется ОДНИМ сообщением.
"""
import asyncio
import os
import random
import re
import time

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User, MessageMediaContact, MessageMediaVenue, MessageMediaGeo
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaVenue, InputGeoPoint

from logger import log
from safe_telethon import safe_send
from shutdown import shutdown_event, setup_shutdown_handlers, backup_worker, finalize
from storage_db import (
    init_db, migrate_from_json,
    add_message, get_chat_history, get_chat_history_full, has_messages, count_messages,
    get_client_info, set_client_info, get_all_clients,
    get_client_stage, set_client_stage,
    load_prices, save_prices,
    save_summary,
    save_appointment, get_upcoming_appointments,
    save_training_qa, get_all_training_qa, delete_training_qa, count_training_qa,
    set_conversation_label, get_conversation_label,
)
from ai_handler import (
    get_ai_reply, generate_summary, detect_language, detect_gender,
    detect_competitor, get_competitor_facts,
    detect_tradein, detect_testdrive,
    detect_optout, detect_buying_intent, is_greeting_only,
    OPTOUT_FAREWELL,
    get_followup_message,
    is_asking_price, is_asking_location, is_asking_photo,
    get_model_from_text, get_all_models_from_text,
    detect_hesitation, extract_facts,
    detect_post_purchase, extract_conversation_patterns,
    interpret_sticker,
    detect_nasiya_calc, NASIYA_ROUTING_MSG,
)

load_dotenv()

API_ID     = int(os.getenv("TG_API_ID", "0"))
API_HASH   = os.getenv("TG_API_HASH", "")
MANAGER_ID = int(os.getenv("MANAGER_ID", "0"))

# ══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКИ
# ══════════════════════════════════════════════════════════════════════════════
DELAY_MIN              = 3    # сек — минимальная задержка перед ответом
DELAY_MAX              = 7    # сек — максимальная задержка
PAUSE_AFTER_MANUAL_MIN = 30   # мин — пауза после ручного ответа менеджера
ANTISPAM_COOLDOWN_MIN  = 2    # мин — минимальный интервал между ботовыми ответами
TG_HISTORY_LIMIT       = 50   # кол-во сообщений загружаемых из TG при первом контакте
MISSED_MSG_MAX_AGE_H   = 12   # часов — не обрабатывать сообщения старше этого
BATCH_WAIT_S           = 2.5  # сек — ждём дополнительных сообщений перед ответом

# Координаты салона
LOCATION_LAT = 41.27141264101891
LOCATION_LON = 69.23809351597293

# Прайс — конкретное сообщение из канала
PRICE_CHANNEL = "deeeepal"
PRICE_MSG_ID  = 6

# Локация — venue-сообщение из того же канала.
# Как опубликовать:
#   1. Открой канал @deeeepal
#   2. Отправь туда локацию (Вложение → Локация → "Отправить как геопозицию" или venue)
#   3. Запомни ID сообщения и поставь ниже
# Пока 0 — бот использует координаты из кода (LOCATION_LAT/LON).
LOCATION_MSG_ID = 16

# Фото моделей (локальные файлы)
MODEL_PHOTOS: dict[str, list[str]] = {
    "courage":   ["photos/courage_1.jpg",  "photos/courage_2.jpg",  "photos/courage_3.jpg"],
    "free_318":  ["photos/free_318_1.jpg", "photos/free_318_2.jpg"],
    "free_plus": ["photos/free_plus_1.jpg","photos/free_plus_2.jpg"],
    "m817":      ["photos/m817_1.jpg",     "photos/m817_2.jpg"],
    "taishan":   ["photos/taishan_1.jpg",  "photos/taishan_2.jpg"],
}


# Fallback-ответ если Claude API временно недоступен
_FALLBACK_REPLIES = {
    "ru": "Одну секунду, уточняю информацию. Если срочно — позвоните нам: +998 98 444 05 44",
    "uz": "Bir daqiqa, ma'lumotni aniqlayman. Shoshilinch bo'lsa: +998 98 444 05 44",
    "en": "One moment please. For urgent matters call us: +998 98 444 05 44",
}


# ══════════════════════════════════════════════════════════════════════════════
# СОСТОЯНИЕ  (in-memory, не персистируется — для сессии)
# ══════════════════════════════════════════════════════════════════════════════

_manual_sent:   dict[int, float] = {}   # chat_id → timestamp ручного ответа
_bot_sent:      dict[int, float] = {}   # chat_id → timestamp последнего бот-ответа
_processed_ids: set[int]         = set()  # message_id уже обработанных сообщений
_tg_loaded:     set[int]         = set()  # chat_id для которых уже загружена история

_msg_buffer:    dict[int, list]           = {}  # chat_id → [text, ...] буфер входящих
_msg_tasks:     dict[int, asyncio.Task]   = {}  # chat_id → задача debounce

_td_state: dict[int, dict] = {}   # состояние флоу тест-драйва по chat_id
_TD_FLOW_TIMEOUT = 30 * 60        # сек — заброшенный флоу автоматически сбрасывается

_active_model: dict[int, tuple] = {}   # chat_id → (model_key, timestamp_last_mentioned)
_ACTIVE_MODEL_TTL = 30 * 60           # сек — активная модель сбрасывается после 30 мин молчания

me_id:       int   = 0
_startup_ts: float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _is_paused(chat_id: int) -> bool:
    """Менеджер недавно ответил вручную — бот молчит."""
    return time.time() - _manual_sent.get(chat_id, 0) < PAUSE_AFTER_MANUAL_MIN * 60


def _can_send(chat_id: int, cooldown_min: int = ANTISPAM_COOLDOWN_MIN) -> bool:
    """Антиспам — не отвечать слишком часто."""
    return time.time() - _bot_sent.get(chat_id, 0) > cooldown_min * 60


def _mark_sent(chat_id: int):
    _bot_sent[chat_id] = time.time()
    # Периодически чистим устаревшие записи чтобы dict не рос бесконечно
    if len(_bot_sent) > 500:
        _cleanup_old_timestamps()


def _cleanup_old_timestamps():
    """Удаляет записи старше 2 часов из in-memory словарей."""
    cutoff = time.time() - 7200  # 2 часа
    stale_bot    = [k for k, v in _bot_sent.items()    if v < cutoff]
    stale_manual = [k for k, v in _manual_sent.items() if v < cutoff]
    for k in stale_bot:
        _bot_sent.pop(k, None)
    for k in stale_manual:
        _manual_sent.pop(k, None)
    if stale_bot or stale_manual:
        log.debug("Очищено временных меток: bot=%d manual=%d", len(stale_bot), len(stale_manual))


def _is_opted_out(chat_id: int) -> bool:
    """Клиент попросил не писать — никогда не отвечать."""
    return bool(get_client_info(chat_id).get("opted_out"))


# ══════════════════════════════════════════════════════════════════════════════
# PHONE VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

_UZ_MOBILE_PREFIXES = frozenset({
    "90", "91", "93", "94", "95", "97", "98", "99",   # мобильные операторы UZ
    "33", "55", "77", "88",                             # виртуальные/MVNO
    "71",                                               # Ташкент городской
})


def validate_phone(text: str) -> tuple[bool, str]:
    """
    Валидирует и нормализует номер телефона.
    Возвращает (is_valid, e164_normalized).
    normalized — пустая строка если невалидно.

    Поддерживает форматы:
      +998901234567  →  +998901234567
      998901234567   →  +998901234567
      0901234567     →  +998901234567
      901234567      →  +998901234567  (если UZ-префикс)
      +7 705 ...     →  +7705...       (казахстанский/российский)
    """
    digits = re.sub(r"[\s\-\(\)\+\.]", "", text.strip())
    if not digits or not digits.isdigit():
        return False, ""

    # +998 XX XXXXXXX → 12 цифр
    if digits.startswith("998") and len(digits) == 12:
        return True, f"+{digits}"

    # 0XX XXXXXXX → 10 цифр (с лидирующим нулём)
    if digits.startswith("0") and len(digits) == 10:
        return True, f"+998{digits[1:]}"

    # XX XXXXXXX → 9 цифр (местный без 0), только UZ-префиксы
    if len(digits) == 9 and digits[:2] in _UZ_MOBILE_PREFIXES:
        return True, f"+998{digits}"

    # Международный формат (не UZ) — принимаем 10-15 цифр
    if 10 <= len(digits) <= 15:
        return True, f"+{digits}"

    return False, ""


# ══════════════════════════════════════════════════════════════════════════════
# MODEL CONTEXT TRACKING
# ══════════════════════════════════════════════════════════════════════════════

def _update_active_model(chat_id: int, model: str):
    """Запоминает какую модель клиент упомянул последней."""
    _active_model[chat_id] = (model, time.time())


def _get_active_model(chat_id: int) -> str | None:
    """Возвращает активную модель если она упоминалась в последние 30 мин."""
    entry = _active_model.get(chat_id)
    if not entry:
        return None
    model, ts = entry
    if time.time() - ts > _ACTIVE_MODEL_TTL:
        _active_model.pop(chat_id, None)
        return None
    return model


def _resolve_model(text: str, chat_id: int, messages: list) -> str | None:
    """
    Определяет модель которая интересует клиента — 4 уровня приоритета:
      1. Явное упоминание в текущем сообщении (одна модель)
      2. Активная модель в памяти (< 30 мин с момента упоминания)
      3. Профиль клиента в БД (info["model"])
      4. Поиск по последним 10 сообщениям клиента в истории
    Если в тексте 2+ модели — клиент сравнивает, возвращаем None.
    """
    # 1. Текущее сообщение
    all_in_text = get_all_models_from_text(text)
    if len(all_in_text) == 1:
        return all_in_text[0]
    if len(all_in_text) > 1:
        # Несколько моделей → сравнение, не запоминаем
        return None

    # 2. Активная память (недавно упомянутая)
    active = _get_active_model(chat_id)
    if active:
        return active

    # 3. БД — профиль клиента
    info = get_client_info(chat_id)
    if info.get("model"):
        return info["model"]

    # 4. История — ищем в последних 10 сообщениях клиента
    for msg in reversed(messages[-10:]):
        if msg.get("role") == "user":
            found = get_all_models_from_text(msg.get("content", ""))
            if len(found) == 1:
                return found[0]

    return None


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Разбивает длинный текст на части не длиннее limit символов."""
    parts = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


async def _get_name(entity) -> str:
    try:
        if isinstance(entity, User):
            parts = [entity.first_name or "", entity.last_name or ""]
            return " ".join(p for p in parts if p).strip()
    except Exception:
        pass
    return "Клиент"


async def _get_name_from_event(event) -> str:
    try:
        return await _get_name(await event.get_sender())
    except Exception:
        return "Клиент"


async def _should_handle(event) -> bool:
    """Пропускаем только входящие из личных чатов после старта."""
    if event.out:
        return False
    if not event.is_private:
        return False
    if event.chat_id == me_id:
        return False
    if _is_paused(event.chat_id):
        return False
    if event.date.timestamp() < _startup_ts:
        return False
    return True


async def _already_answered(client: TelegramClient, chat_id: int, since_ts: float) -> bool:
    """Проверяет — ответил ли менеджер вручную после указанного timestamp."""
    if _manual_sent.get(chat_id, 0) >= since_ts:
        return True
    try:
        async for msg in client.iter_messages(chat_id, limit=5):
            if msg.out and msg.date.timestamp() >= since_ts:
                return True
            if msg.date.timestamp() < since_ts:
                break
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFY MANAGER
# ══════════════════════════════════════════════════════════════════════════════

async def _notify(client: TelegramClient, text: str):
    if not MANAGER_ID:
        log.info("[МЕНЕДЖЕР] %s", text)
        return
    result = await safe_send(client.send_message, MANAGER_ID, text)
    if result is None:
        log.warning("Не удалось отправить уведомление менеджеру")


# ══════════════════════════════════════════════════════════════════════════════
# LOAD TG HISTORY
# ══════════════════════════════════════════════════════════════════════════════

async def _load_tg_history(client: TelegramClient, chat_id: int):
    """Загружает историю из Telegram при первом обращении к чату."""
    if chat_id in _tg_loaded:
        return
    _tg_loaded.add(chat_id)
    if has_messages(chat_id):
        return
    try:
        msgs = []
        async for msg in client.iter_messages(chat_id, limit=TG_HISTORY_LIMIT):
            if msg.text:
                msgs.append({"role": "assistant" if msg.out else "user",
                             "content": msg.text})
        msgs.reverse()
        for m in msgs:
            add_message({}, chat_id, m["role"], m["content"])
        log.info("TG история: %d сообщений из чата %d", len(msgs), chat_id)
    except Exception as e:
        log.warning("TG история [%d]: %s", chat_id, e)


# ══════════════════════════════════════════════════════════════════════════════
# SENDERS
# ══════════════════════════════════════════════════════════════════════════════

MAPS_LINK        = "https://maps.app.goo.gl/iLfVr5HWjYJShQsK8"
YANDEX_MAPS_LINK = (
    f"https://yandex.uz/maps/?ll={LOCATION_LON},{LOCATION_LAT}"
    f"&z=17&pt={LOCATION_LON},{LOCATION_LAT},pm2rdm"
)

# Подпись к venue-карточке — ссылки прямо в одном сообщении
_LOCATION_CAPTION = (
    f"📍 Яндекс Карты: {YANDEX_MAPS_LINK}\n"
    f"📍 Google Maps: {MAPS_LINK}"
)


async def _send_location(client: TelegramClient, chat_id: int):
    """
    Отправляет локацию двумя сообщениями:
      1. Telegram venue-карточка (пин на карте)
      2. Яндекс + Google Maps ссылки

    Два режима venue-карточки:
      A. LOCATION_MSG_ID > 0 — берём venue из канала (как прайс),
         re-upload без атрибуции. Менеджер может обновить адрес сам,
         просто опубликовав новую локацию в канале и поменяв ID.
      B. LOCATION_MSG_ID == 0 — venue строится из координат в коде
         (LOCATION_LAT / LOCATION_LON).

    Fallback во всех случаях: если venue не отправился — только ссылки.
    """
    venue_ok = False
    peer = await client.get_input_entity(chat_id)

    # ── Режим A: venue из канала (как прайс) ──────────────────────────────
    if LOCATION_MSG_ID > 0:
        try:
            channel = await client.get_entity(PRICE_CHANNEL)
            msgs    = await client.get_messages(channel, ids=[LOCATION_MSG_ID])
            msg     = msgs[0] if msgs else None

            if msg and isinstance(msg.media, (MessageMediaVenue, MessageMediaGeo)):
                media = msg.media
                if isinstance(media, MessageMediaVenue):
                    geo   = media.geo
                    venue_media = InputMediaVenue(
                        geo_point=InputGeoPoint(lat=geo.lat, long=geo.long),
                        title=media.title or "TAT AUTO",
                        address=media.address or "г. Ташкент, ул. Шота Руставели 77",
                        provider=media.provider or "",
                        venue_id=media.venue_id or "",
                        venue_type=media.venue_type or "",
                    )
                else:  # MessageMediaGeo — просто гео без названия
                    geo   = media.geo
                    venue_media = InputMediaVenue(
                        geo_point=InputGeoPoint(lat=geo.lat, long=geo.long),
                        title="TAT AUTO",
                        address="г. Ташкент, ул. Шота Руставели 77",
                        provider="", venue_id="", venue_type="",
                    )

                await client(SendMediaRequest(
                    peer=peer, media=venue_media,
                    message="", random_id=random.randint(1, 2**63),
                ))
                venue_ok = True
                log.info("Локация (канал msg=%d) -> %d", LOCATION_MSG_ID, chat_id)

        except Exception as e:
            log.warning("Локация из канала [%d]: %s — переключаюсь на код", chat_id, e)

    # ── Режим B: venue из координат в коде (fallback или LOCATION_MSG_ID==0) ─
    if not venue_ok:
        try:
            await client(SendMediaRequest(
                peer=peer,
                media=InputMediaVenue(
                    geo_point=InputGeoPoint(lat=LOCATION_LAT, long=LOCATION_LON),
                    title="TAT AUTO",
                    address="г. Ташкент, ул. Шота Руставели 77",
                    provider="", venue_id="", venue_type="",
                ),
                message="", random_id=random.randint(1, 2**63),
            ))
            venue_ok = True
            log.info("Локация (код) -> %d", chat_id)
        except Exception as e:
            log.warning("Локация venue [%d]: %s", chat_id, e)

    # ── Всегда: ссылки на карты ───────────────────────────────────────────
    await safe_send(client.send_message, chat_id, _LOCATION_CAPTION)
    if not venue_ok:
        log.info("Локация (только ссылки) -> %d", chat_id)


async def _send_price(client: TelegramClient, chat_id: int):
    """
    Отправляет прайс-лист клиенту напрямую — БЕЗ пересылки и атрибуции канала.
    Клиент видит сообщение как отправленное непосредственно Воей, а не из канала.

    Алгоритм:
      1. Получаем сообщение из канала через get_messages (не forward_messages)
      2. Если есть фото/документ — re-upload через send_file (не forward)
      3. Если только текст — send_message
      4. Fallback: текстовый прайс из БД
    """
    try:
        channel = await client.get_entity(PRICE_CHANNEL)
        msgs    = await client.get_messages(channel, ids=[PRICE_MSG_ID])
        msg     = msgs[0] if msgs else None

        if msg:
            caption = (msg.message or "").strip()

            if msg.photo:
                result = await safe_send(
                    client.send_file, chat_id, msg.photo,
                    caption=caption,
                )
                if result is not None:
                    log.info("Прайс (фото) отправлен напрямую -> %d", chat_id)
                    return

            elif msg.document:
                result = await safe_send(
                    client.send_file, chat_id, msg.document,
                    caption=caption,
                )
                if result is not None:
                    log.info("Прайс (документ) отправлен напрямую -> %d", chat_id)
                    return

            elif msg.message:
                result = await safe_send(client.send_message, chat_id, msg.message)
                if result is not None:
                    log.info("Прайс (текст из канала) отправлен -> %d", chat_id)
                    return

    except Exception as e:
        log.warning("Прайс: не удалось получить из канала (%s) — отправляю текст", e)

    # Fallback — текстовый прайс из БД (всегда актуален)
    await safe_send(client.send_message, chat_id, load_prices())
    log.info("Прайс (fallback текст) отправлен -> %d", chat_id)


async def _send_model_photos(client: TelegramClient, chat_id: int, model_key: str):
    photos = [p for p in MODEL_PHOTOS.get(model_key, []) if os.path.exists(p)]
    if not photos:
        log.warning("Фото не найдено: %s", model_key)
        return
    result = await safe_send(
        client.send_file,
        chat_id,
        photos[0] if len(photos) == 1 else photos,
    )
    if result is not None:
        log.info("Фото %s: %d шт. -> %d", model_key, len(photos), chat_id)


# ══════════════════════════════════════════════════════════════════════════════
# TEST DRIVE FLOW
# ══════════════════════════════════════════════════════════════════════════════

_TD_QUESTIONS = {
    "ask_datetime": {
        "ru": "Когда вам удобно подъехать? Работаем каждый день с 9 до 19.",
        "uz": "Qachon qulay? Har kuni 9 dan 19 gacha ishlaymiz.",
        "en": "When works for you? We're open daily 9am-7pm.",
    },
    "ask_phone": {
        "ru": "Отлично! Скиньте номер — я напомню за день до визита.",
        "uz": "Yaxshi! Raqamingizni yozing — bir kun oldin eslataman.",
        "en": "Great! Send your number — I'll text you a day before.",
    },
    "confirm": {
        "ru": "Записал вас! Ждём на Шота Руставели 77. До встречи!",
        "uz": "Yozdim! Shota Rustaveli 77 da kutamiz. Ko'rishguncha!",
        "en": "You're booked! See you at Shota Rustaveli 77!",
    },
}


async def _handle_testdrive_step(client: TelegramClient, chat_id: int,
                                  text: str, name: str, lang: str) -> bool:
    """Обрабатывает шаг флоу тест-драйва. Возвращает True если шаг обработан."""
    state = _td_state.get(chat_id)
    if not state:
        return False

    # Сбрасываем заброшенный флоу (клиент не отвечал 30+ минут)
    if time.time() - state.get("started", 0) > _TD_FLOW_TIMEOUT:
        log.info("Флоу тест-драйва истёк для chat_id=%d — сбрасываем", chat_id)
        del _td_state[chat_id]
        return False

    step = state.get("step")

    if step == "ask_datetime":
        state["datetime"] = text
        state["step"]     = "ask_phone"
        q = _TD_QUESTIONS["ask_phone"].get(lang, _TD_QUESTIONS["ask_phone"]["ru"])
        await safe_send(client.send_message, chat_id, q)
        add_message({}, chat_id, "assistant", q)   # BUG #4 fix
        _mark_sent(chat_id)
        return True

    if step == "ask_phone":
        # ── Валидация номера телефона ────────────────────────────────────────
        is_valid, normalized = validate_phone(text)

        if not is_valid:
            attempts = state.get("phone_attempts", 0) + 1
            state["phone_attempts"] = attempts

            if attempts >= 3:
                # После 3 неудач — принимаем как есть, не блокируем запись
                normalized = text
                log.warning("Принят невалидный телефон после %d попыток [%d]: %s",
                            attempts, chat_id, text)
            else:
                err = {
                    "ru": f"Похоже это не номер телефона. Введите в формате +998901234567 (попытка {attempts}/3).",
                    "uz": f"Bu telefon raqam emas. +998901234567 formatida kiriting ({attempts}/3 urinish).",
                    "en": f"Doesn't look like a phone number. Try +998901234567 format (attempt {attempts}/3).",
                }
                q = err.get(lang, err["ru"])
                await safe_send(client.send_message, chat_id, q)
                add_message({}, chat_id, "assistant", q)
                _mark_sent(chat_id)
                return True   # шаг не завершён, ждём правильного ввода

        state["phone"] = normalized
        info           = get_client_info(chat_id)
        model_key      = info.get("model", state.get("model", ""))
        model_name     = model_key.replace("_", " ").upper() if model_key else "не указана"

        save_appointment(
            chat_id=chat_id, name=name, model=model_name,
            datetime_str=state.get("datetime", "не указано"), phone=normalized,
        )
        set_client_stage(chat_id, "testdrive_scheduled")
        set_client_info(chat_id, testdrive_scheduled=True)

        confirm = _TD_QUESTIONS["confirm"].get(lang, _TD_QUESTIONS["confirm"]["ru"])
        await safe_send(client.send_message, chat_id, confirm)
        add_message({}, chat_id, "assistant", confirm)   # BUG #4 fix
        _mark_sent(chat_id)

        asyncio.create_task(_notify(client,
            f"🚗 ТЕСТ-ДРАЙВ ЗАПИСАН\n"
            f"Клиент: {name} (ID: {chat_id})\n"
            f"Модель: {model_name}\n"
            f"Дата/время: {state.get('datetime', '?')}\n"
            f"Телефон: {normalized}"))

        del _td_state[chat_id]
        return True

    return False


def _start_testdrive_flow(chat_id: int, lang: str) -> str:
    _td_state[chat_id] = {"step": "ask_datetime", "started": time.time()}
    return _TD_QUESTIONS["ask_datetime"].get(lang, _TD_QUESTIONS["ask_datetime"]["ru"])


# ══════════════════════════════════════════════════════════════════════════════
# CORE REPLY
# ══════════════════════════════════════════════════════════════════════════════

async def _reply(client: TelegramClient, chat_id: int, text: str, name: str,
                 lang: str = "ru", msg_ts: float = 0.0,
                 extra_context_override: str = ""):

    # ── 0. Opt-out проверка — НИКОГДА не отвечаем ──────────────────────────
    if _is_opted_out(chat_id):
        log.info("Пропуск (opted-out) -> %d", chat_id)
        return

    # ── 1. Менеджер уже ответил? ────────────────────────────────────────────
    if msg_ts and await _already_answered(client, chat_id, since_ts=msg_ts):
        log.info("Пропуск (менеджер ответил) -> %d", chat_id)
        return

    # ── 2. Загружаем TG историю при первом контакте ─────────────────────────
    await _load_tg_history(client, chat_id)

    # ── 3. Ранняя загрузка профиля клиента (нужна для шагов 3.1 и 3.2) ──────
    _early_info = get_client_info(chat_id)

    # ── 3.1. Пост-продажный запрос → переадресация живому менеджеру ─────────
    # Проверяем ДО optout, чтобы купивший клиент не получил прощальное сообщение.
    if detect_post_purchase(text, _early_info):
        routing_msg = "Передал ваш вопрос менеджеру."
        add_message({}, chat_id, "user", text)
        add_message({}, chat_id, "assistant", routing_msg)
        await safe_send(client.send_message, chat_id, routing_msg)
        _mark_sent(chat_id)
        log.info("🔧 Пост-продажа -> %s (%d): %s", name, chat_id, text[:60])
        asyncio.create_task(_notify(client,
            f"🔧 ПОСТ-ПРОДАЖА: {name} (ID: {chat_id})\n"
            f"Клиент уже купил и задаёт вопрос поддержки:\n«{text[:300]}»"))
        return

    # ── 3.2. Обнаруживаем opt-out в тексте сообщения ────────────────────────
    # Пропускаем проверку для известных покупателей — они не «уходят», а просят помощь.
    if not _early_info.get("purchased") and detect_optout(text):
        farewell = OPTOUT_FAREWELL.get(lang, OPTOUT_FAREWELL["ru"])
        set_client_info(chat_id, opted_out=True)
        add_message({}, chat_id, "user", text)
        add_message({}, chat_id, "assistant", farewell)
        await safe_send(client.send_message, chat_id, farewell)
        _mark_sent(chat_id)
        log.info("Opted-out: %s (%d) — отправлено прощание", name, chat_id)
        asyncio.create_task(_notify(client,
            f"⛔ ОТП-АУТ: {name} ({chat_id}) — «{text[:100]}»"))
        return

    # ── 3.3. Расчёт насия/кредита → роутим к @Deepaluz ──────────────────────
    if detect_nasiya_calc(text):
        nasiya_msg = NASIYA_ROUTING_MSG.get(lang, NASIYA_ROUTING_MSG["ru"])
        add_message({}, chat_id, "user", text)
        add_message({}, chat_id, "assistant", nasiya_msg)
        await safe_send(client.send_message, chat_id, nasiya_msg)
        _mark_sent(chat_id)
        log.info("💳 Насия-роутинг -> %s (%d)", name, chat_id)
        asyncio.create_task(_notify(client,
            f"💳 РАСЧЁТ НАСИЯ: {name} (ID: {chat_id})\n«{text[:200]}»"))
        return

    # ── 4. Флоу тест-драйва ─────────────────────────────────────────────────
    if await _handle_testdrive_step(client, chat_id, text, name, lang):
        add_message({}, chat_id, "user", text)
        return

    # ── 5. Сохраняем сообщение ──────────────────────────────────────────────
    add_message({}, chat_id, "user", text)
    messages = get_chat_history({}, chat_id, limit=20)

    # ── 6. Определяем намерения ─────────────────────────────────────────────
    asking_price      = is_asking_price(text)
    asking_location   = is_asking_location(text)
    asking_photo      = is_asking_photo(text)
    asking_testdrive  = detect_testdrive(text)
    model_key         = _resolve_model(text, chat_id, messages)
    competitor_key    = detect_competitor(text)
    has_tradein       = detect_tradein(text)
    buying_intent     = detect_buying_intent(text)
    only_greeting     = is_greeting_only(text)
    hesitation_level, h_score = detect_hesitation(text)

    # ── 7. Данные клиента ───────────────────────────────────────────────────
    info         = get_client_info(chat_id)
    is_new       = not info.get("greeted", False)
    display_name = info.get("name") or (name if name != "Клиент" else "")
    gender       = info.get("gender") or detect_gender(display_name)
    name_known   = bool(display_name or info.get("name_asked"))

    if gender and not info.get("gender"):
        set_client_info(chat_id, gender=gender)

    # Обновляем hesitation_score через EMA (экспоненциальное сглаживание)
    h_prev = float(info.get("hesitation_score", 0.0))
    if h_score > 0:
        # Новый сигнал — поднимаем score
        new_h = min(1.0, h_prev * 0.7 + h_score * 0.3)
    else:
        # Нейтральное сообщение — медленный decay
        new_h = max(0.0, h_prev * 0.85)
    if abs(new_h - h_prev) > 0.01:
        set_client_info(chat_id, hesitation_score=new_h)

    if model_key:
        _update_active_model(chat_id, model_key)
        # Явное упоминание в текущем тексте → обновляем даже если модель была другая
        # Из контекста (история/память) → сохраняем только если модель не задана
        explicit = get_all_models_from_text(text)
        if len(explicit) == 1:
            set_client_info(chat_id, model=model_key)
        elif not info.get("model"):
            set_client_info(chat_id, model=model_key)
    if get_client_stage(chat_id) == "new":
        set_client_stage(chat_id, "interested")

    # ── 8. Горячий лид — немедленно уведомляем менеджера ───────────────────
    if buying_intent:
        log.info("🔥 ГОРЯЧИЙ ЛИД: %s (%d)", name, chat_id)
        asyncio.create_task(_notify(client,
            f"🔥 ГОРЯЧИЙ ЛИД: {name} (ID: {chat_id})\n"
            f"Модель: {info.get('model', '?')}\n"
            f"Сообщение: «{text[:200]}»"))
        set_client_stage(chat_id, "interested")
        # Сбрасываем колебание — клиент готов купить
        if h_prev > 0:
            set_client_info(chat_id, hesitation_score=0.0)
            new_h = 0.0

    # Уведомляем менеджера при первом явном колебании
    if hesitation_level == "высокий" and h_prev < 0.4:
        log.info("🤔 РАЗДУМЫВАЕТ: %s (%d)", name, chat_id)
        asyncio.create_task(_notify(client,
            f"🤔 РАЗДУМЫВАЕТ: {name} (ID: {chat_id})\n"
            f"Модель: {info.get('model', '?')}\n"
            f"«{text[:200]}»"))

    # ── 9. Доп. контекст для AI ─────────────────────────────────────────────
    extra_parts = []

    if competitor_key:
        facts = get_competitor_facts(competitor_key, lang)
        extra_parts.append(
            f"[КОНКУРЕНТ: клиент упомянул {competitor_key.upper()}. "
            f"Используй эти факты естественно, без агрессии: {facts}]"
        )

    if has_tradein and not info.get("tradein_asked"):
        extra_parts.append(
            "[TRADE-IN: клиент упомянул свой автомобиль. "
            "Ненавязчиво спроси: модель, год, пробег.]"
        )
        set_client_info(chat_id, tradein_asked=True)
        asyncio.create_task(_notify(client,
            f"🔄 TRADE-IN: {name} ({chat_id})\n«{text[:200]}»"))

    if only_greeting and is_new:
        extra_parts.append(
            "[Клиент только поздоровался. "
            "Ответь его же приветствием, кратко представься как менеджер TAT AUTO "
            "и спроси чем можешь помочь — одним предложением.]"
        )

    # ── Контекст при колебании клиента ──────────────────────────────────────
    if hesitation_level == "высокий" or new_h > 0.7:
        extra_parts.append(
            "[Клиент откладывает решение. НЕ ДАВИ. "
            "Прими спокойно — скажи что готов помочь когда решит. "
            "Не предлагай тест-драйв и кредит прямо сейчас. "
            "Один короткий ответ без списков.]"
        )
    elif hesitation_level == "средний" or new_h > 0.4:
        if model_key and count_messages(chat_id) > 4:
            extra_parts.append(
                "[Клиент сравнивает варианты. Выдели одно уникальное преимущество "
                "нашего автомобиля. Предложи тест-драйв как способ принять решение — "
                "без давления, одним предложением.]"
            )
        else:
            extra_parts.append(
                "[Клиент сравнивает варианты. Выдели одно уникальное преимущество "
                "нашего автомобиля. Задай один уточняющий вопрос: какая модель интересует.]"
            )
    elif hesitation_level == "слабый" or new_h > 0.2:
        extra_parts.append(
            "[Клиент упомянул цену. Не оправдывайся — объясни ценность. "
            "Напомни об OFB кредите: взнос от 25%, срок до 60 мес., "
            "конкретную ставку назови исходя из возможного взноса.]"
        )

    if extra_context_override:
        extra_parts.insert(0, extra_context_override)

    extra_context = "\n".join(extra_parts)

    # ── 10. Тест-драйв → отдельный флоу ────────────────────────────────────
    if asking_testdrive and not info.get("testdrive_scheduled"):
        question = _start_testdrive_flow(chat_id, lang)
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        await safe_send(client.send_message, chat_id, question)
        _mark_sent(chat_id)
        add_message({}, chat_id, "assistant", question)
        return

    # ── 10.5. Локация — отправляем сразу, ДО текстового ответа ──────────────
    # Пин приходит первым, затем идёт typing + AI-ответ.
    if asking_location:
        await _send_location(client, chat_id)
        await asyncio.sleep(1)

    # ── 11. Задержка "печатает..." ──────────────────────────────────────────
    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    try:
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(delay)
    except Exception:
        await asyncio.sleep(delay)

    # ── 12. Повторная проверка (менеджер мог ответить пока думали) ──────────
    if msg_ts and await _already_answered(client, chat_id, since_ts=msg_ts):
        log.info("Пропуск (менеджер ответил пока думали) -> %d", chat_id)
        return

    # ── 13. Прайс → напрямую (без атрибуции канала) + квалификация лида ─────
    if asking_price:
        # Dedup: не отправляем прайс повторно если уже был в этом диалоге
        price_sent_before = any(
            "[Прайс-лист отправлен]" in m.get("content", "")
            for m in messages
        )
        if not price_sent_before:
            await _send_price(client, chat_id)
            add_message({}, chat_id, "assistant", "[Прайс-лист отправлен]")
            _mark_sent(chat_id)

        if is_new:
            set_client_info(chat_id, greeted=True)

        # ── Follow-up: квалифицируем лид после прайса ───────────────────────
        # Выбираем вопрос в зависимости от того, что уже известно о клиенте
        known_model  = info.get("model") or model_key
        known_budget = info.get("hesitation_score", 0) > 0.3  # proxy: обсуждал цену

        if known_model:
            follow_hint = (
                f"[Прайс только что отправлен. Модель клиента: {known_model}. "
                f"Задай ОДИН вопрос: бюджет, сроки или способ оплаты. "
                f"Не повторяй цены. Одно предложение.]"
            )
        else:
            follow_hint = (
                "[Прайс только что отправлен. Задай ОДИН вопрос: "
                "какая модель интересует больше — Courage или Free. "
                "Не повторяй цены. Одно предложение.]"
            )

        await asyncio.sleep(random.uniform(3, 5))  # пауза — прайс должен дойти первым

        fresh_history = get_chat_history({}, chat_id, limit=10)
        follow_up = await get_ai_reply(
            fresh_history,
            chat_id=chat_id,
            name=display_name,
            lang=lang,
            extra_context=follow_hint,
            is_new_client=is_new,
            name_known=name_known,
            gender=gender,
        )
        if follow_up:
            await safe_send(client.send_message, chat_id, follow_up)
            add_message({}, chat_id, "assistant", follow_up)
            _mark_sent(chat_id)
            log.info("Прайс + follow-up -> %d | %s", chat_id, follow_up[:60])

        asyncio.create_task(_update_summary_bg(chat_id))
        return

    # ── 14. AI ответ ─────────────────────────────────────────────────────────
    reply = await get_ai_reply(
        messages,
        chat_id=chat_id,
        name=display_name,
        lang=lang,
        extra_context=extra_context,
        is_new_client=is_new,
        name_known=name_known,
        gender=gender,
    )
    if not reply:
        # AI недоступен — отвечаем fallback-сообщением чтобы клиент не висел без ответа
        reply = _FALLBACK_REPLIES.get(lang, _FALLBACK_REPLIES["ru"])
        log.warning("AI вернул None — использую fallback для chat_id=%d", chat_id)
        # Уведомляем менеджера если AI упал (не чаще 1 раза в 10 мин на chat_id)
        asyncio.create_task(_notify(client,
            f"⚠️ AI API недоступен — отправлен fallback ответ клиенту ID {chat_id}"))

    # ── 15. Отправляем ответ ─────────────────────────────────────────────────
    await safe_send(client.send_message, chat_id, reply)
    add_message({}, chat_id, "assistant", reply)
    _mark_sent(chat_id)

    if is_new:
        set_client_info(chat_id, greeted=True)
    if not name_known:
        set_client_info(chat_id, name_asked=True)

    log.info("-> %s | %s | %s", name, lang, reply[:80])

    # ── 16. Фото модели ─────────────────────────────────────────────────────
    if asking_photo and model_key:
        await asyncio.sleep(1)
        await _send_model_photos(client, chat_id, model_key)

    # ── 17. Summary в фоне каждые 10 сообщений ──────────────────────────────
    asyncio.create_task(_update_summary_bg(chat_id))


async def _update_summary_bg(chat_id: int):
    """Фоновое обновление резюме (каждые 10 сообщений) и памяти фактов (каждые 4)."""
    total = count_messages(chat_id)
    if total < 4:
        return

    msgs = get_chat_history({}, chat_id, limit=30)

    # Факты о клиенте — каждые 4 сообщения (дёшево, Haiku)
    if total % 4 == 0:
        await extract_facts(msgs, chat_id)

    # Краткое резюме — каждые 10 сообщений (чуть дороже)
    if total >= 6 and total % 10 == 0:
        summary = await generate_summary(msgs)
        if summary:
            save_summary(chat_id, summary)


# ══════════════════════════════════════════════════════════════════════════════
# DEBOUNCE  — батчинг нескольких сообщений клиента в один ответ
# ══════════════════════════════════════════════════════════════════════════════

async def _debounce_and_reply(client: TelegramClient, chat_id: int,
                               name: str, lang: str, msg_ts: float):
    """
    Ждёт BATCH_WAIT_S секунд после последнего сообщения клиента.
    Если за это время пришли ещё сообщения — они уже в буфере.
    Объединяет все накопленные тексты и отвечает одним _reply().

    Вызывается через asyncio.create_task. Если задача отменена (пришло
    новое сообщение) — CancelledError поглощается и reply не вызывается.
    """
    try:
        await asyncio.sleep(BATCH_WAIT_S)
    except asyncio.CancelledError:
        return  # новое сообщение — перезапустят нас с обновлённым буфером

    texts = _msg_buffer.pop(chat_id, [])
    if not texts:
        return

    combined   = " ".join(texts)
    extra_ctx  = ""
    if len(texts) > 1:
        extra_ctx = (
            f"[Клиент написал {len(texts)} сообщения подряд: «{combined[:400]}». "
            f"Ответь на все вопросы в одном сообщении естественно — без упоминания паузы.]"
        )
        log.info("Дебаунс: объединено %d сообщ. от %s (%d)", len(texts), name, chat_id)

    await _reply(client, chat_id, combined, name, lang,
                 msg_ts=msg_ts, extra_context_override=extra_ctx)


# ══════════════════════════════════════════════════════════════════════════════
# MISSED MESSAGES  (сообщения пока бот был выключен)
# ══════════════════════════════════════════════════════════════════════════════

async def reply_to_missed(client: TelegramClient):
    """При старте обрабатывает непрочитанные сообщения в личных чатах (не старше 12 ч)."""
    log.info("Проверяю пропущенные сообщения...")
    now       = time.time()
    max_age_s = MISSED_MSG_MAX_AGE_H * 3600
    count     = 0

    try:
        async for dialog in client.iter_dialogs():
            if not dialog.is_user:
                continue
            if dialog.unread_count == 0:
                continue

            chat_id = dialog.id
            if chat_id == me_id:
                continue

            # Opted-out — пропускаем
            if _is_opted_out(chat_id):
                continue

            try:
                unread_msgs = []
                async for msg in client.iter_messages(chat_id,
                                                      limit=dialog.unread_count + 5):
                    if msg.out:
                        break
                    if not msg.text:
                        continue
                    if now - msg.date.timestamp() > max_age_s:
                        break
                    unread_msgs.append(msg)

                if not unread_msgs:
                    continue

                unread_msgs.reverse()
                last_ts = unread_msgs[-1].date.timestamp()

                if await _already_answered(client, chat_id, since_ts=last_ts):
                    continue

                try:
                    entity = await client.get_entity(chat_id)
                    name   = await _get_name(entity)
                except Exception:
                    name = "Клиент"

                # Объединяем все пропущенные сообщения в один контекст
                combined = " | ".join(m.text for m in unread_msgs)
                lang     = detect_language(combined)

                await _load_tg_history(client, chat_id)
                # Сохраняем только уникальные пропущенные сообщения
                recent_texts = {m.get("content") for m in get_chat_history({}, chat_id, limit=5)}
                for m in unread_msgs:
                    if m.text not in recent_texts:
                        add_message({}, chat_id, "user", m.text)

                info = get_client_info(chat_id)
                if not info.get("name") and name != "Клиент":
                    set_client_info(chat_id, name=name, lang=lang)

                # Контекстная подсказка AI для множественных сообщений
                missed_context = ""
                if len(unread_msgs) > 1:
                    missed_context = (
                        f"[ПРОПУЩЕНО {len(unread_msgs)} СООБЩЕНИЯ пока бот был недоступен. "
                        f"Клиент написал их подряд: {combined[:400]}. "
                        f"Ответь на все вопросы в одном ответе естественно, "
                        f"как будто разговор не прерывался. Не упоминай паузу.]"
                    )
                    log.info("Пропущено %d сообщ.: %s (%d): %s",
                             len(unread_msgs), name, chat_id, combined[:60])
                else:
                    log.info("Пропущено: %s (%d): %s", name, chat_id, combined[:60])

                await _reply(client, chat_id, combined, name, lang,
                             msg_ts=0.0, extra_context_override=missed_context)
                count += 1
                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                log.error("Пропущено [%d]: %s", chat_id, e)

    except Exception as e:
        log.error("Пропущено (общая ошибка): %s", e)

    log.info("Пропущенные: обработано %d чатов", count)


# ══════════════════════════════════════════════════════════════════════════════
# FOLLOW-UP WORKER
# ══════════════════════════════════════════════════════════════════════════════
# ⛔ ОТКЛЮЧЁН — бот не пишет клиентам первым (требование #1).
# Код сохранён для возможного включения в будущем.
#
# async def followup_worker(client): ...
#


# ══════════════════════════════════════════════════════════════════════════════
# MANAGER COMMANDS  (работают через "Избранные")
# ══════════════════════════════════════════════════════════════════════════════

async def _cmd_clients(client: TelegramClient, chat_id: int):
    """Показывает воронку клиентов по стадиям."""
    all_clients = get_all_clients()
    stages = {
        "new":                 ("🆕 Новые",              []),
        "interested":          ("💬 Интерес",            []),
        "testdrive_scheduled": ("📅 Тест-драйв записан", []),
        "testdrive_done":      ("✅ Тест-драйв прошёл",  []),
        "deal":                ("🎉 Сделка",             []),
    }
    opted_out_count = 0

    for cid_str, info in all_clients.items():
        if info.get("opted_out"):
            opted_out_count += 1
            continue
        stage = info.get("stage", "new")
        if stage not in stages:
            stage = "new"
        nm    = info.get("name") or f"ID {cid_str}"
        model = (info.get("model") or "?").replace("_", " ").upper()
        stages[stage][1].append(f"• {nm} ({model})")

    lines = ["📊 База клиентов:\n"]
    for _, (label, clients) in stages.items():
        if clients:
            lines.append(f"{label} — {len(clients)} чел.")
            lines.extend(clients[:10])
            if len(clients) > 10:
                lines.append(f"  ...ещё {len(clients) - 10}")
            lines.append("")
    if opted_out_count:
        lines.append(f"⛔ Отписались: {opted_out_count} чел.")

    await safe_send(client.send_message, chat_id, "\n".join(lines) or "Клиентов нет.")


async def _cmd_testdrives(client: TelegramClient, chat_id: int):
    """Показывает предстоящие записи на тест-драйв."""
    apps = get_upcoming_appointments()
    if not apps:
        await safe_send(client.send_message, chat_id, "Записей нет.")
        return
    lines = ["🚗 Записи на тест-драйв:\n"]
    for a in apps[-20:]:
        lines.append(
            f"• {a['name']} | {a['model']} | {a['datetime_str']} | {a.get('phone','?')}"
        )
    await safe_send(client.send_message, chat_id, "\n".join(lines))


async def _cmd_chat_history(client: TelegramClient, manager_id: int,
                            target_id: int, page: int = 0):
    """
    /чат ID [страница] — показывает историю переписки с клиентом.
    Постраничный вывод по 20 сообщений, с временными метками.
    """
    PAGE_SIZE = 20
    msgs = get_chat_history_full(target_id, limit=200)

    if not msgs:
        await safe_send(client.send_message, manager_id,
            f"❌ История пуста или клиент ID {target_id} не найден в базе.")
        return

    info  = get_client_info(target_id)
    name  = info.get("name") or f"ID {target_id}"
    stage = info.get("stage", "new")
    model = (info.get("model") or "?").replace("_", " ").upper()

    total_pages = max(1, (len(msgs) + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    chunk       = msgs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [
        f"💬 {name}  |  {model}  |  стадия: {stage}",
        f"Сообщений: {len(msgs)}  ·  Стр. {page + 1}/{total_pages}\n",
    ]

    for m in chunk:
        icon   = "👤" if m["role"] == "user" else "🤖"
        ts_str = time.strftime("%d.%m %H:%M", time.localtime(m["ts"]))
        content = m["content"].replace("\n", " ")[:280]
        lines.append(f"{icon} [{ts_str}]  {content}")

    # Навигация
    if total_pages > 1:
        lines.append("")
        nav = []
        if page > 0:
            nav.append(f"◀ /чат {target_id} {page - 1}")
        if page < total_pages - 1:
            nav.append(f"▶ /чат {target_id} {page + 1}")
        lines.append("  ·  ".join(nav))

    full_text = "\n".join(lines)
    for part in _split_message(full_text):
        await safe_send(client.send_message, manager_id, part)


async def _cmd_prices(client: TelegramClient, chat_id: int, text: str):
    """Просмотр и обновление цен. Использование: /цены [новый текст]"""
    parts = text.split(None, 1)
    if len(parts) < 2:
        await safe_send(client.send_message, chat_id,
            f"Текущие цены:\n\n{load_prices()}\n\nОбновить: /цены <новый текст>")
        return
    save_prices(parts[1].strip())
    await safe_send(client.send_message, chat_id, "✅ Цены обновлены.")


async def _cmd_knowledge(client: TelegramClient, chat_id: int, text: str):
    """
    /знание                    — статистика базы знаний
    /знание список             — все Q&A пары (до 20)
    /знание удалить ID         — удалить пару по номеру
    """
    parts = text.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else ""

    if sub == "список":
        pairs = get_all_training_qa(limit=20)
        if not pairs:
            await safe_send(client.send_message, chat_id,
                "📭 База знаний пуста. Добавь: /обучение вопрос | ответ")
            return
        lines = [f"📚 База знаний ({len(pairs)} записей):\n"]
        for p in pairs:
            ts = time.strftime("%d.%m", time.localtime(p["created_at"]))
            lines.append(f"[{p['id']}] {ts} {p['source']}\n"
                         f"В: {p['question'][:80]}\n"
                         f"О: {p['answer'][:120]}\n")
        for part in _split_message("\n".join(lines)):
            await safe_send(client.send_message, chat_id, part)
        return

    if sub == "удалить":
        if len(parts) < 3:
            await safe_send(client.send_message, chat_id,
                "❌ Укажи ID: /знание удалить 42")
            return
        try:
            qa_id = int(parts[2].strip())
            ok    = delete_training_qa(qa_id)
            msg   = f"✅ Запись #{qa_id} удалена." if ok else f"❌ Запись #{qa_id} не найдена."
            await safe_send(client.send_message, chat_id, msg)
        except ValueError:
            await safe_send(client.send_message, chat_id, "❌ ID должен быть числом.")
        return

    # По умолчанию — статистика
    total = count_training_qa()
    await safe_send(client.send_message, chat_id,
        f"📚 База знаний: {total} записей\n\n"
        f"Команды:\n"
        f"/знание список — показать все\n"
        f"/знание удалить ID — удалить запись\n"
        f"/обучение вопрос | ответ — добавить\n"
        f"/импорт ID — извлечь паттерны из диалога")


async def _cmd_add_training(client: TelegramClient, chat_id: int, text: str):
    """
    /обучение вопрос | ответ
    Добавляет пару вопрос-ответ в базу знаний.
    Разделитель: ' | ' (пробел-труба-пробел).
    """
    # Убираем команду из начала
    body = text[len("/обучение"):].strip()
    if "|" not in body:
        await safe_send(client.send_message, chat_id,
            "❌ Формат: /обучение вопрос | ответ\n\n"
            "Пример:\n/обучение Дорого! | Понимаю, давайте посчитаем кредит — "
            "при взносе 30% платёж всего X сум в месяц.")
        return
    parts    = body.split("|", 1)
    question = parts[0].strip()
    answer   = parts[1].strip()
    if len(question) < 5 or len(answer) < 5:
        await safe_send(client.send_message, chat_id,
            "❌ Вопрос и ответ должны быть не менее 5 символов.")
        return
    qa_id = save_training_qa(question, answer, source="manual")
    await safe_send(client.send_message, chat_id,
        f"✅ Добавлено в базу знаний (ID: {qa_id})\n\n"
        f"В: {question}\nО: {answer}")
    log.info("База знаний: добавлена пара #%d", qa_id)


async def _cmd_mark_success(client: TelegramClient, chat_id: int, text: str):
    """
    /успех ID [примечание]
    Помечает диалог как успешный и автоматически извлекает обучающие паттерны.
    Использовать для диалогов: сделка закрыта / лид передан / менеджер доволен.
    """
    parts = text.split(None, 2)
    if len(parts) < 2:
        await safe_send(client.send_message, chat_id,
            "Использование: /успех ID [примечание]\n"
            "Пример: /успех 123456789 закрыли сделку Free 318")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await safe_send(client.send_message, chat_id, "❌ ID должен быть числом.")
        return

    note = parts[2] if len(parts) > 2 else ""
    set_conversation_label(target_id, "success", note)

    info  = get_client_info(target_id)
    name  = info.get("name") or f"ID {target_id}"
    model = (info.get("model") or "?").replace("_", " ").upper()

    await safe_send(client.send_message, chat_id,
        f"✅ Диалог {name} ({target_id}) помечен как успешный.\n"
        f"Извлекаю обучающие паттерны...")

    msgs    = get_chat_history_full(target_id, limit=100)
    msgs_ai = [{"role": m["role"], "content": m["content"]} for m in msgs]
    count   = await extract_conversation_patterns(msgs_ai, target_id)

    await safe_send(client.send_message, chat_id,
        f"📚 Извлечено {count} паттернов из диалога {name} ({model}).\n"
        f"Они уже доступны боту как база знаний.")


async def _cmd_mark_deal(client: TelegramClient, chat_id: int, text: str):
    """
    /сделка ID [модель]
    Помечает клиента как купившего. После этого любой его вопрос про
    сервис/гарантию/документы → «Передал вопрос менеджеру.»
    """
    parts = text.split(None, 2)
    if len(parts) < 2:
        await safe_send(client.send_message, chat_id,
            "Использование: /сделка ID [модель]\n"
            "Пример: /сделка 123456789 Voyah Free 318")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await safe_send(client.send_message, chat_id, "❌ ID должен быть числом.")
        return

    model_note = parts[2] if len(parts) > 2 else ""
    set_client_info(target_id, purchased=True, stage="deal")
    if model_note:
        set_client_info(target_id, model=model_note.lower().replace(" ", "_"))

    info = get_client_info(target_id)
    name = info.get("name") or f"ID {target_id}"

    set_conversation_label(target_id, "success", f"deal:{model_note}")
    await safe_send(client.send_message, chat_id,
        f"🎉 {name} ({target_id}) — сделка зафиксирована.\n"
        f"Клиент переведён в режим пост-продажи. "
        f"Любой сервисный вопрос будет переадресован менеджеру.")
    log.info("Сделка: %s (%d) модель=%s", name, target_id, model_note)


async def _cmd_import_conversation(client: TelegramClient, chat_id: int, text: str):
    """
    /импорт ID [примечание]
    Извлекает паттерны продаж из указанного диалога и добавляет в базу знаний.
    Используй только для диалогов @Deepaluz, @Muhsin1906 или других успешных переписок.
    """
    parts = text.split(None, 2)
    if len(parts) < 2:
        await safe_send(client.send_message, chat_id,
            "Использование: /импорт ID [примечание]\n"
            "Пример: /импорт 123456789 диалог Deepaluz\n\n"
            "Бот извлечёт лучшие продающие паттерны из этого диалога.")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await safe_send(client.send_message, chat_id, "❌ ID должен быть числом.")
        return

    note = parts[2] if len(parts) > 2 else ""
    set_conversation_label(target_id, "success", note or "imported")

    msgs    = get_chat_history_full(target_id, limit=100)
    if not msgs:
        await safe_send(client.send_message, chat_id,
            f"❌ История диалога {target_id} пуста в базе.\n"
            f"Сначала убедись что клиент писал боту — история загружается при первом контакте.")
        return

    await safe_send(client.send_message, chat_id,
        f"⏳ Анализирую диалог {target_id} ({len(msgs)} сообщений)...")

    msgs_ai = [{"role": m["role"], "content": m["content"]} for m in msgs]
    count   = await extract_conversation_patterns(msgs_ai, target_id)

    if count > 0:
        await safe_send(client.send_message, chat_id,
            f"✅ Импортировано {count} паттернов из диалога {target_id}.\n"
            f"База знаний обновлена — бот сразу использует их в ответах.")
    else:
        await safe_send(client.send_message, chat_id,
            f"⚠️ Не удалось извлечь паттерны из диалога {target_id}.\n"
            f"Диалог слишком короткий или без продающих техник. "
            f"Добавь вручную: /обучение вопрос | ответ")


async def _cmd_help(client: TelegramClient, chat_id: int):
    """Показывает все доступные команды менеджера."""
    await safe_send(client.send_message, chat_id,
        "📋 Команды Воя-бота (Избранные):\n\n"
        "👥 Клиенты:\n"
        "/клиенты — воронка по стадиям\n"
        "/тестдрайвы — предстоящие записи\n"
        "/чат ID [стр] — история переписки\n"
        "/сделка ID [модель] — зафиксировать покупку\n"
        "/успех ID [примечание] — отметить успешный диалог\n\n"
        "📚 База знаний:\n"
        "/знание — статистика\n"
        "/знание список — показать Q&A пары\n"
        "/знание удалить ID — удалить пару\n"
        "/обучение вопрос | ответ — добавить Q&A\n"
        "/импорт ID — извлечь паттерны из диалога\n\n"
        "💰 Прайс:\n"
        "/цены — текущий прайс\n"
        "/цены <текст> — обновить\n\n"
        "✉️ Написать клиенту:\n"
        "@username текст\n"
        "+998901234567 текст"
    )


async def _handle_saved_messages(client: TelegramClient, event):
    """
    Ручная отправка клиенту из Избранных:
      @username текст сообщения
      +998901234567 текст сообщения
    """
    text       = event.raw_text or ""
    username_m = re.match(r"@(\w+)(.*)", text, re.DOTALL | re.IGNORECASE)
    phone_m    = re.match(r"(\+?[\d][\d\s\-]{7,14})(.*)", text, re.DOTALL)

    target_id   = None
    custom_text = ""

    try:
        if username_m:
            entity      = await client.get_entity(username_m.group(1))
            target_id   = entity.id
            custom_text = (username_m.group(2) or "").strip()
        elif phone_m:
            entity      = await client.get_entity(phone_m.group(1).strip())
            target_id   = entity.id
            custom_text = (phone_m.group(2) or "").strip()
    except Exception as e:
        await safe_send(client.send_message, me_id, f"❌ Не нашёл контакт: {e}")
        return

    if not target_id:
        return

    info = get_client_info(target_id)
    name = info.get("name", "")
    msg  = custom_text or (
        f"Здравствуйте{', ' + name if name else ''}! "
        "Хотел уточнить — остались ли вопросы по автомобилям."
    )
    result = await safe_send(client.send_message, target_id, msg)
    if result is not None:
        _manual_sent[target_id] = time.time()
        _bot_sent[target_id]    = time.time()
        add_message({}, target_id, "assistant", msg)
        log.info("Saved Messages -> %d: %s", target_id, msg[:60])


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    global me_id, _startup_ts

    # ── Инициализация ────────────────────────────────────────────────────────
    init_db()
    migrate_from_json()
    # Передаём текущий loop явно — избегаем asyncio.get_event_loop() в signal handler
    setup_shutdown_handlers(asyncio.get_running_loop())

    # StringSession — для Docker/сервера (сессия из env-переменной TG_SESSION).
    # Если TG_SESSION не задана — используем локальный файл agent_session.session.
    _tg_session = os.getenv("TG_SESSION", "").strip()
    session     = StringSession(_tg_session) if _tg_session else "agent_session"
    client      = TelegramClient(session, API_ID, API_HASH)

    # ── ВХОДЯЩИЕ СООБЩЕНИЯ ───────────────────────────────────────────────────
    # func=lambda e: e.is_private — группы не попадают в обработчик вообще
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_incoming(event):
        try:
            if not await _should_handle(event):
                return

            # Дедупликация по message_id
            msg_id = event.id
            if msg_id in _processed_ids:
                return
            _processed_ids.add(msg_id)
            # Чистим старые ID чтобы set не рос бесконечно
            if len(_processed_ids) > 2000:
                for old_id in sorted(_processed_ids)[:1000]:
                    _processed_ids.discard(old_id)

            text    = event.raw_text or ""
            chat_id = event.chat_id
            name    = await _get_name_from_event(event)

            # ── Медиа-сообщения без текста ─────────────────────────────────
            if not text:
                # Стикер → интерпретируем как сигнал намерения
                sticker = getattr(event.message, "sticker", None)
                if sticker is not None:
                    emoji = ""
                    for attr in (getattr(sticker, "attributes", None) or []):
                        if getattr(attr, "alt", None):
                            emoji = attr.alt
                            break
                    signal, synthetic_text, ai_hint = interpret_sticker(emoji)
                    log.info("<- STICKER %s [%s] %s (%d)",
                             emoji or "?", signal, name, chat_id)

                    # Обновляем данные клиента (без перезаписи lang — нет текста)
                    info    = get_client_info(chat_id)
                    lang    = info.get("lang") or "ru"   # берём из профиля
                    if not info.get("name") and name != "Клиент":
                        set_client_info(chat_id, name=name)

                    await _reply(client, chat_id, synthetic_text, name, lang,
                                 msg_ts=event.date.timestamp(),
                                 extra_context_override=ai_hint)
                    return

                # Шаринг контакта (кнопка «Поделиться номером»)
                # — только если клиент в шаге ask_phone тест-драйва
                if (isinstance(event.message.media, MessageMediaContact)
                        and chat_id in _td_state
                        and _td_state[chat_id].get("step") == "ask_phone"):
                    raw_phone = event.message.media.phone_number or ""
                    text = (f"+{raw_phone}"
                            if raw_phone and not raw_phone.startswith("+")
                            else raw_phone)
                else:
                    return  # другой медиа-тип — игнорируем

            if not text:
                return

            lang = detect_language(text)

            # Обновляем данные клиента
            info    = get_client_info(chat_id)
            updates = {"lang": lang}
            if not info.get("name") and name != "Клиент":
                updates["name"] = name
            set_client_info(chat_id, **updates)

            msg_ts = event.date.timestamp()
            log.info("← %s | %s | %s", name, lang, text[:80])

            # Флоу тест-драйва (шаг ввода телефона) — отвечаем немедленно,
            # не буферизуем: нельзя смешивать номер телефона с другим текстом.
            if (chat_id in _td_state
                    and _td_state[chat_id].get("step") == "ask_phone"):
                await _reply(client, chat_id, text, name, lang, msg_ts=msg_ts)
                return

            # Дебаунс — ждём BATCH_WAIT_S секунд перед ответом.
            # Если клиент пишет несколько сообщений подряд — объединяем в одно.
            _msg_buffer.setdefault(chat_id, []).append(text)
            old_task = _msg_tasks.get(chat_id)
            if old_task and not old_task.done():
                old_task.cancel()
            _msg_tasks[chat_id] = asyncio.create_task(
                _debounce_and_reply(client, chat_id, name, lang, msg_ts)
            )

        except Exception:
            log.exception("Ошибка on_incoming [chat=%s]", getattr(event, "chat_id", "?"))

    # ── ИСХОДЯЩИЕ СООБЩЕНИЯ (команды менеджера) ──────────────────────────────
    @client.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        chat_id = event.chat_id
        text    = event.raw_text or ""

        # ── Команды в Избранных (Saved Messages) ────────────────────────────
        if chat_id == me_id:
            if text.startswith("/справка") or text.startswith("/помощь"):
                await _cmd_help(client, chat_id)

            elif text.startswith("/клиенты"):
                await _cmd_clients(client, chat_id)

            elif text.startswith("/тестдрайвы"):
                await _cmd_testdrives(client, chat_id)

            elif text.startswith("/чат"):
                parts = text.split()
                if len(parts) < 2:
                    await safe_send(client.send_message, me_id,
                        "Использование: /чат ID [страница]\n"
                        "Пример: /чат 123456789\n"
                        "Следующая страница: /чат 123456789 1")
                else:
                    try:
                        target_id = int(parts[1])
                        page      = int(parts[2]) if len(parts) >= 3 else 0
                        await _cmd_chat_history(client, me_id, target_id, page)
                    except ValueError:
                        await safe_send(client.send_message, me_id,
                            "❌ ID клиента должен быть числом. Пример: /чат 123456789")

            elif text.startswith("/обучение"):
                await _cmd_add_training(client, chat_id, text)

            elif text.startswith("/знание"):
                await _cmd_knowledge(client, chat_id, text)

            elif text.startswith("/успех"):
                asyncio.create_task(_cmd_mark_success(client, chat_id, text))

            elif text.startswith("/сделка"):
                await _cmd_mark_deal(client, chat_id, text)

            elif text.startswith("/импорт"):
                asyncio.create_task(_cmd_import_conversation(client, chat_id, text))

            elif text.startswith("@") or re.match(r"\+?[\d][\d\s\-]{7,14}", text):
                await _handle_saved_messages(client, event)

            return

        # Команда обновления цен из любого чата
        if text.startswith("/цены"):
            await _cmd_prices(client, chat_id, text)
            return

        # Ручной ответ менеджера — ставим паузу
        _manual_sent[chat_id] = time.time()
        _bot_sent[chat_id]    = time.time()
        log.info("Ручной ответ -> %d (пауза %d мин)", chat_id, PAUSE_AFTER_MANUAL_MIN)

    # ── СТАРТ ────────────────────────────────────────────────────────────────
    await client.start()
    me    = await client.get_me()
    me_id = me.id

    log.info("═══════════════════════════════════════")
    log.info("TAT AUTO бот запущен: %s (@%s)", me.first_name, me.username)
    log.info("Менеджер ID: %s", MANAGER_ID or "не настроен")
    log.info("Режим: только ЛИЧНЫЕ чаты, группы игнорируются")
    log.info("Follow-up: ОТКЛЮЧЁН (бот не пишет первым)")
    log.info("═══════════════════════════════════════")

    # Фиксируем время старта ДО обработки пропущенных (BUG #1 fix)
    # Живые события с timestamp < _startup_ts будут игнорированы
    _startup_ts = time.time()

    # Обрабатываем сообщения пока бот был выключен
    await reply_to_missed(client)

    # Фоновые задачи
    asyncio.create_task(backup_worker())   # автобэкап каждые 6 часов
    # ⛔ followup_worker НЕ запускается — бот не пишет первым

    # Ждём завершения (Telegram disconnect или сигнал SIGINT/SIGTERM)
    run_task      = asyncio.create_task(client.run_until_disconnected())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    done, pending = await asyncio.wait(
        [run_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    await finalize(client)


if __name__ == "__main__":
    asyncio.run(main())
