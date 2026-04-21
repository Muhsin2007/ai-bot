"""
Безопасные обёртки для Telethon — обрабатывают FloodWait и другие ошибки.
Использование: await safe_send(client.send_message, chat_id, text)
"""
import asyncio

from logger import log

try:
    from telethon.errors import (
        FloodWaitError,
        ChatWriteForbiddenError,
        UserIsBlockedError,
        PeerIdInvalidError,
        InputUserDeactivatedError,
    )
except ImportError:
    # Заглушки если telethon не установлен
    FloodWaitError = Exception
    ChatWriteForbiddenError = Exception
    UserIsBlockedError = Exception
    PeerIdInvalidError = Exception
    InputUserDeactivatedError = Exception


async def safe_send(coro_func, *args, max_retries: int = 2, **kwargs):
    """
    Обёртка для всех client.send_* вызовов.
    Обрабатывает FloodWait, UserBlocked, ChatForbidden и др.

    Пример:
        await safe_send(client.send_message, chat_id, text)
        await safe_send(client.forward_messages, entity=chat_id, messages=3, from_peer=channel)
        await safe_send(client.send_file, chat_id, photos)
    """
    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)

        except FloodWaitError as e:
            wait = getattr(e, "seconds", 30) + 1
            log.warning("FloodWait: жду %d сек (попытка %d/%d)", wait, attempt + 1, max_retries + 1)
            if wait > 300:
                log.error("FloodWait слишком долгий (%d сек) — пропускаю отправку", wait)
                return None
            await asyncio.sleep(wait)
            if attempt == max_retries:
                try:
                    return await coro_func(*args, **kwargs)
                except Exception:
                    return None

        except UserIsBlockedError:
            log.warning("Пользователь заблокировал бота — %s", _target(args, kwargs))
            return None

        except (ChatWriteForbiddenError, PeerIdInvalidError, InputUserDeactivatedError) as e:
            log.warning("Нельзя отправить сообщение (%s): %s", type(e).__name__, _target(args, kwargs))
            return None

        except Exception as e:
            if "flood" in str(e).lower():
                await asyncio.sleep(10)
                continue
            log.error("Ошибка отправки (попытка %d): %s", attempt + 1, e)
            if attempt < max_retries:
                await asyncio.sleep(2)
            else:
                return None

    return None


def _target(args, kwargs) -> str:
    """Вспомогательная функция для логирования — извлекает chat_id."""
    if args:
        return str(args[0])
    return str(kwargs.get("entity", kwargs.get("chat_id", "?")))
