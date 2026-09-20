"""
Prayer times via the Aladhan free API (no key required).
"""

import logging
import requests

logger = logging.getLogger(__name__)

PRAYER_NAMES = {
    "ru": {
        "Fajr": "Фаджр 🌅", "Sunrise": "Восход ☀️", "Dhuhr": "Зухр 🌞",
        "Asr": "Аср 🌤️", "Maghrib": "Магриб 🌇", "Isha": "Иша 🌙",
    },
    "uz": {
        "Fajr": "Bomdod 🌅", "Sunrise": "Quyosh chiqishi ☀️", "Dhuhr": "Peshin 🌞",
        "Asr": "Asr 🌤️", "Maghrib": "Shom 🌇", "Isha": "Xufton 🌙",
    },
    "en": {
        "Fajr": "Fajr 🌅", "Sunrise": "Sunrise ☀️", "Dhuhr": "Dhuhr 🌞",
        "Asr": "Asr 🌤️", "Maghrib": "Maghrib 🌇", "Isha": "Isha 🌙",
    },
}


def get_prayer_times(city: str, country: str = "", lang: str = "en") -> str:
    """
    Fetch today's prayer times for a city and return a formatted string.
    """
    try:
        if country:
            url = "https://api.aladhan.com/v1/timingsByCity"
            params = {"city": city, "country": country, "method": 2}
        else:
            # timingsByAddress works with a bare city name (no country needed)
            url = "https://api.aladhan.com/v1/timingsByAddress"
            params = {"address": city, "method": 2}

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("code") != 200:
            raise ValueError(f"API returned code {data.get('code')}")

        timings = data["data"]["timings"]
        date_info = data["data"]["date"]["readable"]
        meta = data["data"]["meta"]
        tz = meta.get("timezone", "")

        names = PRAYER_NAMES.get(lang, PRAYER_NAMES["en"])
        key_prayers = ["Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha"]

        header = {
            "ru": f"🕌 **Время намаза — {city}**\n📅 {date_info} ({tz})\n",
            "uz": f"🕌 **Namoz vaqtlari — {city}**\n📅 {date_info} ({tz})\n",
            "en": f"🕌 **Prayer Times — {city}**\n📅 {date_info} ({tz})\n",
        }.get(lang, f"🕌 **Prayer Times — {city}**\n📅 {date_info} ({tz})\n")

        lines = [header]
        for p in key_prayers:
            if p in timings:
                label = names.get(p, p)
                lines.append(f"{label}: **{timings[p]}**")

        return "\n".join(lines)

    except Exception as exc:
        logger.error("Prayer times error for city='%s': %s", city, exc)
        err = {
            "ru": f"❌ Не удалось получить время намаза для '{city}'. Проверьте название города.",
            "uz": f"❌ '{city}' uchun namoz vaqtlarini olishda xatolik yuz berdi. Shahar nomini tekshiring.",
            "en": f"❌ Could not fetch prayer times for '{city}'. Please check the city name.",
        }
        return err.get(lang, err["en"])
