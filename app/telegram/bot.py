"""
Telegram bot — single long-running process serving all users.

The TeleBot instance is created lazily inside run_bot() so that the
token is guaranteed to be available (load_dotenv has already run).
"""

import html
import logging
import os
import re
import tempfile
import traceback

from telebot import TeleBot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.hermes.adapter import HermesAdapter
from app.users.manager import (
    get_or_create_user,
    get_user,
    update_last_seen,
    update_user_language,
)
from app.voice.stt import transcribe_voice
from app.reminders.scheduler import start_reminder_scheduler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Localised intro messages — warm, guiding, with real examples
# ---------------------------------------------------------------------------
INTRO_MESSAGES = {
    "ru": (
        "👋 Привет! Меня зовут Лейла.\n"
        "Я — ваш личный помощник, и я всегда здесь, чтобы помочь.\n\n"

        "Вот что я умею — просто напишите или надиктуйте голосовым сообщением:\n\n"

        "🧮  «Сколько будет 1580 × 12?»\n"
        "💰  «Я потратил 45 000 на продукты и 12 000 на такси. Сколько осталось от 200 000?»\n"
        "📝  «Напиши поздравление с днём рождения для коллеги»\n"
        "🔎  «Расскажи, чем полезен зелёный чай»\n"
        "📋  «Напомни, что мне нужно сделать сегодня: аптека, банк, позвонить маме»\n"
        "🧠  «Запомни: мой врач — Иванов Пётр Сергеевич, приём в 15:00 в среду»\n"
        "✉️  «Помоги написать письмо в школу»\n"
        "🌐  «Переведи на английский: Здравствуйте, как дела?»\n\n"

        "🎤 Можете просто записать голосовое сообщение — я пойму!\n\n"

        "Не нужно учить никаких команд — общайтесь со мной как с человеком.\n"
        "Я запоминаю важные вещи, которые вы мне расскажете.\n\n"

        "Попробуйте прямо сейчас! Например, спросите:\n"
        "«Что ты умеешь?» или «Сколько дней до Нового года?» 😊"
    ),
    "en": (
        "👋 Hello! My name is Leyla.\n"
        "I'm your personal assistant, and I'm always here to help.\n\n"

        "Here's what I can do — just type or send a voice message:\n\n"

        "🧮  \"What is 1580 × 12?\"\n"
        "💰  \"I spent \$45 on groceries and \$12 on a taxi. How much is left from \$200?\"\n"
        "📝  \"Write a birthday greeting for my friend\"\n"
        "🔎  \"Tell me about the health benefits of green tea\"\n"
        "📋  \"Remind me what I need to do today: pharmacy, bank, call mom\"\n"
        "🧠  \"Remember this: my doctor is Dr. Smith, appointment Wednesday at 3 PM\"\n"
        "✉️  \"Help me write an email to my landlord\"\n"
        "🌐  \"Translate to Russian: Hello, how are you?\"\n\n"

        "🎤 You can also just record a voice message — I'll understand!\n\n"

        "No commands to learn — just talk to me like you would to a person.\n"
        "I remember important things you tell me.\n\n"

        "Try it right now! For example, ask me:\n"
        "\"What can you do?\" or \"How many days until New Year?\" 😊"
    ),
    "uz": (
        "👋 Salom! Mening ismim Laylo.\n"
        "Men sizning shaxsiy yordamchingizman va har doim yordam berishga tayyorman.\n\n"

        "Mana men nimalar qila olaman — shunchaki yozing yoki ovozli xabar yuboring:\n\n"

        "🧮  «1580 × 12 necha bo'ladi?»\n"
        "💰  «Oziq-ovqatga 450 000 va taksiga 120 000 sarfladim. 2 000 000 dan qancha qoldi?»\n"
        "📝  «Do'stim uchun tug'ilgan kun tabrigi yoz»\n"
        "🔎  «Yashil choyning foydalari haqida aytib ber»\n"
        "📋  «Bugun nima qilishim kerakligini eslatib tur: dorixona, bank, oyimga qo'ng'iroq»\n"
        "🧠  «Eslab qol: mening shifokorim — Aliyev Jasur, qabul chorshanba kuni soat 15:00 da»\n"
        "✉️  \"Maktabga xat yozishda yordam ber\"\n"
        "🌐  «Ruscha'ga tarjima qil: Assalomu alaykum, qalaysiz?»\n\n"

        "🎤 Ovozli xabar ham yuborishingiz mumkin — men tushunaman!\n\n"

        "Hech qanday buyruqlarni yodlash shart emas — men bilan oddiy gaplashing.\n"
        "Men siz aytgan muhim narsalarni eslab qolaman.\n\n"

        "Hoziroq sinab ko'ring! Masalan, so'rang:\n"
        "«Sen nima qila olasan?» yoki «Yangi yilgacha necha kun qoldi?» 😊"
    ),
}

