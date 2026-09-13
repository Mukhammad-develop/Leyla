# Leyla — Personal AI Telegram Assistant

A production-grade Telegram bot backed by OpenAI, with per-user profile
isolation, persistent memory, and multilingual support (Russian, English,
Uzbek).

Runs as **one** long-running Python process — perfect for a single
PythonAnywhere Always-on Task.

## Architecture

```
Telegram Users
       │
       ▼
  ONE Telegram Bot  (pyTelegramBotAPI, long-polling)
       │
       ▼
  ONE Python Process  (PythonAnywhere Always-on Task)
       │
  ┌────┴────────────────────────────────────┐
  │  SQLite mapping DB                      │
  │  telegram_user_id → hermes_profile_id   │
  │  + language preference                  │
  └────┬────────────────────────────────────┘
       │
  Per-user Hermes Profiles (file-system isolated)
       │
       ├── telegram_123/
       │     ├── memory.json    (persistent facts)
       │     └── history.json   (conversation log)
       │
       ├── telegram_456/  …
       └── telegram_789/  …
```

## Features

| Feature | Details |
|---|---|
| **Profile isolation** | Every Telegram user gets a unique profile directory. User A cannot access User B's memory or history. |
| **Persistent memory** | The LLM is instructed to emit `[REMEMBER: …]` tags; facts are extracted and injected into future system prompts. |
| **Multilingual onboarding** | On `/start` the user picks Russian, English, or Uzbek via inline buttons. |
| **Natural language switching** | Say "Speak English" / "Давай по-русски" / "O'zbekcha gaplash" — the language preference updates automatically. |
| **Crash-safe file I/O** | JSON state files are written atomically (tmp + rename). |
| **Thread-safe concurrency** | Per-user locks prevent state corruption under concurrent Telegram updates. |
| **Non-text handling** | Voice, photos, stickers, etc. get a polite "text only" reply instead of crashing. |
| **OpenAI model** | Configurable via `OPENAI_MODEL` (default: `gpt-5.6-luna`). |

## Quick Start (local)

```bash
git clone https://github.com/Mukhammad-develop/Leyla.git
cd Leyla
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — fill in TELEGRAM_BOT_TOKEN and OPENAI_API_KEY

python app/main.py
```

## Running Tests

```bash
PYTHONPATH=. python -m pytest tests/ -v
# or
PYTHONPATH=. python -m unittest discover tests/ -v
```

## PythonAnywhere Deployment

1. Open a **Bash console** on PythonAnywhere.

2. Clone and install:
   ```bash
   git clone https://github.com/Mukhammad-develop/Leyla.git
   cd Leyla
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Create the `.env` file:
   ```bash
   cp .env.example .env
   nano .env          # add your tokens
   ```

4. Go to **Tasks → Always-on Tasks** and set the command to:
   ```
   /home/<YOUR_USERNAME>/Leyla/venv/bin/python /home/<YOUR_USERNAME>/Leyla/app/main.py
   ```

> **One task serves all users.** Do not create multiple tasks.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `OPENAI_API_KEY` | ✅ | — | OpenAI API key |
| `OPENAI_MODEL` | — | `gpt-5.6-luna` | Model identifier |
| `DATABASE_PATH` | — | `data/app.db` | SQLite database path |
| `DATA_DIR` | — | `data` | Root for profile directories |
| `LOG_LEVEL` | — | `INFO` | Python logging level |

## Security

- Tenant mapping uses Telegram's cryptographic user ID — cannot be spoofed.
- Profile paths are derived deterministically (`telegram_<id>`) — no user input in file paths.
- API keys live in `.env` (git-ignored) and are never stored in the database.
- Per-user threading locks prevent concurrent state corruption.
