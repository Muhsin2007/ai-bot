"""
Скрипт обучения бота на переписках с клиентами.

Запуск:
  python learn.py              — интерактивный просмотр и обучение
  python learn.py --auto       — автоматически добавить все диалоги менеджера
  python learn.py --show       — показать статистику обучения
"""

import json
import os
import sys
import argparse
from storage import (
    load_history,
    save_training_example,
    load_training_examples,
    get_chat_history,
)


def show_stats():
    """Показывает статистику обучающих данных."""
    examples = load_training_examples()
    history = load_history()

    print("\n" + "=" * 50)
    print("  СТАТИСТИКА ОБУЧЕНИЯ")
    print("=" * 50)
    print(f"Всего диалогов в истории:     {len(history)} чатов")
    print(f"Всего примеров для обучения:  {len(examples)}")

    sources = {}
    for ex in examples:
        src = ex.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    for src, count in sources.items():
        print(f"  - {src}: {count} примеров")

    unknown_file = "unknown_questions.json"
    if os.path.exists(unknown_file):
        try:
            with open(unknown_file, "r", encoding="utf-8") as f:
                unknown = json.load(f)
            pending = [q for q in unknown if q.get("status") == "pending"]
            print(f"\nВопросы без ответа (нужно обучить): {len(pending)}")
            for i, q in enumerate(pending[:5], 1):
                print(f"  {i}. {q['question'][:80]}")
            if len(pending) > 5:
                print(f"  ... и ещё {len(pending) - 5}")
        except Exception:
            pass

    print()


def interactive_learning():
    """Интерактивный режим: менеджер проходит по диалогам и добавляет правильные ответы."""
    history = load_history()

    print("\n" + "=" * 55)
    print("  ОБУЧЕНИЕ БОТА НА ПЕРЕПИСКАХ")
    print("=" * 55)
    print("Вы будете видеть вопросы клиентов.")
    print("Введите правильный ответ или нажмите Enter чтобы пропустить.\n")

    total_added = 0

    for chat_id, messages in history.items():
        print(f"\n--- Чат {chat_id} ({len(messages)} сообщений) ---")

        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg["role"] == "user":
                user_text = msg["content"]
                print(f"\n👤 Клиент: {user_text}")

                # Показываем ответ бота если есть
                bot_reply = None
                if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant":
                    bot_reply = messages[i + 1]["content"]
                    print(f"🤖 Бот:    {bot_reply[:200]}{'...' if len(bot_reply) > 200 else ''}")

                print()
                print("  [Enter]       — пропустить")
                print("  [y]           — добавить ответ бота как правильный")
                print("  [текст]       — ввести правильный ответ вручную")
                print("  [q]           — выйти")

                choice = input("> ").strip()

                if choice == "q":
                    print(f"\nДобавлено примеров: {total_added}")
                    return

                elif choice == "y" and bot_reply:
                    save_training_example(user_text, bot_reply, source="approved")
                    print("✅ Добавлено!")
                    total_added += 1

                elif choice and choice not in ["", "y"]:
                    save_training_example(user_text, choice, source="manual")
                    print("✅ Добавлено!")
                    total_added += 1

            i += 1

    print(f"\n✅ Обучение завершено. Добавлено примеров: {total_added}")


def answer_unknown_questions():
    """Позволяет менеджеру ответить на вопросы, которые бот не знал."""
    unknown_file = "unknown_questions.json"

    if not os.path.exists(unknown_file):
        print("Нет неизвестных вопросов.")
        return

    with open(unknown_file, "r", encoding="utf-8") as f:
        questions = json.load(f)

    pending = [(i, q) for i, q in enumerate(questions) if q.get("status") == "pending"]

    if not pending:
        print("Все вопросы уже обработаны!")
        return

    print(f"\n=== ВОПРОСЫ КОТОРЫЕ БОТ НЕ ЗНАЛ ({len(pending)} шт.) ===\n")

    for idx, (orig_i, q) in enumerate(pending, 1):
        print(f"\n[{idx}/{len(pending)}]")
        print(f"❓ Вопрос: {q['question']}")
        print(f"🤖 Бот ответил: {q['bot_reply'][:150]}...")
        print("\nВведите правильный ответ (или Enter чтобы пропустить):")

        answer = input("> ").strip()

        if answer:
            # Сохраняем как обучающий пример
            save_training_example(q["question"], answer, source="unknown_resolved")
            # Помечаем как обработанный
            questions[orig_i]["status"] = "answered"
            questions[orig_i]["answer"] = answer
            print("✅ Добавлено в обучение!")

    # Сохраняем обновлённые статусы
    with open(unknown_file, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)


def auto_import():
    """Автоматически добавляет все ответы бота из истории как примеры."""
    history = load_history()
    count = 0

    for chat_id, messages in history.items():
        for i in range(len(messages) - 1):
            if messages[i]["role"] == "user" and messages[i + 1]["role"] == "assistant":
                user_text = messages[i]["content"]
                bot_reply = messages[i + 1]["content"]

                # Пропускаем "не знаю" ответы
                skip_phrases = ["уточню у менеджера", "@deepaluz", "напишите напрямую"]
                if any(p in bot_reply.lower() for p in skip_phrases):
                    continue

                save_training_example(user_text, bot_reply, source="auto")
                count += 1

    print(f"✅ Автоматически импортировано {count} примеров из истории диалогов.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Обучение бота TAT AUTO")
    parser.add_argument("--show", action="store_true", help="Показать статистику")
    parser.add_argument("--auto", action="store_true", help="Авто-импорт из истории")
    parser.add_argument("--unknown", action="store_true", help="Ответить на неизвестные вопросы")
    args = parser.parse_args()

    if args.show:
        show_stats()
    elif args.auto:
        auto_import()
    elif args.unknown:
        answer_unknown_questions()
    else:
        show_stats()
        print("\nВыберите действие:")
        print("  1 — Обучать на переписках вручную")
        print("  2 — Ответить на неизвестные вопросы")
        print("  3 — Авто-импорт всех диалогов")
        print("  4 — Выйти")

        choice = input("\n> ").strip()
        if choice == "1":
            interactive_learning()
        elif choice == "2":
            answer_unknown_questions()
        elif choice == "3":
            auto_import()
        else:
            print("Выход.")
