"""
Telegram bot — single long-running process serving all users.
"""

import html
import logging
import os
import io
import re
import tempfile
import traceback

from telebot import TeleBot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

from app.hermes.adapter import HermesAdapter
from app.users.manager import (
    get_or_create_user,
    get_user,
    update_last_seen,
    update_user_language,
    update_translator_mode,
)
from app.voice.stt import transcribe_voice
from app.reminders.scheduler import start_reminder_scheduler
from app.vision.analyzer import analyze_image
from app.photo_vault.manager import save_photo, get_photo, delete_photo
from app.translator.engine import translate_text, text_to_speech

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Localised messages
# ---------------------------------------------------------------------------
INTRO_MESSAGES = {
    "ru": (
        "👋 Привет! Меня зовут Лейла, ваш личный помощник.

"
        "Вот что я умею (пишите или отправляйте голосовые!):
"
        "💱 **Курсы валют**: «Сколько будет 100 долларов в сумах?»
"
        "⏰ **Напоминания**: «Напомни выпить лекарство через 2 часа»
"
        "🌐 **Переводчик**: «Включи переводчик на китайский» (переводит голос и текст)
"
        "📸 **Чтение фото**: Отправьте фото документа и спросите «Что здесь написано?»
"
        "🗂 **Хранилище**: Отправьте фото и скажите «Сохрани как мой паспорт»
"
        "📞 **Контакты**: «Запомни номер врача: Алиев +99890...»
"
        "🕌 **Время намаза**: «Время намаза в Ташкенте»
"
        "🔎 **Поиск в сети**: «Какая сегодня погода?»

"
        "🎤 Вам не нужно учить команды — просто общайтесь со мной как с человеком!"
    ),
    "en": (
        "👋 Hello! My name is Laila, your personal assistant.

"
        "Here is what I can do (just type or speak!):
"
        "💱 **Live Currency**: \"How much is 100 USD in UZS?\"
"
        "⏰ **Reminders**: \"Remind me to take my pills in 2 hours\"
"
        "🌐 **Translator**: \"Turn on translator to Chinese\" (translates voice & text)
"
        "📸 **Read Photos**: Send a photo of a document and ask \"What does this say?\"
"
        "🗂 **Photo Vault**: Send an image and say \"Save this as my passport\"
"
        "📞 **Contacts**: \"Save my doctor's number: Aliyev +99890...\"
"
        "🕌 **Prayer Times**: \"Prayer times in Tashkent\"
"
        "🔎 **Web Search**: \"What is the weather today?\"

"
        "🎤 You don't need to learn commands — just talk to me naturally!"
    ),
    "uz": (
        "👋 Salom! Mening ismim Laylo, sizning shaxsiy yordamchingizman.

"
        "Men nimalar qila olaman (yozing yoki ovozli xabar yuboring!):
"
        "💱 **Valyuta kursi**: «100 dollar necha so'm bo'ladi?»
"
        "⏰ **Eslatmalar**: «2 soatdan keyin dori ichishni eslat»
"
        "🌐 **Tarjimon**: «Xitoy tiliga tarjimonni yoq» (ovoz va matnni tarjima qiladi)
"
        "📸 **Rasm o'qish**: Hujjat rasmini yuboring va «Bu yerda nima yozilgan?» deb so'rang
"
        "🗂 **Rasmlar xazinasi**: Rasm yuboring va «Buni pasportim deb saqla» deng
"
        "📞 **Kontaktlar**: «Shifokor raqamini saqla: Aliyev +99890...»
"
        "🕌 **Namoz vaqtlari**: «Toshkentda namoz vaqtlari»
"
        "🔎 **Internet qidiruv**: «Bugun ob-havo qanday?»

"
        "🎤 Hech qanday buyruqlarni yodlash shart emas — men bilan oddiy gaplashing!"
    ),
}

UNSUPPORTED_CONTENT_MESSAGES = {
    "ru": "Пока я могу работать только с текстом, голосом и фотографиями. Напишите или надиктуйте! ✍️🎤",
    "en": "I can only work with text, voice, and photos for now. Please type or record! ✍️🎤",
    "uz": "Hozircha faqat matn, ovoz va rasmlar bilan ishlay olaman. Iltimos, yozing yoki ovozli xabar yuboring! ✍️🎤",
}

