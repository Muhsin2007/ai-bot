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
from telethon.tl.types import User
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaVenue, InputGeoPoint

from logger import log
from safe_telethon import safe_send
from shutdown import shutdown_event, setup_shutdown_handlers, backup_worker, finalize
from storage_db import (
    init_db, migrate_from_json,
    add_message, get_chat_history, has_messages,
    get_client_info, set_client_info, get_all_clients,
    get_client_stage, set_client_stage,
    load_prices, save_prices,
    save_summary,
    save_appointment, get_upcoming_appointments,
)
from ai_handler import (
    get_ai_reply, generate_summary, detect_language, detect_gender,
    detect_competitor, get_competitor_facts,
    detect_tradein, detect_testdrive,
    detect_optout, detect_buying_intent, is_greeting_only,
    OPTOUT_FAREWELL,
    get_followup_message,
    is_asking_price, is_asking_location, is_asking_photo, get_model_from_text,
)

load_dotenv()

API_ID     = int(os.getenv("TG_API_ID"))
API_HASH   = os.getenv("TG_API_HASH")
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

# Координаты салона
LOCATION_LAT = 41.271219
LOCATION_LON = 69.238094

# Прайс — пересылаем конкретное сообщение из канала
PRICE_CHANNEL = "deeeepal"
PRICE_MSG_ID  = 3

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
    "ru": "Одну секунду, уточняю информацию. Если срочно — позвоните нам: +998 95 004 97 49",
    "uz": "Bir daqiqa, ma'lumotni aniqlayman. Shoshilinch bo'lsa: +998 95 004 97 49",
    "en": "One moment please. For urgent matters call us: +998 95 004 97 49",
}


# ══════════════════════════════════════════════════════════════════════════════
# СОСТОЯНИЕ  (in-memory, не персистируется — для сессии)
# ══════════════════════════════════════════════════════════════════════════════

_manual_sent:   dict[int, float] = {}   # chat_id → timestamp ручного ответа
_bot_sent:      dict[int, float] = {}   # chat_id → timestamp последнего бот-ответа
_processed_ids: set[int]         = set()  # message_id уже обработанных сообщений
_tg_loaded:     set[int]         = set()  # chat_id для которых уже загружена история

_td_state: dict[int, dict] = {}   # состояние флоу тест-драйва по chat_id

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


def _is_opted_out(chat_id: int) -> bool:
    """Клиент попросил не писать — никогда не отвечать."""
    return bool(get_client_info(chat_id).get("opted_out"))


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

async def _send_location(client: TelegramClient, chat_id: int):
    """Отправляет venue-карточку с адресом салона."""
    try:
        await client(SendMediaRequest(
            peer=await client.get_input_entity(chat_id),
            media=InputMediaVenue(
                geo_point=InputGeoPoint(lat=LOCATION_LAT, long=LOCATION_LON),
                title="TAT AUTO",
                address="г. Ташкент, ул. Шота Руставели 77",
                provider="", venue_id="", venue_type="",
            ),
            message="",
            random_id=random.randint(1, 2**63),
        ))
        log.info("Локация отправлена -> %d", chat_id)
    except Exception as e:
        log.warning("Локация [%d]: %s", chat_id, e)


async def _send_price(client: TelegramClient, chat_id: int):
    """
    Пересылает прайс-лист из канала @deeeepal (сообщение #3).
    Fallback: текстовый прайс. Ровно ОДНО сообщение в обоих случаях.
    """
    try:
        channel = await client.get_entity(PRICE_CHANNEL)
        result = await safe_send(
            client.forward_messages,
            entity=chat_id,
            messages=PRICE_MSG_ID,
            from_peer=channel,
        )
        if result is not None:
            log.info("Прайс переслан из @%s/%d -> %d", PRICE_CHANNEL, PRICE_MSG_ID, chat_id)
            return
    except Exception as e:
        log.warning("Прайс: канал недоступен (%s) — отправляю текст", e)

    # Fallback — текст
    await safe_send(client.send_message, chat_id, load_prices())


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
        "ru": "На какую дату и время Вам удобно записаться?",
        "uz": "Qaysi sana va vaqt qulay bo'ladi?",
        "en": "What date and time works best for you?",
    },
    "ask_phone": {
        "ru": "Хорошо. Укажите Ваш номер телефона для подтверждения.",
        "uz": "Yaxshi. Tasdiqlash uchun telefon raqamingizni yozing.",
        "en": "Great. Please share your phone number for confirmation.",
    },
    "confirm": {
        "ru": "Записал. Ждём Вас в TAT AUTO на Шота Руставели 77.",
        "uz": "Yozib oldim. Shota Rustaveli 77 da TAT AUTO salonida kutamiz.",
        "en": "Booked. See you at TAT AUTO, Shota Rustaveli 77.",
    },
}


