"""
SQLite-хранилище (замена storage.py).
Полностью совместимо по API — main.py меняет только строку импорта.

WAL-режим: параллельные чтения не блокируют запись.
Миграция из JSON запускается один раз при старте.
"""
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager

log = logging.getLogger("tat_auto")

DB_FILE        = "data/tat_auto.db"
MIGRATION_FLAG = "data/.migrated"   # файл-маркер: миграция уже была выполнена

DEFAULT_PRICES = (
    "Voyah Courage 650 — 473 000 000 сум\n"
    "Voyah Free 318 (2026) — 504 000 000 сум\n"
    "Voyah Free+ (2026) — 553 000 000 сум\n"
    "M-Hero M817 — 817 065 000 сум\n\n"
    "Банковский кредит (OFB «Автокредит Лёгкий»):\n"
    "  • Сумма: до 800 млн сум\n"
    "  • Срок: от 12 до 60 месяцев\n"
    "  • Первый взнос 25% → ставка 24,5% годовых\n"
    "  • Первый взнос 30% → ставка 23,9% годовых\n"
    "  • Первый взнос 40% → ставка 22,9% годовых\n"
    "  • Первый взнос 50% → ставка 21,9% годовых\n"
    "  • Документы: паспорт + договор покупки\n"
    "  • При взносе от 40%: + выписка по карте за 6 мес.\n\n"
    "Насия савдо (только для авто со счёт-справкой):\n"
    "  • M-Hero M817 — есть счёт-справка ✓\n"
    "  • Voyah Free 318 — есть счёт-справка ✓\n"
    "  • Первый взнос 30%, срок до 36 месяцев\n"
    "  • Voyah Courage, Free+, Taishan — насия савдо недоступно\n\n"
    "Тест-драйв: запись по телефону +998 95 004 97 49"
)

# Колонки таблицы clients (без extra_json)
_CLIENT_COLS = {
    "name", "lang", "gender", "stage", "model",
    "greeted", "name_asked", "testdrive_scheduled",
    "tradein_asked", "purchased", "opted_out",
}


# ══════════════════════════════════════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════════════════════════════════════

