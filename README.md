# Hermes Telegram Bot

A Telegram bot integrating the Hermes Agent, allowing multiple users to have their own isolated Hermes profiles and memories from a single running Python process. 

This project uses a single PythonAnywhere Always-on Task to serve all Telegram users securely. 

## Architecture

```
Telegram Users
       │
       ▼
ONE Telegram Bot
       │
       ▼
ONE Python Process
       │
       ▼
     Hermes
       │
       ├── Profile telegram_123
       │      ├── Memory
       │      ├── Sessions
       │      └── State
       │
       ├── Profile telegram_456
       │      ├── Memory
       │      ├── Sessions
       │      └── State
       │
       └── Profile telegram_789
              ├── Memory
              ├── Sessions
              └── State
```

ONE PythonAnywhere Always-on Task.

## Features

- **Hermes Integration:** Uses Hermes native profile system to map Telegram IDs to isolated Hermes environments.
- **User/Profile Isolation:** Every Telegram user is assigned a separate, sandboxed Hermes profile (tenant isolation). User A cannot access User B's memory, state, or sessions.
- **Language System:** 
  - Onboarding in Russian, English, and Uzbek via Telegram inline buttons.
  - Persistent language preference saved inside SQLite.
  - Natural Language Switching: "Speak English" changes the persistent language seamlessly.
- **OpenAI Integration:** Fully configurable model. Defaults to `gpt-5.6-luna`.
- **Concurrent Safe:** Thread-safe state modifications through per-user locks.

## Local Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Mukhammad-develop/Leyla.git
   cd Leyla
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your TELEGRAM_BOT_TOKEN and OPENAI_API_KEY
   ```
5. Run the bot:
   ```bash
   python -m app.main
   ```

## Testing

Run the automated tests to verify profile isolation, memory persistence, and language features:
```bash
PYTHONPATH=. python -m unittest discover tests/
```

## PythonAnywhere Deployment

1. Open a Bash console on PythonAnywhere.
2. Clone the repository and setup your environment as shown above.
3. Edit your `.env` with your secrets.
4. Go to the **Tasks** tab.
5. Create a new **Always-on Task**.
6. Provide the exact command to start the bot. Example:
   ```bash
   /home/YOUR_USERNAME/Leyla/venv/bin/python /home/YOUR_USERNAME/Leyla/app/main.py
   ```
*(Make sure to replace `YOUR_USERNAME` with your actual PythonAnywhere username)*

**IMPORTANT:** ONE Always-on Task is required for the entire Telegram bot. Do not create one task per Telegram user.

## Environment Variables

- `TELEGRAM_BOT_TOKEN`: Your Telegram Bot API token.
- `OPENAI_API_KEY`: Your OpenAI API key.
- `OPENAI_MODEL`: Defaults to `gpt-5.6-luna`.
- `DATABASE_PATH`: Path to the SQLite DB (default: `./data/app.db`).
- `DATA_DIR`: Directory where Hermes profiles and session memories are saved.

## Security & Tenant Isolation Model

All inbound Telegram messages are resolved to an authenticated Telegram User ID. The application strictly queries its mapping table using this ID to determine the `hermes_profile`. Arbitrary user input is never used to construct profile paths. File I/O for `HermesAdapter` leverages `profile_id` and runs concurrently in thread-safe per-user locks preventing state corruption.

## Troubleshooting

- **Database Errors:** Make sure `DATA_DIR` and `DATABASE_PATH` points to a writable directory.
- **PythonAnywhere Restart:** If the Always-on task dies, check the log via the PythonAnywhere dashboard. Long messages are auto-split safely.