async def _handle_testdrive_step(client: TelegramClient, chat_id: int,
                                  text: str, name: str, lang: str) -> bool:
    """Обрабатывает шаг флоу тест-драйва. Возвращает True если шаг обработан."""
    state = _td_state.get(chat_id)
    if not state:
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
        state["phone"] = text
        info           = get_client_info(chat_id)
        model_key      = info.get("model", state.get("model", ""))
        model_name     = model_key.replace("_", " ").upper() if model_key else "не указана"

        save_appointment(
            chat_id=chat_id, name=name, model=model_name,
            datetime_str=state.get("datetime", "не указано"), phone=text,
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
            f"Телефон: {text}"))

        del _td_state[chat_id]
        return True

    return False


def _start_testdrive_flow(chat_id: int, lang: str) -> str:
    _td_state[chat_id] = {"step": "ask_datetime"}
    return _TD_QUESTIONS["ask_datetime"].get(lang, _TD_QUESTIONS["ask_datetime"]["ru"])


# ══════════════════════════════════════════════════════════════════════════════
# CORE REPLY
# ══════════════════════════════════════════════════════════════════════════════

async def _reply(client: TelegramClient, chat_id: int, text: str, name: str,
                 lang: str = "ru", msg_ts: float = 0.0):

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

    # ── 3. Обнаруживаем opt-out в тексте сообщения ──────────────────────────
    if detect_optout(text):
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

    # ── 4. Флоу тест-драйва ─────────────────────────────────────────────────
    if await _handle_testdrive_step(client, chat_id, text, name, lang):
        add_message({}, chat_id, "user", text)
        return

    # ── 5. Сохраняем сообщение ──────────────────────────────────────────────
    add_message({}, chat_id, "user", text)
    messages = get_chat_history({}, chat_id, limit=20)

    # ── 6. Определяем намерения ─────────────────────────────────────────────
    asking_price     = is_asking_price(text)
    asking_location  = is_asking_location(text)
    asking_photo     = is_asking_photo(text)
    asking_testdrive = detect_testdrive(text)
    model_key        = get_model_from_text(text)
    competitor_key   = detect_competitor(text)
    has_tradein      = detect_tradein(text)
    buying_intent    = detect_buying_intent(text)
    only_greeting    = is_greeting_only(text)

    # ── 7. Данные клиента ───────────────────────────────────────────────────
    info         = get_client_info(chat_id)
    is_new       = not info.get("greeted", False)
    display_name = info.get("real_name") or (name if name != "Клиент" else "")
    gender       = info.get("gender") or detect_gender(display_name)
    name_known   = bool(display_name or info.get("name_asked"))

    if gender and not info.get("gender"):
        set_client_info(chat_id, gender=gender)
    if model_key and not info.get("model"):
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

    extra_context = "\n".join(extra_parts)

    # ── 10. Тест-драйв → отдельный флоу ────────────────────────────────────
    if asking_testdrive and not info.get("testdrive_scheduled"):
        question = _start_testdrive_flow(chat_id, lang)
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        await safe_send(client.send_message, chat_id, question)
        _mark_sent(chat_id)
        add_message({}, chat_id, "assistant", question)
        return

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

    # ── 13. Прайс → пересылаем из канала (ONE message, no AI needed) ────────
    if asking_price:
        await _send_price(client, chat_id)
        add_message({}, chat_id, "assistant", "[Прайс-лист отправлен]")
        _mark_sent(chat_id)
        if is_new:
            set_client_info(chat_id, greeted=True)
        # Если одновременно просят и прайс и локацию
        if asking_location:
            await asyncio.sleep(1)
            await _send_location(client, chat_id)
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

    # ── 15. Отправляем ответ ─────────────────────────────────────────────────
    await safe_send(client.send_message, chat_id, reply)
    add_message({}, chat_id, "assistant", reply)
    _mark_sent(chat_id)

    if is_new:
        set_client_info(chat_id, greeted=True)
    if not name_known:
        set_client_info(chat_id, name_asked=True)

    log.info("-> %s | %s | %s", name, lang, reply[:80])

    # ── 16. Локация (после текстового ответа) ───────────────────────────────
    if asking_location:
        await asyncio.sleep(1)
        await _send_location(client, chat_id)

    # ── 17. Фото модели ─────────────────────────────────────────────────────
    if asking_photo and model_key:
        await asyncio.sleep(1)
        await _send_model_photos(client, chat_id, model_key)

    # ── 18. Summary в фоне каждые 10 сообщений ──────────────────────────────
    asyncio.create_task(_update_summary_bg(chat_id))


