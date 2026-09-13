"""
Telegram bot — single long-running process serving all users.

The TeleBot instance is created lazily inside run_bot() so that the
token is guaranteed to be available (load_dotenv has already run).
"""

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Localised intro messages
# ---------------------------------------------------------------------------
INTRO_MESSAGES = {
    "ru": (
        "👋 Привет! Я — Лейла, твой персональный ИИ-ассистент.\n\n"
        "Представь, что я — человек за компьютером, который может "
        "помочь тебе с повседневными задачами.\n\n"
        "🧠 Я помню важные вещи, которые ты мне расскажешь\n"
        "🧮 Я считаю и решаю задачи\n"
        "🔎 Я ищу информацию\n"
        "📝 Я пишу тексты\n"
        "📋 Я помогаю с организацией дел\n"
        "💻 Я помогаю как компьютерный ассистент\n\n"
        "💬 Просто общайся со мной естественно. Не нужно учить "
        "команды. Если хочешь что-то изменить — просто скажи!"
    ),
    "en": (
        "👋 Hi! I'm Leyla, your personal AI assistant.\n\n"
        "Imagine me as a person with access to a computer who can "
        "help you with many everyday tasks.\n\n"
        "🧠 I remember things you tell me\n"
        "🧮 I calculate and solve problems\n"
        "🔎 I research information\n"
        "📝 I write texts\n"
        "📋 I help organise your day\n"
        "💻 I assist like a computer-savvy helper\n\n"
        "💬 Just talk to me naturally. No commands to learn. "
        "If you want to change something — just tell me!"
    ),
    "uz": (
        "👋 Salom! Men Leyla, sizning shaxsiy AI yordamchingizman.\n\n"
        "Tasavvur qiling, men kompyuterga kirish huquqiga ega bo'lgan "
        "va kundalik vazifalaringizda yordam beradigan insonman.\n\n"
        "🧠 Men aytgan narsalaringizni eslab qolaman\n"
        "🧮 Men hisoblayman va masalalarni yechaman\n"
        "🔎 Men ma'lumot izlayman\n"
        "📝 Men matnlar yozaman\n"
        "📋 Men ishlaringizni tartibga solishda yordam beraman\n"
        "💻 Men kompyuter yordamchisi sifatida xizmat qilaman\n\n"
        "💬 Men bilan oddiy gaplashavering. Buyruqlarni yodlash "
        "shart emas. Nimadir o'zgartirmoqchi bo'lsangiz — ayting!"
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


def split_message(text: str, chunk_size: int = 4000) -> list[str]:
    """Split a long response into Telegram-safe chunks (limit 4096)."""
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _process_and_reply(bot, chat_id, telegram_id, user, text: str) -> None:
    """Common logic: send text through Hermes and reply with the result."""
    hermes = HermesAdapter(user["hermes_profile"])
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
            lang = user["language"]

            # Show "listening" feedback
            status_msg = bot.send_message(
                message.chat.id,
                VOICE_TRANSCRIBING_MESSAGES.get(lang, VOICE_TRANSCRIBING_MESSAGES["en"]),
            )

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

            # Delete the "listening" status message
            try:
                bot.delete_message(message.chat.id, status_msg.message_id)
            except Exception:
                pass

            if not transcribed_text:
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

