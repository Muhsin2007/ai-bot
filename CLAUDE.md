# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**TAT AUTO** — a Telegram userbot (not a bot account) for an electric car dealership in Tashkent. It runs as a real user account via Telethon, auto-replies to incoming private messages using Claude AI, handles test-drive bookings, credit calculations, and logs leads to Google Sheets.

## Running the project

```bash
# Install dependencies
pip install -r requirements.txt
# ffmpeg must be installed separately (system package, not pip)

# First-time Telegram auth — run once locally, copy TG_SESSION to .env
python auth.py

# Convert an existing .session file to a string
python convert_session.py

# Verify setup
python test.py

# Start the bot
python main.py

# Training tools
python learn.py           # interactive menu
python learn.py --show    # print training stats
python learn.py --auto    # auto-import manager conversations
python learn.py --unknown # answer flagged unknown questions
```

## Required .env keys

```
TG_API_ID=
TG_API_HASH=
TG_SESSION=          # string session from auth.py
MANAGER_ID=          # Telegram user ID of the manager account
ANTHROPIC_API_KEY=
OPENAI_API_KEY=      # for Whisper voice transcription
GOOGLE_SHEET_ID=     # optional — Google Sheets lead logging
```

## Architecture

All logic lives in flat Python files (no packages). The flow is:

```
main.py
  └─ Telethon event handler (new_message)
       ├─ Debounce buffer (BATCH_WAIT_S=2.5s — waits for rapid follow-ups)
       ├─ Gate checks: opted-out / manual-pause / antispam cooldown
       ├─ ai_handler.py — all Claude API calls and intent detection
       └─ storage_db.py — SQLite persistence
```

### Key files

| File | Role |
|------|------|
| `main.py` | Entry point. Telethon client, event handlers, test-drive flow state machine, message routing |
| `ai_handler.py` | Claude API (`get_ai_reply`), language detection (heuristic, no API), all `detect_*` intent functions, competitor facts |
| `storage_db.py` | SQLite via WAL mode (`data/tat_auto.db`). All client state, message history, training Q&A, prices, summaries |
| `safe_telethon.py` | Retry wrapper for all `client.send_*` calls — handles FloodWait, UserBlocked, etc. |
| `shutdown.py` | SIGINT/SIGTERM handlers, 6-hour ZIP backup worker, graceful disconnect |
| `sheets_handler.py` | Google Sheets integration — writes leads, test-drives, purchases |
| `voice_handler.py` | OGG→MP3 conversion via ffmpeg, Whisper transcription |
| `auth.py` | One-time local script to generate `TG_SESSION` string |
| `learn.py` | Manager tool to add training examples; uses legacy `storage.py` (not `storage_db`) |
| `logger.py` | RotatingFileHandler to `logs/bot.log`, shared as `from logger import log` |

### Storage design

`storage_db.py` is a drop-in replacement for the older `storage.py` (JSON-based). `storage_db` uses SQLite with WAL mode. Named columns in `clients` cover frequently accessed fields; anything else goes into `extra_json`. Migration from JSON runs once at startup (flagged by `data/.migrated`).

`learn.py` still imports from the old `storage.py` — this is intentional (legacy training tool, not yet migrated).

### In-memory session state (main.py, not persisted)

- `_manual_sent` — timestamps of manager manual replies; bot stays silent for `PAUSE_AFTER_MANUAL_MIN` (30 min) after each
- `_bot_sent` — antispam: bot won't reply to the same chat more than once per `ANTISPAM_COOLDOWN_MIN` (2 min)
- `_td_state` — test-drive flow state per chat (multi-step: name → phone → date → confirm), auto-resets after 30 min idle
- `_active_model` — last car model mentioned per chat, TTL 30 min
- `_msg_buffer` / `_msg_tasks` — debounce: collects rapid follow-up messages before calling AI

### Behavior rules enforced in code

- Bot never initiates — only handles `events.NewMessage(incoming=True)`
- Groups are ignored (`ONLY_PRIVATE = True` + sender type checks)
- Opted-out clients are permanently silenced (stored in DB)
- The price list is always forwarded as a single message from a specific channel message (`PRICE_CHANNEL / PRICE_MSG_ID`)
- All Telethon sends go through `safe_send()` — never call `client.send_message` directly

### Language detection

`detect_language()` in `ai_handler.py` is pure heuristic (no API call): checks for Uzbek-specific Cyrillic characters (Ў, Қ, Ғ, Ҳ), then keyword lists for Uzbek Cyrillic and Latin, then falls back to character-count ratio (Latin→`en`, Cyrillic→`ru`).