async def _update_summary_bg(chat_id: int):
    msgs = get_chat_history({}, chat_id, limit=30)
    if len(msgs) >= 6 and len(msgs) % 10 == 0:
        summary = await generate_summary(msgs)
        if summary:
            save_summary(chat_id, summary)


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
                combined = " | ".join(m.text for m in unread_msgs)
                last_ts  = unread_msgs[-1].date.timestamp()

                if await _already_answered(client, chat_id, since_ts=last_ts):
                    continue

                try:
                    entity = await client.get_entity(chat_id)
                    name   = await _get_name(entity)
                except Exception:
                    name = "Клиент"

                lang = detect_language(combined)

                await _load_tg_history(client, chat_id)
                # Добавляем только уникальные сообщения
                recent_texts = {m.get("content") for m in get_chat_history({}, chat_id, limit=5)}
                for m in unread_msgs:
                    if m.text not in recent_texts:
                        add_message({}, chat_id, "user", m.text)

                info = get_client_info(chat_id)
                if not info.get("name") and name != "Клиент":
                    set_client_info(chat_id, name=name, lang=lang)

                log.info("Пропущено: %s (%d): %s", name, chat_id, combined[:60])
                await _reply(client, chat_id, combined, name, lang, msg_ts=0.0)
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


async def _cmd_prices(client: TelegramClient, chat_id: int, text: str):
    """Просмотр и обновление цен. Использование: /цены [новый текст]"""
    parts = text.split(None, 1)
    if len(parts) < 2:
        await safe_send(client.send_message, chat_id,
            f"Текущие цены:\n\n{load_prices()}\n\nОбновить: /цены <новый текст>")
        return
    save_prices(parts[1].strip())
    await safe_send(client.send_message, chat_id, "✅ Цены обновлены.")


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
    setup_shutdown_handlers()

    client = TelegramClient("agent_session", API_ID, API_HASH)

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

            text = event.raw_text
            if not text:
                return

            chat_id = event.chat_id
            name    = await _get_name_from_event(event)
            lang    = detect_language(text)

            # Обновляем данные клиента
            info    = get_client_info(chat_id)
            updates = {"lang": lang}
            if not info.get("name") and name != "Клиент":
                updates["name"] = name
            set_client_info(chat_id, **updates)

            msg_ts = event.date.timestamp()
            log.info("← %s | %s | %s", name, lang, text[:80])

            await _reply(client, chat_id, text, name, lang, msg_ts=msg_ts)

        except Exception:
            log.exception("Ошибка on_incoming [chat=%s]", getattr(event, "chat_id", "?"))

    # ── ИСХОДЯЩИЕ СООБЩЕНИЯ (команды менеджера) ──────────────────────────────
    @client.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        chat_id = event.chat_id
        text    = event.raw_text or ""

        # Команды в Избранных
        if chat_id == me_id:
            if text.startswith("/клиенты"):
                await _cmd_clients(client, chat_id)
            elif text.startswith("/тестдрайвы"):
                await _cmd_testdrives(client, chat_id)
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
