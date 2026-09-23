"""
Telegram bot — single long-running process serving all users.
"""

import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

from telebot import TeleBot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.formatting import format_telegram_html, strip_telegram_markdown
from app.hermes.adapter import HermesAdapter
from app.users.manager import (
    get_or_create_user,
    get_user,
    update_last_seen,
    update_user_language,
    update_translator_mode,
    get_all_users,
    get_recent_users,
)
from app.voice.stt import transcribe_voice
from app.reminders.scheduler import start_reminder_scheduler
from app.reminders.manager import get_pending_reminders, count_pending
from app.vision.analyzer import analyze_image
from app.photo_vault.manager import (
    save_photo,
    get_photo,
    get_photo_file_id,
    delete_photo,
    list_photos,
)
from app.translator.engine import translate_text, text_to_speech
from app.imagegen.generator import generate_image
from app.usage.tracker import (
    is_limit_reached,
    increment_usage,
    get_daily_limit,
    set_daily_limit,
    get_usage_today,
    get_token_usage_today,
    get_token_stats_today,
    get_pro_token_limit,
    set_pro_token_limit,
)
from app.contacts.manager import get_all_contacts
from app.lists.manager import get_all_lists
from app.expenses.manager import get_recent_expenses
from app.calories.manager import get_all_entries as get_all_calorie_entries
from app.events.manager import get_all_events

logger = logging.getLogger(__name__)

ADMIN_ID = 1927099919
START_TIME = time.time()

# Maximum characters sent to ElevenLabs TTS in one go
TTS_MAX_CHARS = 2500