VOICE_FAILED_MESSAGES = {
    "ru": "❌ Не удалось распознать голосовое сообщение. Попробуйте ещё раз.",
    "en": "❌ Could not transcribe your voice message. Please try again.",
    "uz": "❌ Ovozli xabarni aniqlab bo'lmadi. Iltimos, qaytadan urinib ko'ring.",
}


def split_message(text: str, max_chars: int = 3500) -> list[str]:
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
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(1))
        return f"\x00CB{len(code_blocks)-1}\x00"

    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(1))
        return f"\x00IC{len(inline_codes)-1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", f"<code>{html.escape(code, quote=False)}</code>")
    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{html.escape(code, quote=False)}</code></pre>")

    return text


def _process_translator_mode(bot, chat_id, text, target_lang, lang_code):
    """Handle a message while in Translator mode."""
    translated = translate_text(text, target_lang)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔴 Turn off translator", callback_data="translator_off"))

    formatted = format_telegram_html(translated)
    bot.send_message(chat_id, formatted, parse_mode="HTML", reply_markup=markup)

    # Also send TTS Voice note
    bot.send_chat_action(chat_id, "record_voice")
    audio_bytes = text_to_speech(translated, target_lang)
    if audio_bytes:
        bot.send_voice(chat_id, io.BytesIO(audio_bytes), reply_markup=markup)


def _process_and_reply(bot, chat_id, telegram_id, user, text: str) -> None:
    bot.send_chat_action(chat_id, "typing")
    
    # Check if translator mode is active
    if user.get("translator_lang"):
        _process_translator_mode(bot, chat_id, text, user["translator_lang"], user["language"])
        return

    # Normal Hermes path
    hermes = HermesAdapter(user["hermes_profile"], telegram_user_id=telegram_id, chat_id=chat_id)
    response = hermes.send_message(text, user["language"])

    # Process Language change
    match = re.search(r"\[LANGUAGE_CHANGED_TO:\s*([a-z]{2})\]", response)
    if match:
        new_lang = match.group(1)
        if new_lang in ("ru", "en", "uz"):
            update_user_language(telegram_id, new_lang)
        response = response.replace(match.group(0), "").strip()

    # Process Translator mode changes triggered by LLM
    pending_translator = getattr(hermes, "_pending_translator_update", None)
    if pending_translator:
        if pending_translator.lower() == "off":
            update_translator_mode(telegram_id, "")
        else:
            update_translator_mode(telegram_id, pending_translator)

    # Send text chunks
    chunks = split_message(response)
    for chunk in chunks:
        if chunk.strip():
            try:
                bot.send_message(chat_id, format_telegram_html(chunk), parse_mode="HTML")
            except Exception:
                bot.send_message(chat_id, chunk)

    # Handle Photo Vault GET
    pending_get = getattr(hermes, "_pending_get_photo", None)
    if pending_get:
        path = get_photo(telegram_id, pending_get)
        if path and os.path.exists(path):
            bot.send_chat_action(chat_id, "upload_photo")
            with open(path, "rb") as f:
                bot.send_photo(chat_id, f, caption=f"📸 {pending_get}")
        else:
            err = {"en": "Photo not found.", "ru": "Фото не найдено.", "uz": "Rasm topilmadi."}
            bot.send_message(chat_id, err.get(user["language"], err["en"]))

    # Handle Photo Vault DELETE
    pending_delete = getattr(hermes, "_pending_delete_photo", None)
    if pending_delete:
        if delete_photo(telegram_id, pending_delete):
            msg = {"en": "Deleted.", "ru": "Удалено.", "uz": "O'chirildi."}
            bot.send_message(chat_id, f"🗑️ {msg.get(user['language'], msg['en'])}")


