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
        "👋 Salom! Mening ismim Leyla.\n"
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

