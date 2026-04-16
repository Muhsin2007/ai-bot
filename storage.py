import json
import os
import logging
from threading import Lock
from datetime import datetime

logger = logging.getLogger(__name__)

HISTORY_FILE = "history.json"
TRAINING_FILE = "training_data.json"
MAX_MESSAGES = 30  # увеличили для лучшего контекста

_lock = Lock()


def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Ошибка загрузки истории: {e}")
            return {}
    return {}


def save_history(history: dict):
    with _lock:
        try:
            tmp_path = HISTORY_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, HISTORY_FILE)
        except IOError as e:
            logger.error(f"Ошибка сохранения истории: {e}")


API_HISTORY_LIMIT = 20   # сколько последних сообщений отправляем в Claude API


def get_chat_history(history: dict, chat_id: int, for_api: bool = False) -> list:
    """Возвращает историю чата + few-shot примеры из обучения.
    for_api=True — обрезает до API_HISTORY_LIMIT последних сообщений (экономия токенов).
    """
    base = history.get(str(chat_id), [])
    if for_api:
        base = base[-API_HISTORY_LIMIT:]
    few_shot = get_few_shot_examples(limit=3 if for_api else 5)
    if few_shot:
        return few_shot + base
    return base


def add_message(history: dict, chat_id: int, role: str, text: str):
    key = str(chat_id)
    if key not in history:
        history[key] = []
    history[key].append({"role": role, "content": text})
    history[key] = history[key][-MAX_MESSAGES:]
    save_history(history)


def save_training_example(user_question: str, correct_answer: str, source: str = "manual"):
    """Сохраняет пример для обучения."""
    with _lock:
        data = []
        if os.path.exists(TRAINING_FILE):
            try:
                with open(TRAINING_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = []

        # Проверяем дубликаты
        for ex in data:
            if ex.get("user") == user_question:
                # Обновляем если источник лучше
                if source in ("manual", "unknown_resolved") and ex.get("source") == "auto":
                    ex["assistant"] = correct_answer
                    ex["source"] = source
                    _write_training(data)
                return

        entry = {
            "user": user_question,
            "assistant": correct_answer,
            "source": source,
            "date": datetime.now().strftime("%d.%m.%Y"),
        }
        data.append(entry)
        _write_training(data)
        logger.info(f"Сохранён пример ({source}): {user_question[:60]}")


def save_training_from_manager(user_question: str, manager_answer: str, source: str = "manager"):
    """
    Сохраняет пример из переписки менеджера.
    Менеджеры @Muhsin1906 и @JavoxirTat — их ответы имеют высокий приоритет.
    """
    save_training_example(user_question, manager_answer, source=source)


def _write_training(data: list):
    try:
        tmp_path = TRAINING_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, TRAINING_FILE)
    except IOError as e:
        logger.error(f"Ошибка записи обучающих данных: {e}")


def load_training_examples() -> list:
    if not os.path.exists(TRAINING_FILE):
        return []
    try:
        with open(TRAINING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки примеров: {e}")
        return []


def get_few_shot_examples(limit: int = 5) -> list:
    """
    Возвращает лучшие примеры для few-shot.
    Приоритет: manual > manager > unknown_resolved > approved > auto
    """
    examples = load_training_examples()

    priority = {"manual": 0, "unknown_resolved": 1, "approved": 2, "auto": 3}
    # Сортируем по приоритету источника
    def get_priority(ex):
        src = ex.get("source", "auto")
        # Manager usernames имеют высший приоритет
        if src.startswith("@"):
            return -1
        return priority.get(src, 99)

    sorted_examples = sorted(examples, key=get_priority)
    recent = sorted_examples[:limit]

    messages = []
    for ex in recent:
        messages.append({"role": "user", "content": ex["user"]})
        messages.append({"role": "assistant", "content": ex["assistant"]})
    return messages


def get_stats() -> dict:
    """Возвращает статистику данных."""
    history = load_history()
    examples = load_training_examples()

    sources = {}
    for ex in examples:
        src = ex.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    unknown_count = 0
    if os.path.exists("unknown_questions.json"):
        try:
            with open("unknown_questions.json", "r", encoding="utf-8") as f:
                unknown = json.load(f)
            unknown_count = len([q for q in unknown if q.get("status") == "pending"])
        except Exception:
            pass

    return {
        "total_chats": len(history),
        "total_messages": sum(len(v) for v in history.values()),
        "training_examples": len(examples),
        "sources": sources,
        "unknown_pending": unknown_count,
    }
