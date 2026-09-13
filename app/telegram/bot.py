import os
import re
import logging
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.users.manager import get_or_create_user, update_user_language, update_last_seen, get_user
from app.hermes.adapter import HermesAdapter

logger = logging.getLogger(__name__)

bot = TeleBot(os.environ.get("TELEGRAM_BOT_TOKEN", "dummy"))

# Introduction messages localized
INTRO_MESSAGES = {
    "ru": "👋 Привет! Я твой персональный ИИ-ассистент.\n\nПредставь, что я — человек за компьютером, который может помочь тебе с повседневными задачами.\n\n🧠 Я помню...\n🧮 Я считаю...\n🔎 Я ищу информацию...\n📝 Я пишу тексты...\n\n💬 Просто общайся со мной естественно. Не нужно учить команды. Если хочешь что-то изменить, просто скажи мне об этом!",
    "en": "👋 Hi! I'm your personal AI assistant.\n\nImagine me as a person who has access to a computer and can help you with many of the things a personal assistant could do with one.\n\n🧠 I remember...\n🧮 I calculate...\n🔎 I research...\n📝 I write...\n\n💬 Just talk to me naturally. You don't need to learn commands. If you want to change something about how I work, just tell me.",
    "uz": "👋 Salom! Men sizning shaxsiy AI yordamchingizman.\n\nTasavvur qiling, men kompyuterga kirish huquqiga ega bo'lgan va kundalik vazifalaringizda yordam beradigan insonman.\n\n🧠 Men eslab qolaman...\n🧮 Men hisoblayman...\n🔎 Men ma'lumot izlayman...\n📝 Men yozaman...\n\n💬 Men bilan oddiy gaplashavering. Buyruqlarni yodlash shart emas. Agar nimadir o'zgartirmoqchi bo'lsangiz, shunchaki menga ayting!"
}

def split_message(text, chunk_size=4000):
    """Splits a long message into safe chunks for Telegram (limit is 4096)."""
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

@bot.message_handler(commands=['start'])
def handle_start(message):
    telegram_id = message.from_user.id
    user, is_new = get_or_create_user(telegram_id)
    
    if not user['language']:
        # Ask for language
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang_uz")
        )
        bot.send_message(message.chat.id, "Hello! 👋\nWhich language do you prefer?", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "Welcome back!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def handle_language_selection(call):
    telegram_id = call.from_user.id
    lang_code = call.data.split('_')[1]
    
    update_user_language(telegram_id, lang_code)
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Language saved! / Язык сохранен! / Til saqlandi!"
    )
    
    # Send intro message
    intro = INTRO_MESSAGES.get(lang_code, INTRO_MESSAGES["en"])
    bot.send_message(call.message.chat.id, intro)

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    telegram_id = message.from_user.id
    user = get_user(telegram_id)
    
    if not user or not user['language']:
        handle_start(message)
        return
        
    update_last_seen(telegram_id)
    
    # Process with Hermes
    hermes = HermesAdapter(user['hermes_profile'])
    try:
        response = hermes.send_message(message.text, user['language'])
        
        # Check if Hermes detected a natural language change
        match = re.search(r'\[LANGUAGE_CHANGED_TO:\s*([a-z]{2})\]', response)
        if match:
            new_lang = match.group(1)
            update_user_language(telegram_id, new_lang)
            # Remove the marker from the user-facing response
            response = response.replace(match.group(0), "").strip()
            
        # Send back safely chunked
        for chunk in split_message(response):
            if chunk.strip():
                bot.send_message(message.chat.id, chunk)
                
    except Exception as e:
        logger.error(f"Error processing message for {telegram_id}: {e}")
        bot.send_message(message.chat.id, "An error occurred while processing your request.")

def run_bot():
    logger.info("Starting Telegram Bot...")
    bot.infinity_polling()