UNSUPPORTED_CONTENT_MESSAGES = {
    "ru": "Пока я могу работать только с текстовыми и голосовыми сообщениями. Напишите или надиктуйте! ✍️🎤",
    "en": "I can only work with text and voice messages for now. Please type or record a voice message! ✍️🎤",
    "uz": "Hozircha faqat matnli va ovozli xabarlar bilan ishlay olaman. Iltimos, matn yozing yoki ovozli xabar yuboring! ✍️🎤",
}

VOICE_TRANSCRIBING_MESSAGES = {
    "ru": "🎧 Слушаю ваше сообщение…",
    "en": "🎧 Listening to your message…",
    "uz": "🎧 Xabaringizni tinglayapman…",
}

VOICE_FAILED_MESSAGES = {
    "ru": "❌ Не удалось распознать голосовое сообщение. Попробуйте ещё раз или напишите текстом.",
    "en": "❌ Could not transcribe your voice message. Please try again or type your message.",
    "uz": "❌ Ovozli xabarni aniqlab bo'lmadi. Iltimos, qaytadan urinib ko'ring yoki matn yozing.",
}


def split_message(text: str, max_chars: int = 3500) -> list[str]:
    """Split a long response into Telegram-safe chunks (limit 4096), breaking nicely on newlines."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0

    for line in lines:
        if current_len + len(line) + 1 > max_chars:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            if len(line) > max_chars:
                for i in range(0, len(line), max_chars):
                    chunks.append(line[i : i + max_chars])
                continue
        current_chunk.append(line)
        current_len += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks


def format_telegram_html(text: str) -> str:
    """Converts standard markdown (**bold**, *italic*, `code`, ```block```, etc.) to Telegram HTML."""
    # 1. Code blocks
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(1))
        return f"\x00CB{len(code_blocks)-1}\x00"

    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    # 2. Inline code
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(1))
        return f"\x00IC{len(inline_codes)-1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # 3. Escape HTML (&, <, > only, keep quotes and apostrophes for natural text)
    text = html.escape(text, quote=False)

    # 4. Links: [text](url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', text)

    # 5. Headers: ### Header -> <b>Header</b>
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 6. Bold: **text** or __text__ -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)

    # 7. Italic: *text* or _text_ -> <i>text</i>
    text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)

    # 8. Strikethrough: ~~text~~ -> <s>text</s>
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # 9. Restore inline code
    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", f"<code>{html.escape(code, quote=False)}</code>")

    # 10. Restore code blocks
    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{html.escape(code, quote=False)}</code></pre>")

    return text


def _process_and_reply(bot, chat_id, telegram_id, user, text: str) -> None:
    """Common logic: send text through Hermes and reply with the result."""
    bot.send_chat_action(chat_id, "typing")
    hermes = HermesAdapter(user["hermes_profile"], telegram_user_id=telegram_id, chat_id=chat_id)
    response = hermes.send_message(text, user["language"])

    # Intercept language-change tags
    match = re.search(r"\[LANGUAGE_CHANGED_TO:\s*([a-z]{2})\]", response)
    if match:
        new_lang = match.group(1)
        if new_lang in ("ru", "en", "uz"):
            update_user_language(telegram_id, new_lang)
        response = response.replace(match.group(0), "").strip()

    for chunk in split_message(response):
        if chunk.strip():
            formatted_chunk = format_telegram_html(chunk)
            try:
                bot.send_message(chat_id, formatted_chunk, parse_mode="HTML")
            except Exception as exc:
                logger.warning(
                    "Failed to send HTML formatted message (%s). Sending plain text.",
                    exc,
                )
                bot.send_message(chat_id, chunk)


# ---------------------------------------------------------------------------
# Bot setup & handlers  (all created inside run_bot to avoid import-time I/O)
# ---------------------------------------------------------------------------

def run_bot() -> None:
    """Create the bot, register handlers, and start polling."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "dummy":
        logger.error("TELEGRAM_BOT_TOKEN is not set — cannot start bot")
        return

    bot = TeleBot(token, threaded=True, num_threads=4)

    # Start the background reminder & timer scheduler
    start_reminder_scheduler(bot)

    # -- /start -------------------------------------------------------------
    @bot.message_handler(commands=["start"])
    def handle_start(message):
        try:
            telegram_id = message.from_user.id
            user, _ = get_or_create_user(telegram_id)

            if not user["language"]:
                markup = InlineKeyboardMarkup()
                markup.add(
                    InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
                    InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                    InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz"),
                )
                bot.send_message(
                    message.chat.id,
                    "Привет! / Hello! / Salom! 👋\n"
                    "Какой язык вы предпочитаете?\n"
                    "Which language do you prefer?\n"
                    "Qaysi tilni afzal ko'rasiz?",
                    reply_markup=markup,
                )
            else:
                lang = user["language"]
                welcome = {
                    "ru": "С возвращением! Чем могу помочь? 😊",
                    "en": "Welcome back! How can I help? 😊",
                    "uz": "Xush kelibsiz! Qanday yordam bera olaman? 😊",
                }
                bot.send_message(
                    message.chat.id,
                    welcome.get(lang, welcome["en"]),
                )
        except Exception:
            logger.error("Error in /start:\n%s", traceback.format_exc())

    # -- Language callback ---------------------------------------------------
    @bot.callback_query_handler(func=lambda call: call.data.startswith("lang_"))
    def handle_language_selection(call):
        try:
            telegram_id = call.from_user.id
            lang_code = call.data.split("_", 1)[1]

            get_or_create_user(telegram_id)
            update_user_language(telegram_id, lang_code)

            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="✅ Language saved! / Язык сохранён! / Til saqlandi!",
            )

            intro = INTRO_MESSAGES.get(lang_code, INTRO_MESSAGES["en"])
            bot.send_message(call.message.chat.id, intro)
            bot.answer_callback_query(call.id)
        except Exception:
            logger.error("Error in language callback:\n%s", traceback.format_exc())

    # -- Voice messages (transcribe via ElevenLabs STT) ----------------------
    @bot.message_handler(content_types=["voice"])
    def handle_voice(message):
        try:
            telegram_id = message.from_user.id
            user = get_user(telegram_id)

            if not user or not user["language"]:
                handle_start(message)
                return

            update_last_seen(telegram_id)

            bot.send_chat_action(message.chat.id, "typing")

            # Download the .ogg file from Telegram
            file_info = bot.get_file(message.voice.file_id)
            downloaded = bot.download_file(file_info.file_path)

            # Write to a temp file and transcribe
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".ogg")
                with os.fdopen(fd, "wb") as f:
                    f.write(downloaded)

                transcribed_text = transcribe_voice(tmp_path)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            if not transcribed_text:
                lang = user["language"]
                bot.send_message(
                    message.chat.id,
                    VOICE_FAILED_MESSAGES.get(lang, VOICE_FAILED_MESSAGES["en"]),
                )
                return

            # Process the transcribed text exactly like a normal text message
            _process_and_reply(bot, message.chat.id, telegram_id, user, transcribed_text)

        except Exception:
            logger.error(
                "Error processing voice for %s:\n%s",
                message.from_user.id,
                traceback.format_exc(),
            )
            try:
                bot.send_message(
                    message.chat.id,
                    "⚠️ An error occurred processing your voice message.",
                )
            except Exception:
                pass

    # -- Non-text/non-voice content (photo, sticker, …) ---------------------
    @bot.message_handler(
        content_types=[
            "audio", "document", "photo", "sticker", "video",
            "video_note", "location", "contact",
            "animation", "dice",
        ]
    )
    def handle_non_text(message):
        try:
            telegram_id = message.from_user.id
            user = get_user(telegram_id)
            lang = user["language"] if user and user["language"] else "en"
            bot.send_message(
                message.chat.id,
                UNSUPPORTED_CONTENT_MESSAGES.get(lang, UNSUPPORTED_CONTENT_MESSAGES["en"]),
            )
        except Exception:
            logger.error("Error handling non-text:\n%s", traceback.format_exc())

    # -- Text messages -------------------------------------------------------
    @bot.message_handler(func=lambda m: True, content_types=["text"])
    def handle_text(message):
        try:
            telegram_id = message.from_user.id
            user = get_user(telegram_id)

            if not user or not user["language"]:
                handle_start(message)
                return

            update_last_seen(telegram_id)

            text = message.text or ""
            if not text.strip():
                return

            _process_and_reply(bot, message.chat.id, telegram_id, user, text)

        except Exception:
            logger.error(
                "Error processing message for %s:\n%s",
                message.from_user.id,
                traceback.format_exc(),
            )
            try:
                bot.send_message(
                    message.chat.id,
                    "⚠️ An error occurred. Please try again.",
                )
            except Exception:
                pass

    # -- Start polling -------------------------------------------------------
    logger.info("Bot starting — polling Telegram…")
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=25,
        allowed_updates=["message", "callback_query"],
    )