def run_bot() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "dummy":
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        return

    bot = TeleBot(token, threaded=True, num_threads=4)
    start_reminder_scheduler(bot)

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
                    "Привет! / Hello! / Salom! 👋\nКакой язык вы предпочитаете?",
                    reply_markup=markup,
                )
            else:
                lang = user["language"]
                welcome = {"ru": "С возвращением! Чем могу помочь? 😊", "en": "Welcome back! How can I help? 😊", "uz": "Xush kelibsiz! Qanday yordam bera olaman? 😊"}
                bot.send_message(message.chat.id, welcome.get(lang, welcome["en"]))
        except Exception:
            logger.error("Error in /start:\n%s", traceback.format_exc())

    @bot.callback_query_handler(func=lambda call: call.data.startswith("lang_"))
    def handle_language_selection(call):
        telegram_id = call.from_user.id
        lang_code = call.data.split("_", 1)[1]
        get_or_create_user(telegram_id)
        update_user_language(telegram_id, lang_code)
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="✅ Language saved!")
        bot.send_message(call.message.chat.id, INTRO_MESSAGES.get(lang_code, INTRO_MESSAGES["en"]))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "translator_off")
    def handle_translator_off(call):
        update_translator_mode(call.from_user.id, "")
        msg = {"en": "Translator mode OFF. I'm Laila again! 😊", "ru": "Режим переводчика выключен. Я снова Лейла! 😊", "uz": "Tarjimon rejimi o'chirildi. Men yana Layloman! 😊"}
        user = get_user(call.from_user.id)
        lang = user["language"] if user else "en"
        bot.send_message(call.message.chat.id, msg.get(lang, msg["en"]))
        bot.answer_callback_query(call.id)

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

            file_info = bot.get_file(message.voice.file_id)
            downloaded = bot.download_file(file_info.file_path)

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
                bot.send_message(message.chat.id, VOICE_FAILED_MESSAGES.get(user["language"], VOICE_FAILED_MESSAGES["en"]))
                return

            _process_and_reply(bot, message.chat.id, telegram_id, user, transcribed_text)

        except Exception:
            logger.error("Voice error: %s", traceback.format_exc())

    @bot.message_handler(content_types=["photo", "document"])
    def handle_photo(message):
        try:
            telegram_id = message.from_user.id
            user = get_user(telegram_id)
            if not user:
                handle_start(message)
                return
                
            bot.send_chat_action(message.chat.id, "typing")
            
            # Find the best file to download
            file_id = None
            if message.photo:
                file_id = message.photo[-1].file_id  # Highest resolution
            elif message.document:
                mime = message.document.mime_type or ""
                if "image" in mime:
                    file_id = message.document.file_id
            
            if not file_id:
                bot.send_message(message.chat.id, UNSUPPORTED_CONTENT_MESSAGES.get(user["language"], UNSUPPORTED_CONTENT_MESSAGES["en"]))
                return

            file_info = bot.get_file(file_id)
            downloaded = bot.download_file(file_info.file_path)
            
            caption = (message.caption or "").strip()
            
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
                with os.fdopen(fd, "wb") as f:
                    f.write(downloaded)

                # If caption says 'save' or 'remember', put it in Photo Vault
                # A simple heuristic; can be improved
                low_cap = caption.lower()
                if "save" in low_cap or "remember" in low_cap or "сохрани" in low_cap or "eslab" in low_cap or "saqla" in low_cap:
                    label = caption
                    # clean up verbs
                    for word in ["save this as", "save", "remember", "сохрани как", "сохрани", "saqlab qol", "saqla"]:
                        if label.lower().startswith(word):
                            label = label[len(word):].strip()
                    
                    if not label:
                        label = "Document"

                    if save_photo(telegram_id, label, tmp_path):
                        msg = {"en": f"✅ Saved to Photo Vault as '{label}'!", "ru": f"✅ Сохранено как '{label}'!", "uz": f"✅ '{label}' nomi bilan saqlandi!"}
                        bot.send_message(message.chat.id, msg.get(user["language"], msg["en"]))
                    else:
                        bot.send_message(message.chat.id, "❌ Error saving photo.")
                else:
                    # Otherwise run Vision Analysis
                    prompt = caption if caption else {
                        "en": "What is in this image?",
                        "ru": "Что на этой картинке? Если это текст, прочитай его.",
                        "uz": "Bu rasmda nima bor? Agar u matn bo'lsa, o'qib bering."
                    }.get(user["language"], "What is in this image?")
                    
                    analysis = analyze_image(tmp_path, prompt, user["language"])
                    bot.send_message(message.chat.id, format_telegram_html(analysis), parse_mode="HTML")

            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception:
            logger.error("Photo error: %s", traceback.format_exc())

    @bot.message_handler(content_types=["audio", "sticker", "video", "video_note", "location", "contact", "animation", "dice"])
    def handle_non_text(message):
        user = get_user(message.from_user.id)
        lang = user["language"] if user and user["language"] else "en"
        bot.send_message(message.chat.id, UNSUPPORTED_CONTENT_MESSAGES.get(lang, UNSUPPORTED_CONTENT_MESSAGES["en"]))

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
            logger.error("Text error: %s", traceback.format_exc())

    logger.info("Bot starting — polling Telegram…")
    bot.infinity_polling(timeout=30, long_polling_timeout=25, allowed_updates=["message", "callback_query"])
