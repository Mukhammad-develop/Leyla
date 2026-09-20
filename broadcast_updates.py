import os
import time
from dotenv import load_dotenv
from telebot import TeleBot

load_dotenv()

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.users.manager import get_all_users

UPDATE_MESSAGES = {
    "ru": (
        "🚀 **Большое обновление Лейлы!** 🚀\n"
        "Я научилась новым полезным навыкам:\n\n"
        "💱 **Курс валют**: Спросите «Сколько будет 100 долларов в сумах?»\n"
        "🌐 **Переводчик**: Скажите «Включи переводчик на китайский» (переводит текст и голос!)\n"
        "🕌 **Время намаза**: Спросите «Время намаза в Ташкенте»\n"
        "📸 **Чтение фото**: Отправьте мне фото документа, и я прочитаю его для вас!\n"
        "🔎 **Поиск в сети**: Задайте любой вопрос, и я найду ответ в интернете.\n\n"
        "Просто общайтесь со мной как обычно — я всегда готова помочь! 😊"
    ),
    "en": (
        "🚀 **Big Update for Laila!** 🚀\n"
        "I've learned some amazing new skills to help you even more:\n\n"
        "💱 **Live Currency**: Ask me \"How much is 100 USD in UZS?\"\n"
        "🌐 **Translator**: Say \"Turn on translator to Chinese\" and I will translate your voice and text!\n"
        "🕌 **Prayer Times**: Ask \"Prayer times in Tashkent\"\n"
        "📸 **Read Photos**: Send me a photo of a document and I will read it for you!\n"
        "🔎 **Web Search**: Ask me anything to search the internet.\n\n"
        "Just talk to me normally — I'm ready to help! 😊"
    ),
    "uz": (
        "🚀 **Laylo uchun katta yangilanish!** 🚀\n"
        "Sizga yanada ko'proq yordam berish uchun yangi qobiliyatlarni o'rgandim:\n\n"
        "💱 **Valyuta kursi**: «100 dollar necha so'm bo'ladi?» deb so'rang\n"
        "🌐 **Tarjimon**: «Xitoy tiliga tarjimonni yoq» deng (ovoz va matnni tarjima qilaman!)\n"
        "🕌 **Namoz vaqtlari**: «Toshkentda namoz vaqtlari» deb so'rang\n"
        "📸 **Rasm o'qish**: Menga hujjat rasmini yuboring va men uni siz uchun o'qib beraman!\n"
        "🔎 **Internet qidiruv**: Internetdan ma'lumot topish uchun istalgan narsani so'rang.\n\n"
        "Men bilan odatdagidek gaplashavering — yordam berishga tayyorman! 😊"
    ),
}

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        return
        
    bot = TeleBot(token)
    try:
        users = get_all_users()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return
    
    print(f"Starting broadcast to {len(users)} users...")
    success = 0
    
    for user in users:
        lang = user.get("language")
        if not lang or lang not in UPDATE_MESSAGES:
            lang = "en"
            
        message = UPDATE_MESSAGES[lang]
        user_id = user["telegram_user_id"]
        
        try:
            bot.send_message(user_id, message, parse_mode="Markdown")
            print(f"✅ Sent to {user_id} ({lang})")
            success += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"❌ Failed to send to {user_id}: {e}")
            
    print(f"\nBroadcast complete! Successfully sent to {success} out of {len(users)} users.")

if __name__ == "__main__":
    main()