def init_db():
    """Создаёт таблицы и включает WAL-режим. Вызывать при старте."""
    os.makedirs("data", exist_ok=True)
    with _raw_conn() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS clients (
                chat_id     INTEGER PRIMARY KEY,
                name        TEXT,
                lang        TEXT,
                gender      TEXT,
                stage       TEXT DEFAULT 'new',
                model       TEXT,
                greeted     INTEGER DEFAULT 0,
                name_asked  INTEGER DEFAULT 0,
                testdrive_scheduled INTEGER DEFAULT 0,
                tradein_asked       INTEGER DEFAULT 0,
                purchased           INTEGER DEFAULT 0,
                opted_out           INTEGER DEFAULT 0,
                extra_json  TEXT    DEFAULT '{}',
                created_at  REAL,
                updated_at  REAL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id  INTEGER NOT NULL,
                role     TEXT    NOT NULL,
                content  TEXT    NOT NULL,
                ts       REAL    NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES clients(chat_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, ts);

            CREATE TABLE IF NOT EXISTS appointments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER,
                name         TEXT,
                model        TEXT,
                datetime_str TEXT,
                phone        TEXT,
                done         INTEGER DEFAULT 0,
                created_at   REAL
            );

            CREATE TABLE IF NOT EXISTS summaries (
                chat_id    INTEGER PRIMARY KEY,
                summary    TEXT,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS prices (
                id   INTEGER PRIMARY KEY CHECK(id = 1),
                text TEXT
            );

            CREATE TABLE IF NOT EXISTS client_facts (
                chat_id    INTEGER NOT NULL,
                fact_type  TEXT    NOT NULL,
                fact_value TEXT    NOT NULL,
                updated_at REAL    NOT NULL,
                PRIMARY KEY (chat_id, fact_type)
            );
        """)
    # Добавляем opted_out если столбец отсутствует (миграция существующих БД)
    try:
        with _raw_conn() as conn:
            conn.execute("ALTER TABLE clients ADD COLUMN opted_out INTEGER DEFAULT 0")
        log.info("Добавлен столбец opted_out")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            log.warning("ALTER TABLE clients (opted_out): %s", e)
        # иначе — столбец уже есть, всё нормально

    log.info("БД инициализирована: %s", DB_FILE)


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════════════════════

@contextmanager
def _raw_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY  (API совместим с storage.py)
# ══════════════════════════════════════════════════════════════════════════════

def load_history() -> dict:
    """Возвращает пустой dict — данные читаются из БД напрямую."""
    return {}


def save_history(_history: dict):
    """No-op — данные сохраняются в БД через add_message."""
    pass


def add_message(_history: dict, chat_id: int, role: str, content: str):
    """Добавляет сообщение в БД. Хранит последние 100 на чат."""
    with _raw_conn() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, time.time()),
        )
        # Чистим старые — оставляем последние 100
        conn.execute("""
            DELETE FROM messages
            WHERE chat_id = ?
              AND id NOT IN (
                  SELECT id FROM messages
                  WHERE chat_id = ?
                  ORDER BY ts DESC
                  LIMIT 100
              )
        """, (chat_id, chat_id))


def get_chat_history(_history: dict, chat_id: int, limit: int = 20) -> list:
    """Читает историю из БД (аргумент _history игнорируется)."""
    with _raw_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages"
            " WHERE chat_id = ? ORDER BY ts DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def get_chat_history_full(chat_id: int, limit: int = 200) -> list:
    """История с временными метками — для команды /чат менеджера."""
    with _raw_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, ts FROM messages"
            " WHERE chat_id = ? ORDER BY ts DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"], "ts": r["ts"]}
            for r in reversed(rows)]


def has_messages(chat_id: int) -> bool:
    """Проверяет есть ли уже история для этого чата."""
    with _raw_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return (row["cnt"] or 0) > 0


def count_messages(chat_id: int) -> int:
    """Возвращает полное количество сообщений для чата (без лимита)."""
    with _raw_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row["cnt"] or 0


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT DATA
# ══════════════════════════════════════════════════════════════════════════════

def _row_to_dict(row) -> dict:
    d = dict(row)
    extra = json.loads(d.pop("extra_json", None) or "{}")
    d.update(extra)
    return d


def load_client_data() -> dict:
    """Возвращает всех клиентов как {chat_id_str: info_dict}."""
    with _raw_conn() as conn:
        rows = conn.execute("SELECT * FROM clients").fetchall()
    return {str(r["chat_id"]): _row_to_dict(r) for r in rows}


def save_client_data(_data: dict):
    """No-op — используй set_client_info."""
    pass


def get_client_info(chat_id: int) -> dict:
    with _raw_conn() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return _row_to_dict(row) if row else {}


def set_client_info(chat_id: int, **kwargs):
    col_updates   = {k: v for k, v in kwargs.items() if k in _CLIENT_COLS}
    extra_updates = {k: v for k, v in kwargs.items() if k not in _CLIENT_COLS}

    with _raw_conn() as conn:
        row = conn.execute(
            "SELECT extra_json FROM clients WHERE chat_id = ?", (chat_id,)
        ).fetchone()

        if row is None:
            # Новый клиент — INSERT
            extra_json = json.dumps(extra_updates, ensure_ascii=False)
            cols = ["chat_id", "extra_json", "created_at", "updated_at"]
            vals = [chat_id, extra_json, time.time(), time.time()]
            for k, v in col_updates.items():
                cols.append(k)
                vals.append(v)
            placeholders = ", ".join("?" * len(cols))
            conn.execute(
                f"INSERT INTO clients ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        else:
            # Обновляем существующего
            existing_extra = json.loads(row["extra_json"] or "{}")
            existing_extra.update(extra_updates)
            extra_json = json.dumps(existing_extra, ensure_ascii=False)

            sets = ["extra_json = ?", "updated_at = ?"]
            vals = [extra_json, time.time()]
            for k, v in col_updates.items():
                sets.append(f"{k} = ?")
                vals.append(v)
            vals.append(chat_id)
            conn.execute(
                f"UPDATE clients SET {', '.join(sets)} WHERE chat_id = ?", vals
            )


def get_all_clients() -> dict:
    return load_client_data()


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT STAGES
# ══════════════════════════════════════════════════════════════════════════════

def set_client_stage(chat_id: int, stage: str):
    set_client_info(chat_id, stage=stage)


def get_client_stage(chat_id: int) -> str:
    return get_client_info(chat_id).get("stage", "new")


# ══════════════════════════════════════════════════════════════════════════════
# PRICES
# ══════════════════════════════════════════════════════════════════════════════

def load_prices() -> str:
    with _raw_conn() as conn:
        row = conn.execute("SELECT text FROM prices WHERE id = 1").fetchone()
    return row["text"] if row else DEFAULT_PRICES


def save_prices(text: str):
    with _raw_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO prices (id, text) VALUES (1, ?)", (text,)
        )


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARIES
# ══════════════════════════════════════════════════════════════════════════════

def load_summary(chat_id: int) -> str:
    with _raw_conn() as conn:
        row = conn.execute(
            "SELECT summary FROM summaries WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    return row["summary"] if row else ""


def save_summary(chat_id: int, summary: str):
    with _raw_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO summaries (chat_id, summary, updated_at) VALUES (?, ?, ?)",
            (chat_id, summary, time.time()),
        )


# ══════════════════════════════════════════════════════════════════════════════
# APPOINTMENTS
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# CLIENT FACTS  (долгосрочная память об интересах клиента)
# ══════════════════════════════════════════════════════════════════════════════

def save_client_facts(chat_id: int, facts: dict):
    """Upsert фактов о клиенте. Только непустые значения."""
    now = time.time()
    with _raw_conn() as conn:
        for fact_type, fact_value in facts.items():
            if fact_type and fact_value and str(fact_value).lower() != "null":
                conn.execute(
                    "INSERT OR REPLACE INTO client_facts"
                    " (chat_id, fact_type, fact_value, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (chat_id, str(fact_type), str(fact_value), now),
                )


def load_client_facts(chat_id: int, max_age_days: int = 30) -> dict:
    """Возвращает факты о клиенте не старше max_age_days дней."""
    if not chat_id:
        return {}
    cutoff = time.time() - max_age_days * 86400
    with _raw_conn() as conn:
        rows = conn.execute(
            "SELECT fact_type, fact_value FROM client_facts"
            " WHERE chat_id = ? AND updated_at > ?",
            (chat_id, cutoff),
        ).fetchall()
    return {r["fact_type"]: r["fact_value"] for r in rows}


def load_appointments() -> list:
    with _raw_conn() as conn:
        rows = conn.execute("SELECT * FROM appointments").fetchall()
    return [dict(r) for r in rows]


def save_appointment(chat_id: int, name: str, model: str,
                     datetime_str: str, phone: str = ""):
    with _raw_conn() as conn:
        conn.execute(
            "INSERT INTO appointments"
            " (chat_id, name, model, datetime_str, phone, done, created_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?)",
            (chat_id, name, model, datetime_str, phone, time.time()),
        )


def get_upcoming_appointments() -> list:
    with _raw_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM appointments WHERE done = 0 ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# MIGRATION  (JSON → SQLite, запускается один раз)
# ══════════════════════════════════════════════════════════════════════════════

def migrate_from_json():
    """Переносит данные из старых JSON-файлов в SQLite. Запускать один раз."""
    # WARN #8 fix: пропускаем если уже мигрировали
    if os.path.exists(MIGRATION_FLAG):
        return

    migrated_any = False

    # client_data.json
    if os.path.exists("client_data.json"):
        try:
            with open("client_data.json", encoding="utf-8") as f:
                data = json.load(f)
            for cid_str, info in data.items():
                try:
                    set_client_info(int(cid_str), **info)
                except Exception as e:
                    log.warning("Миграция клиента %s: %s", cid_str, e)
            log.info("Мигрировано клиентов: %d", len(data))
            migrated_any = True
        except Exception as e:
            log.error("Ошибка миграции client_data.json: %s", e)

    # chat_history.json
    if os.path.exists("chat_history.json"):
        try:
            with open("chat_history.json", encoding="utf-8") as f:
                history = json.load(f)
            total = 0
            for cid_str, msgs in history.items():
                try:
                    chat_id = int(cid_str)
                    # Чтобы не дублировать, пишем только если в БД ещё нет
                    if not has_messages(chat_id):
                        for msg in msgs:
                            add_message({}, chat_id, msg["role"], msg["content"])
                        total += len(msgs)
                except Exception as e:
                    log.warning("Миграция истории %s: %s", cid_str, e)
            log.info("Мигрировано сообщений: %d", total)
            migrated_any = True
        except Exception as e:
            log.error("Ошибка миграции chat_history.json: %s", e)

    # appointments.json
    if os.path.exists("appointments.json"):
        try:
            with open("appointments.json", encoding="utf-8") as f:
                apps = json.load(f)
            for app in apps:
                try:
                    with _raw_conn() as conn:
                        conn.execute(
                            "INSERT INTO appointments"
                            " (chat_id, name, model, datetime_str, phone, done, created_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                app.get("chat_id"), app.get("name"),
                                app.get("model"), app.get("datetime"),
                                app.get("phone", ""),
                                1 if app.get("done") else 0,
                                time.time(),
                            ),
                        )
                except Exception as e:
                    log.warning("Миграция appointment: %s", e)
            log.info("Мигрировано записей тест-драйва: %d", len(apps))
            migrated_any = True
        except Exception as e:
            log.error("Ошибка миграции appointments.json: %s", e)

    # summaries.json
    if os.path.exists("summaries.json"):
        try:
            with open("summaries.json", encoding="utf-8") as f:
                summaries = json.load(f)
            for cid_str, summary in summaries.items():
                try:
                    save_summary(int(cid_str), summary)
                except Exception as e:
                    log.warning("Миграция summary %s: %s", cid_str, e)
            log.info("Мигрировано резюме: %d", len(summaries))
            migrated_any = True
        except Exception as e:
            log.error("Ошибка миграции summaries.json: %s", e)

    # prices.json
    if os.path.exists("prices.json"):
        try:
            with open("prices.json", encoding="utf-8") as f:
                data = json.load(f)
            if "text" in data:
                save_prices(data["text"])
                log.info("Мигрированы цены из prices.json")
                migrated_any = True
        except Exception as e:
            log.error("Ошибка миграции prices.json: %s", e)

    if migrated_any:
        log.info("Миграция JSON → SQLite завершена.")
    else:
        log.info("JSON-файлы не найдены — миграция не нужна.")

    # Записываем флаг чтобы при следующем старте не проверять снова
    try:
        with open(MIGRATION_FLAG, "w", encoding="utf-8") as f:
            f.write("done")
    except Exception as e:
        log.warning("Не удалось записать флаг миграции: %s", e)
