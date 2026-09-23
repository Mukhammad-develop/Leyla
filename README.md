# Leyla — Personal AI Telegram Assistant

A production-grade Telegram bot backed by **OpenRouter** (chat, vision and
image generation) and **ElevenLabs** (speech-to-text and text-to-speech),
with per-user profile isolation, persistent memory, durable structured data
(lists, expenses, calories, birthdays), and multilingual support
(Russian, English, Uzbek).

Runs as **one** long-running Python process — perfect for a single
PythonAnywhere Always-on Task.

## Architecture

```
Telegram Users
       │
       ▼
  ONE Telegram Bot  (pyTelegramBotAPI, long-polling, auto-restart loop)
       │
       ▼
  ONE Python Process  (PythonAnywhere Always-on Task)
       │
  ┌────┴────────────────────────────────────┐
  │  SQLite (WAL)                           │
  │  users, reminders, contacts, photo_vault│
  │  list_items, expenses, calorie_entries, │
  │  events, usage_daily                    │
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

The LLM drives features by emitting tags (`[REMINDER: …]`, `[LIST_ADD: …]`,
`[EXPENSE: …]`, `[WEATHER: …]`, …) which the adapter parses and executes
against the database or live APIs.

## Features

| Feature | Details |
|---|---|
| **Profile isolation** | Every Telegram user gets a unique profile directory. User A cannot access User B's memory or history. |
| **No memory loss** | Lists, expenses, calories, contacts and birthdays live in **dedicated DB tables**, not in the (truncated) chat history. A shopping list survives for months. |
| **Persistent memory** | The LLM emits `[REMEMBER: …]` tags; facts are injected into future system prompts. |
| **Cost control** | Per-user daily message limit, set from the **admin panel** at runtime (default: unlimited). Usage tracked per user per day. Admin is exempt. |
| **Multilingual** | Russian / English / Uzbek onboarding and natural language switching. Name: Лейла / Laila / Laylo. |
| **No commands needed** | Users never type slash commands — every feature is activated by natural language ("answer with voice", "send me my data", …). |
| **Voice replies** | Three modes: text only / voice only / both — toggled by simply asking (ElevenLabs TTS). |
| **Voice input** | Voice notes transcribed via ElevenLabs STT. |
| **Morning briefing** | Auto-sends weather + prayer times + pending reminders every morning at the user's local time; time and on/off set by asking. |
| **Live weather** | Open-Meteo (no API key). |
| **Image generation** | Via OpenRouter image model (`OPENROUTER_IMAGE_MODEL`). |
| **Calorie counter** | "I ate two plovs" — the LLM estimates and logs; daily total + goal tracked. |
| **Birthdays/events** | Yearly recurring greetings at 09:00 local, plus upcoming events in context. |
| **Expense tracker** | Per-currency monthly totals + recent entries in context. |
| **Lists** | Shopping/to-do lists that never get forgotten. |
| **Reminders** | Real timers with ~1s precision (adaptive scheduler), timezone-aware clock-time reminders. |
| **Translator mode** | Translates everything you send + voice note in the target language. |
| **Photo vault** | Photos saved by label, retrievable later; Telegram `file_id` kept as cloud backup. |
| **Vision (OCR)** | Send a document photo — a vision model reads it. |
| **Live currency** | open.er-api.com (no key) with exchangerate.host fallback. |
| **Prayer times** | Aladhan API. |
| **Web search** | DuckDuckGo. |
| **Data export** | Say "send me my data" — profile, memory, history, contacts, lists, expenses, calories, events, photos as one JSON + the photos themselves. |
| **Admin panel** | `/admin` — inline-button panel: live stats, user list, daily-limit control, broadcast. Also `/health` and `/broadcast` (admin-only). |

## Quick Start (local)

```bash
git clone https://github.com/Mukhammad-develop/Leyla.git
cd Leyla
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — fill in TELEGRAM_BOT_TOKEN and OPENROUTER_API_KEY

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
> The process also restarts itself automatically if the polling loop crashes.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `OPENROUTER_API_KEY` | ✅ | — | OpenRouter API key (chat, vision, image gen) |
| `OPENROUTER_MODEL` | — | `openai/gpt-4o-mini` | Chat model |
| `OPENROUTER_VISION_MODEL` | — | `openai/gpt-4o` | Vision/OCR model |
| `OPENROUTER_IMAGE_MODEL` | — | `google/gemini-2.5-flash-image` | Image generation model |
| `OPENROUTER_PRO_MODEL` | — | `openai/gpt-4o` | Better chat model used until the user's daily pro-token budget is spent |
| `DAILY_PRO_TOKEN_LIMIT` | — | `20000` | Per-user daily pro-model token budget; `0` disables the pro model. Admin panel overrides at runtime |
| `ELEVENLABS_API_KEY` | — | — | Voice input (STT) and voice replies (TTS) |
| `MUXLISA_API_KEY` | — | — | Uzbek TTS via Muxlisa (used for `uz` voice replies when set) |
| `MUXLISA_SPEAKER` | — | `1` | Muxlisa speaker id: `0` female, `1` male |
| `DATABASE_PATH` | — | `data/app.db` | SQLite database path |
| `DATA_DIR` | — | `data` | Root for profile directories and photo vault |
| `LOG_LEVEL` | — | `INFO` | Python logging level |
| `DAILY_MESSAGE_LIMIT` | — | `0` (unlimited) | Initial per-user daily message cap; the admin panel setting overrides this at runtime |

## Security

- Tenant mapping uses Telegram's cryptographic user ID — cannot be spoofed.
- Profile paths are derived deterministically (`telegram_<id>`) — no user input in file paths.
- API keys live in `.env` (git-ignored) and are never stored in the database.
- Per-user threading locks prevent concurrent state corruption.
- Daily usage limits cap API costs even if a user spams the bot.