# ---------------------------------------------------------------------------
# Localised messages
# ---------------------------------------------------------------------------
INTRO_MESSAGES = {
    "ru": (
        "👋 Привет! Меня зовут Лейла, ваш личный помощник.\n\n"
        "Вот что я умею (пишите или отправляйте голосовые!):\n"
        "💱 **Курсы валют**: «Сколько будет 100 долларов в сумах?»\n"
        "⏰ **Напоминания**: «Напомни выпить лекарство через 2 часа»\n"
        "🌅 **Утренняя сводка**: погода + намаз + напоминания каждое утро. Скажите «присылай сводку в 7:30» или «выключи сводку»\n"
        "🌤️ **Погода**: «Какая погода в Ташкенте?»\n"
        "🌐 **Переводчик**: «Включи переводчик на китайский» (голос и текст)\n"
        "🗣️ **Голосовые ответы**: скажите «отвечай голосом» или «отвечай текстом и голосом»\n"
        "📸 **Чтение фото**: Отправьте фото документа и спросите «Что здесь написано?»\n"
        "🎨 **Генерация картинок**: «Нарисуй кота в космосе»\n"
        "🗂 **Хранилище**: Отправьте фото и скажите «Сохрани как мой паспорт»\n"
        "📞 **Контакты**: «Запомни номер врача: Алиев +99890...»\n"
        "📝 **Списки**: «Добавь молоко в список покупок» — я не забуду!\n"
        "💸 **Расходы**: «Потратил 50 тысяч на обед»\n"
        "📊 **Калории**: «Съел два плова» — посчитаю и запомню\n"
        "🎂 **Дни рождения**: «День рождения мамы 15 марта» — напомню каждый год\n"
        "🕌 **Время намаза**: «Время намаза в Ташкенте»\n"
        "🔎 **Поиск в сети**: «Какие сегодня новости?»\n"
        "📦 **Экспорт данных**: скажите «пришли мне мои данные»\n\n"
        "🎤 Вам не нужно учить команды — просто общайтесь со мной как с человеком!"
    ),
    "en": (
        "👋 Hello! My name is Laila, your personal assistant.\n\n"
        "Here is what I can do (just type or speak!):\n"
        "💱 **Live Currency**: \"How much is 100 USD in UZS?\"\n"
        "⏰ **Reminders**: \"Remind me to take my pills in 2 hours\"\n"
        "🌅 **Morning Briefing**: weather + prayer times + reminders every morning. Say \"send it at 7:30\" or \"turn it off\"\n"
        "🌤️ **Weather**: \"What is the weather in Tashkent?\"\n"
        "🌐 **Translator**: \"Turn on translator to Chinese\" (voice & text)\n"
        "🗣️ **Voice Replies**: say \"answer with voice\" or \"answer with text and voice\"\n"
        "📸 **Read Photos**: Send a photo of a document and ask \"What does this say?\"\n"
        "🎨 **Image Generation**: \"Draw a cat in space\"\n"
        "🗂 **Photo Vault**: Send an image and say \"Save this as my passport\"\n"
        "📞 **Contacts**: \"Save my doctor's number: Aliyev +99890...\"\n"
        "📝 **Lists**: \"Add milk to my shopping list\" — I never forget!\n"
        "💸 **Expenses**: \"I spent 50k on lunch\"\n"
        "📊 **Calories**: \"I ate two plates of plov\" — I count and remember\n"
        "🎂 **Birthdays**: \"Mom's birthday is March 15\" — yearly reminders\n"
        "🕌 **Prayer Times**: \"Prayer times in Tashkent\"\n"
        "🔎 **Web Search**: \"What is the news today?\"\n"
        "📦 **Data Export**: say \"send me my data\"\n\n"
        "🎤 You don't need to learn commands — just talk to me naturally!"
    ),
    "uz": (
        "👋 Salom! Mening ismim Laylo, sizning shaxsiy yordamchingizman.\n\n"
        "Men nimalar qila olaman (yozing yoki ovozli xabar yuboring!):\n"
        "💱 **Valyuta kursi**: «100 dollar necha so'm bo'ladi?»\n"
        "⏰ **Eslatmalar**: «2 soatdan keyin dori ichishni eslat»\n"
        "🌅 **Tonggi qisqacha**: ob-havo + namoz + eslatmalar har tong. «7:30 da yubor» yoki «o'chirib qo'y» deb ayting\n"
        "🌤️ **Ob-havo**: «Toshkentda ob-havo qanday?»\n"
        "🌐 **Tarjimon**: «Xitoy tiliga tarjimonni yoq» (ovoz va matn)\n"
        "🗣️ **Ovozli javoblar**: «ovozli javob ber» yoki «matn va ovoz bilan javob ber» deb ayting\n"
        "📸 **Rasm o'qish**: Hujjat rasmini yuboring va «Bu yerda nima yozilgan?» deb so'rang\n"
        "🎨 **Rasm yaratish**: «Koinotda mushuk chiz»\n"
        "🗂 **Rasmlar xazinasi**: Rasm yuboring va «Buni pasportim deb saqla» deng\n"
        "📞 **Kontaktlar**: «Shifokor raqamini saqla: Aliyev +99890...»\n"
        "📝 **Ro'yxatlar**: «Xarid ro'yxatiga sut qo'sh» — hech qachon unutmayman!\n"
        "💸 **Xarajatlar**: «Tushlikka 50 ming sarfladim»\n"
        "📊 **Kaloriyalar**: «Ikki plova yedim» — sanab, eslab qolaman\n"
        "🎂 **Tug'ilgan kunlar**: «Onamning tug'ilgan kuni 15-mart» — har yili eslataman\n"
        "🕌 **Namoz vaqtlari**: «Toshkentda namoz vaqtlari»\n"
        "🔎 **Internet qidiruv**: «Bugungi yangiliklar qanday?»\n"
        "📦 **Ma'lumot eksporti**: «ma'lumotlarimni yubor» deb ayting\n\n"
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

LIMIT_MESSAGES = {
    "ru": "😔 Вы достигли дневного лимита в {limit} сообщений. Это помогает контролировать расходы. До завтра! 🌙",
    "en": "😔 You've reached the daily limit of {limit} messages. This helps keep costs under control. See you tomorrow! 🌙",
    "uz": "😔 Siz kunlik {limit} ta xabar chegarasiga yetdingiz. Bu xarajatlarni nazorat qilishga yordam beradi. Ertagacha! 🌙",
}

IMAGE_FAILED_MESSAGES = {
    "ru": "❌ Не удалось создать изображение. Попробуйте ещё раз.",
    "en": "❌ Could not generate the image. Please try again.",
    "uz": "❌ Rasm yarata olmadim. Qaytadan urinib ko'ring.",
}

VOICE_UNAVAILABLE_MESSAGES = {
    "ru": "🔇 Голосовые ответы сейчас недоступны, поэтому отвечаю текстом.",
    "en": "🔇 Voice replies are unavailable right now, so I'll answer in text.",
    "uz": "🔇 Ovozli javoblar hozir mavjud emas, shuning uchun matnda javob beraman.",
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


def _to_ogg_opus(audio_bytes: bytes) -> io.BytesIO | None:
    """
    Convert MP3/WAV to OGG/Opus so Telegram renders a real voice-note bubble
    (round player + waveform) instead of a generic audio file.
    Returns None when ffmpeg is unavailable or conversion fails.
    """
    if not shutil.which("ffmpeg"):
        return None
    tmp_in = tmp_out = None
    try:
        in_suffix = ".wav" if audio_bytes.startswith(b"RIFF") else ".mp3"
        fd, tmp_in = tempfile.mkstemp(suffix=in_suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)
        fd, tmp_out = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_in,
             "-c:a", "libopus", "-b:a", "48k", tmp_out],
            check=True, capture_output=True, timeout=60,
        )
        with open(tmp_out, "rb") as f:
            return io.BytesIO(f.read())
    except Exception as exc:
        logger.warning("Audio→OGG conversion failed: %s", exc)
        return None
    finally:
        for p in (tmp_in, tmp_out):
            if p and os.path.exists(p):
                os.unlink(p)


def _send_text_chunks(bot, chat_id, text):
    """Send a (possibly long) message as HTML, falling back to plain text."""
    for chunk in split_message(text):
        if chunk.strip():
            try:
                bot.send_message(chat_id, format_telegram_html(chunk), parse_mode="HTML")
            except Exception as exc:
                logger.warning("HTML send failed; falling back to plain text: %s", exc)
                bot.send_message(chat_id, strip_telegram_markdown(chunk))


def _deliver_response(bot, chat_id, user, response_text):
    """Deliver an assistant reply honouring the user's voice_mode."""
    lang = user.get("language") or "en"
    mode = user.get("voice_mode") or "text"

    if mode in ("text", "both"):
        _send_text_chunks(bot, chat_id, response_text)

    if mode in ("voice", "both"):
        bot.send_chat_action(chat_id, "record_voice")
        audio_bytes = text_to_speech(response_text[:TTS_MAX_CHARS], lang)
        if audio_bytes:
            bot.send_voice(chat_id, _to_ogg_opus(audio_bytes) or io.BytesIO(audio_bytes))
        else:
            note = VOICE_UNAVAILABLE_MESSAGES.get(lang, VOICE_UNAVAILABLE_MESSAGES["en"])
            bot.send_message(chat_id, note)
            if mode == "voice":
                # TTS unavailable — never leave the user with silence
                _send_text_chunks(bot, chat_id, response_text)


def _process_translator_mode(bot, chat_id, text, target_lang, lang_code):
    """Handle a message while in Translator mode."""
    translated = translate_text(text, target_lang)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔴 Turn off translator", callback_data="translator_off"))

    formatted = format_telegram_html(translated)
    bot.send_message(chat_id, formatted, parse_mode="HTML", reply_markup=markup)

    # Also send TTS Voice note (OGG/Opus so it looks like a real voice message)
    bot.send_chat_action(chat_id, "record_voice")
    audio_bytes = text_to_speech(translated, target_lang)
    if audio_bytes:
        bot.send_voice(chat_id, _to_ogg_opus(audio_bytes) or io.BytesIO(audio_bytes), reply_markup=markup)


def _send_data_export(bot, chat_id, telegram_id, lang):
    """Send the user everything we store about them: one JSON file + saved photos."""
    try:
        adapter = HermesAdapter(f"telegram_{telegram_id}", telegram_user_id=telegram_id, chat_id=chat_id)
        user = get_user(telegram_id) or {}
        memory = HermesAdapter._read_json(adapter.memory_file)
        history = HermesAdapter._read_json(adapter.history_file)

        export = {
            "profile": {
                "telegram_user_id": telegram_id,
                "language": user.get("language"),
                "timezone": user.get("timezone"),
                "city": user.get("city"),
                "voice_mode": user.get("voice_mode"),
                "briefing_enabled": bool(user.get("briefing_enabled")),
                "briefing_time": user.get("briefing_time"),
                "calorie_goal": user.get("calorie_goal"),
                "created_at": user.get("created_at"),
            },
            "memory_facts": [m.get("value") for m in memory if isinstance(m, dict) and "value" in m],
            "conversation_history": history,
            "contacts": get_all_contacts(telegram_id),
            "lists": get_all_lists(telegram_id),
            "expenses": get_recent_expenses(telegram_id, limit=1000),
            "calorie_entries": get_all_calorie_entries(telegram_id, limit=1000),
            "events": get_all_events(telegram_id),
            "pending_reminders": get_pending_reminders(telegram_id),
            "saved_photos": [
                {"label": p["label"], "file_type": p["file_type"], "saved_at": p.get("created_at")}
                for p in list_photos(telegram_id)
            ],
        }

        payload = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
        caption = {
            "ru": "📦 Все ваши данные. Сохранённые фото отправлю следом.",
            "en": "📦 All your data. Saved photos follow below.",
            "uz": "📦 Barcha ma'lumotlaringiz. Saqlangan rasmlar quyida.",
        }.get(lang, "📦 All your data. Saved photos follow below.")
        bot.send_document(
            chat_id,
            io.BytesIO(payload),
            visible_file_name="leyla_data_export.json",
            caption=caption,
        )

        for photo in list_photos(telegram_id)[:20]:
            try:
                file_id = get_photo_file_id(telegram_id, photo["label"])
                if file_id:
                    bot.send_photo(chat_id, file_id, caption=f"📸 {photo['label']}")
                    continue
                path = get_photo(telegram_id, photo["label"])
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        bot.send_photo(chat_id, f, caption=f"📸 {photo['label']}")
            except Exception as exc:
                logger.warning("Export: could not send photo '%s': %s", photo["label"], exc)
    except Exception:
        logger.error("Export error:\n%s", traceback.format_exc())
        err = {
            "ru": "❌ Не удалось подготовить экспорт. Попробуйте позже.",
            "en": "❌ Could not prepare the export. Please try later.",
            "uz": "❌ Eksportni tayyorlab bo'lmadi. Keyinroq urinib ko'ring.",
        }
        bot.send_message(chat_id, err.get(lang, err["en"]))


def _process_and_reply(bot, chat_id, telegram_id, user, text: str) -> None:
    bot.send_chat_action(chat_id, "typing")

    # Daily usage limit (API cost control) — admin is exempt
    if telegram_id != ADMIN_ID and is_limit_reached(telegram_id):
        lang = user.get("language") or "en"
        msg = LIMIT_MESSAGES.get(lang, LIMIT_MESSAGES["en"]).format(limit=get_daily_limit())
        bot.send_message(chat_id, msg)
        return
    increment_usage(telegram_id)

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

    if not response.strip():
        response = "✅"

    # Refresh user — tags may have changed language or voice mode
    user = get_user(telegram_id) or user

    # Send reply as text / voice / both
    _deliver_response(bot, chat_id, user, response)

    # Handle Photo Vault GET — Telegram file_id first (cloud backup), local file as fallback
    pending_get = getattr(hermes, "_pending_get_photo", None)
    if pending_get:
        sent = False
        file_id = get_photo_file_id(telegram_id, pending_get)
        if file_id:
            try:
                bot.send_chat_action(chat_id, "upload_photo")
                bot.send_photo(chat_id, file_id, caption=f"📸 {pending_get}")
                sent = True
            except Exception:
                logger.warning("file_id send failed for '%s' — trying local file", pending_get)
        if not sent:
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

    # Handle AI Image Generation
    pending_image = getattr(hermes, "_pending_image_prompt", None)
    if pending_image:
        try:
            bot.send_chat_action(chat_id, "upload_photo")
            img_bytes = generate_image(pending_image)
            if img_bytes:
                bot.send_photo(chat_id, io.BytesIO(img_bytes), caption=f"🎨 {pending_image[:900]}")
            else:
                err = IMAGE_FAILED_MESSAGES.get(user["language"], IMAGE_FAILED_MESSAGES["en"])
                bot.send_message(chat_id, err)
        except Exception:
            logger.error("Image generation handling failed:\n%s", traceback.format_exc())
            err = IMAGE_FAILED_MESSAGES.get(user.get("language"), IMAGE_FAILED_MESSAGES["en"])
            bot.send_message(chat_id, err)

    # Handle Data Export request
    if getattr(hermes, "_pending_export", False):
        _send_data_export(bot, chat_id, telegram_id, user.get("language") or "en")


def run_bot() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "dummy":
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        return

    bot = TeleBot(token, threaded=True, num_threads=4)
    start_reminder_scheduler(bot)

    # ------------------------------------------------------------------
    # /broadcast (admin) — send per-language messages to every user
    # ------------------------------------------------------------------
    @bot.message_handler(commands=["broadcast"])
    def handle_broadcast(message):
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "You are not authorized to use this command.")
            return

        msg = bot.reply_to(message, "Send Uzbek text. (Type /cancel to abort)")
        bot.register_next_step_handler(msg, process_broadcast_uzbek)

    def process_broadcast_uzbek(message):
        if message.text == "/cancel":
            bot.reply_to(message, "Broadcast cancelled.")
            return

        uzbek_msg = message
        msg = bot.reply_to(message, "Now send Russian text.")
        bot.register_next_step_handler(msg, process_broadcast_russian, uzbek_msg)

    def process_broadcast_russian(message, uzbek_msg):
        if message.text == "/cancel":
            bot.reply_to(message, "Broadcast cancelled.")
            return

        russian_msg = message
        msg = bot.reply_to(message, "Now send English text.")
        bot.register_next_step_handler(msg, process_broadcast_english, uzbek_msg, russian_msg)

    def process_broadcast_english(message, uzbek_msg, russian_msg):
        if message.text == "/cancel":
            bot.reply_to(message, "Broadcast cancelled.")
            return

        english_msg = message
        bot.reply_to(message, "Broadcasting messages...")

        users = get_all_users()
        success = {"uz": 0, "ru": 0, "en": 0}

        for user in users:
            lang = user.get("language")
            if not lang:
                lang = "en"

            if lang == "uz":
                msg_to_send = uzbek_msg
            elif lang == "ru":
                msg_to_send = russian_msg
            else:
                msg_to_send = english_msg

            try:
                bot.copy_message(user["telegram_user_id"], msg_to_send.chat.id, msg_to_send.message_id)
                success[lang] += 1
            except Exception as e:
                logger.warning(f"Broadcast failed for {user['telegram_user_id']}: {e}")

        bot.reply_to(
            message,
            f"✅ Broadcast completed!\nUzbek: {success['uz']}\nRussian: {success['ru']}\nEnglish: {success['en']}",
        )

    # ------------------------------------------------------------------
    # /health (admin) — uptime, users, pending reminders
    # ------------------------------------------------------------------
    def _limit_text() -> str:
        limit = get_daily_limit()
        return f"{limit} msgs/user" if limit > 0 else "unlimited"

    def _pro_limit_text() -> str:
        limit = get_pro_token_limit()
        return f"{limit} tokens/user" if limit > 0 else "disabled"

    def _admin_stats_text() -> str:
        uptime_s = int(time.time() - START_TIME)
        hours, remainder = divmod(uptime_s, 3600)
        minutes, seconds = divmod(remainder, 60)
        users = get_all_users()
        active_today = sum(1 for u in users if get_usage_today(u["telegram_user_id"]) > 0)
        scheduler_alive = any(t.name == "ReminderScheduler" and t.is_alive() for t in threading.enumerate())
        token_stats = get_token_stats_today()
        avg_all = round(token_stats["total_tokens"] / len(users), 1) if users else 0
        pro_model = os.environ.get("OPENROUTER_PRO_MODEL", "openai/gpt-4o")
        base_model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        return (
            "💚 <b>Bot health</b>\n"
            f"• Uptime: {hours}h {minutes}m {seconds}s\n"
            f"• Users: {len(users)} (active today: {active_today})\n"
            f"• Pending reminders: {count_pending()}\n"
            f"• Reminder scheduler: {'alive ✅' if scheduler_alive else 'DEAD ❌'}\n"
            f"• Daily limit: {_limit_text()}\n"
            f"• Tokens today: {token_stats['total_tokens']} total "
            f"(avg {avg_all}/user, {token_stats['avg_tokens_per_token_user']}/token-active user)\n"
            f"• Pro tokens today: {token_stats['pro_tokens']} | pro budget: {_pro_limit_text()}\n"
            f"• Models: pro <code>{pro_model}</code> | base <code>{base_model}</code>"
        )

    @bot.message_handler(commands=["health"])
    def handle_health(message):
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "You are not authorized to use this command.")
            return
        bot.reply_to(message, _admin_stats_text(), parse_mode="HTML")

    # ------------------------------------------------------------------
    # /admin (admin) — control panel
    # ------------------------------------------------------------------
    def _admin_panel_markup() -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
        )
        markup.row(
            InlineKeyboardButton("⚙️ Set daily limit", callback_data="admin_setlimit"),
            InlineKeyboardButton("🧠 Set pro token budget", callback_data="admin_setprolimit"),
        )
        markup.row(
            InlineKeyboardButton("🔓 Remove limit", callback_data="admin_nolimit"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        )
        return markup

    def _admin_users_text() -> str:
        users = get_all_users()
        active_today = sum(1 for u in users if get_usage_today(u["telegram_user_id"]) > 0)
        lines = [f"👥 <b>Users:</b> {len(users)} total, {active_today} active today", ""]
        lines.append("<b>Newest 10:</b>")
        for u in get_recent_users(10):
            tokens = get_token_usage_today(u["telegram_user_id"])
            lines.append(
                f"• <code>{u['telegram_user_id']}</code> | {u.get('language') or '–'} | "
                f"today: {get_usage_today(u['telegram_user_id'])} msgs / {tokens['total_tokens']} tok "
                f"({tokens['pro_tokens']} pro) | since {(u.get('created_at') or '')[:10]}"
            )
        return "\n".join(lines)

    @bot.message_handler(commands=["admin"])
    def handle_admin(message):
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "You are not authorized to use this command.")
            return
        bot.send_message(
            message.chat.id,
            f"🛠 <b>Admin panel</b>\nDaily limit: <b>{_limit_text()}</b>\nPro token budget: <b>{_pro_limit_text()}</b>",
            parse_mode="HTML",
            reply_markup=_admin_panel_markup(),
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
    def handle_admin_callback(call):
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Not authorized")
            return

        data = call.data
        if data == "admin_stats":
            bot.send_message(call.message.chat.id, _admin_stats_text(), parse_mode="HTML")
        elif data == "admin_users":
            bot.send_message(call.message.chat.id, _admin_users_text(), parse_mode="HTML")
        elif data == "admin_setlimit":
            msg = bot.send_message(
                call.message.chat.id,
                f"⚙️ Current daily limit: <b>{_limit_text()}</b>\n"
                "Send the new daily message limit per user (0 = unlimited). Type /cancel to abort.",
                parse_mode="HTML",
            )
            bot.register_next_step_handler(msg, process_set_limit)
        elif data == "admin_setprolimit":
            msg = bot.send_message(
                call.message.chat.id,
                f"🧠 Current pro token budget: <b>{_pro_limit_text()}</b>\n"
                "Send the new daily pro-model token budget per user (0 = disable pro model). Type /cancel to abort.",
                parse_mode="HTML",
            )
            bot.register_next_step_handler(msg, process_set_pro_limit)
        elif data == "admin_nolimit":
            set_daily_limit(0)
            bot.send_message(call.message.chat.id, "✅ Daily limit removed — users are now unlimited.")
        elif data == "admin_broadcast":
            msg = bot.send_message(call.message.chat.id, "Send Uzbek text. (Type /cancel to abort)")
            bot.register_next_step_handler(msg, process_broadcast_uzbek)
        bot.answer_callback_query(call.id)

    def process_set_limit(message):
        if message.from_user.id != ADMIN_ID:
            return
        text = (message.text or "").strip()
        if text == "/cancel":
            bot.reply_to(message, "Cancelled.")
            return
        try:
            new_limit = int(text)
            if new_limit < 0:
                raise ValueError
        except ValueError:
            msg = bot.reply_to(message, "❌ Please send a whole number (0 = unlimited). Type /cancel to abort.")
            bot.register_next_step_handler(msg, process_set_limit)
            return
        set_daily_limit(new_limit)
        if new_limit == 0:
            bot.reply_to(message, "✅ Daily limit removed — users are now unlimited.")
        else:
            bot.reply_to(message, f"✅ Daily limit set to {new_limit} messages per user.")

    def process_set_pro_limit(message):
        if message.from_user.id != ADMIN_ID:
            return
        text = (message.text or "").strip()
        if text == "/cancel":
            bot.reply_to(message, "Cancelled.")
            return
        try:
            new_limit = int(text)
            if new_limit < 0:
                raise ValueError
        except ValueError:
            msg = bot.reply_to(message, "❌ Please send a whole number (0 = disable pro model). Type /cancel to abort.")
            bot.register_next_step_handler(msg, process_set_pro_limit)
            return
        set_pro_token_limit(new_limit)
        if new_limit == 0:
            bot.reply_to(message, "✅ Pro model disabled — all users are on the base model.")
        else:
            bot.reply_to(message, f"✅ Pro token budget set to {new_limit} tokens per user per day.")

    # ------------------------------------------------------------------
    # /start + language selection
    # ------------------------------------------------------------------
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
        if lang_code not in ("ru", "en", "uz"):
            bot.answer_callback_query(call.id)
            return
        get_or_create_user(telegram_id)
        update_user_language(telegram_id, lang_code)
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="✅ Language saved!")
        bot.send_message(call.message.chat.id, format_telegram_html(INTRO_MESSAGES.get(lang_code, INTRO_MESSAGES["en"])), parse_mode="HTML")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "translator_off")
    def handle_translator_off(call):
        update_translator_mode(call.from_user.id, "")
        msg = {"en": "Translator mode OFF. I'm Laila again! 😊", "ru": "Режим переводчика выключен. Я снова Лейла! 😊", "uz": "Tarjimon rejimi o'chirildi. Men yana Layloman! 😊"}
        user = get_user(call.from_user.id)
        lang = user["language"] if user else "en"
        bot.send_message(call.message.chat.id, msg.get(lang, msg["en"]))
        bot.answer_callback_query(call.id)

    # ------------------------------------------------------------------
    # Voice messages → STT → normal processing
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Photos & image documents → vault save or vision analysis
    # ------------------------------------------------------------------
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
                low_cap = caption.lower()
                if "save" in low_cap or "remember" in low_cap or "сохрани" in low_cap or "eslab" in low_cap or "saqla" in low_cap:
                    label = caption
                    # clean up verbs
                    for word in ["save this as", "save", "remember", "сохрани как", "сохрани", "saqlab qol", "saqla"]:
                        if label.lower().startswith(word):
                            label = label[len(word):].strip()

                    if not label:
                        label = "Document"

                    if save_photo(telegram_id, label, tmp_path, file_id=file_id):
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
