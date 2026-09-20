"""
Live weather via Open-Meteo (free, no API key required).

Geocoding resolves a city name to coordinates, then the forecast endpoint
returns current conditions and today's high/low.
"""

import logging

import requests

logger = logging.getLogger(__name__)

# WMO weather interpretation codes -> (emoji, {lang: description})
_WMO = {
    0: ("☀️", {"en": "Clear sky", "ru": "Ясно", "uz": "Ochiq osmon"}),
    1: ("🌤️", {"en": "Mainly clear", "ru": "Преимущественно ясно", "uz": "Asosan ochiq"}),
    2: ("⛅", {"en": "Partly cloudy", "ru": "Переменная облачность", "uz": "Qisman bulutli"}),
    3: ("☁️", {"en": "Overcast", "ru": "Пасмурно", "uz": "Bulutli"}),
    45: ("🌫️", {"en": "Fog", "ru": "Туман", "uz": "Tuman"}),
    48: ("🌫️", {"en": "Depositing rime fog", "ru": "Изморозь, туман", "uz": "Qirav tuman"}),
    51: ("🌦️", {"en": "Light drizzle", "ru": "Лёгкая морось", "uz": "Yengil mayda yomg'ir"}),
    53: ("🌦️", {"en": "Drizzle", "ru": "Морось", "uz": "Mayda yomg'ir"}),
    55: ("🌧️", {"en": "Dense drizzle", "ru": "Сильная морось", "uz": "Qalin mayda yomg'ir"}),
    61: ("🌧️", {"en": "Slight rain", "ru": "Небольшой дождь", "uz": "Yengil yomg'ir"}),
    63: ("🌧️", {"en": "Rain", "ru": "Дождь", "uz": "Yomg'ir"}),
    65: ("🌧️", {"en": "Heavy rain", "ru": "Сильный дождь", "uz": "Kuchli yomg'ir"}),
    66: ("🌧️", {"en": "Freezing rain", "ru": "Ледяной дождь", "uz": "Muzli yomg'ir"}),
    67: ("🌧️", {"en": "Heavy freezing rain", "ru": "Сильный ледяной дождь", "uz": "Kuchli muzli yomg'ir"}),
    71: ("🌨️", {"en": "Slight snow", "ru": "Небольшой снег", "uz": "Yengil qor"}),
    73: ("🌨️", {"en": "Snow", "ru": "Снег", "uz": "Qor"}),
    75: ("❄️", {"en": "Heavy snow", "ru": "Сильный снег", "uz": "Kuchli qor"}),
    77: ("🌨️", {"en": "Snow grains", "ru": "Снежная крупа", "uz": "Qor donador"}),
    80: ("🌦️", {"en": "Slight showers", "ru": "Небольшой ливень", "uz": "Yengil jala"}),
    81: ("🌧️", {"en": "Showers", "ru": "Ливень", "uz": "Jala"}),
    82: ("⛈️", {"en": "Violent showers", "ru": "Сильный ливень", "uz": "Kuchli jala"}),
    85: ("🌨️", {"en": "Snow showers", "ru": "Снегопад", "uz": "Qor jala"}),
    86: ("❄️", {"en": "Heavy snow showers", "ru": "Сильный снегопад", "uz": "Kuchli qor jala"}),
    95: ("⛈️", {"en": "Thunderstorm", "ru": "Гроза", "uz": "Momaqaldiroq"}),
    96: ("⛈️", {"en": "Thunderstorm with hail", "ru": "Гроза с градом", "uz": "Do'l bilan momaqaldiroq"}),
    99: ("⛈️", {"en": "Thunderstorm with heavy hail", "ru": "Гроза с сильным градом", "uz": "Kuchli do'l bilan momaqaldiroq"}),
}


def _describe_code(code: int, lang: str) -> str:
    emoji, names = _WMO.get(code, ("🌡️", {"en": "Unknown", "ru": "Неизвестно", "uz": "Noma'lum"}))
    return f"{emoji} {names.get(lang, names['en'])}"


def get_weather(city: str, lang: str = "en") -> str:
    """Return a formatted current-weather + today-forecast string for a city."""
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city.strip(), "count": 1, "language": "en", "format": "json"},
            timeout=10,
        ).json()
        results = geo.get("results") or []
        if not results:
            raise ValueError(f"City not found: {city}")
        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        display_name = place.get("name", city)
        country = place.get("country", "")

        wx = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=10,
        ).json()

        cur = wx["current"]
        daily = wx["daily"]
        cur_desc = _describe_code(int(cur.get("weather_code", 0)), lang)
        day_desc = _describe_code(int((daily.get("weather_code") or [0])[0]), lang)
        t = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        hum = cur.get("relative_humidity_2m")
        wind = cur.get("wind_speed_10m")
        t_min = (daily.get("temperature_2m_min") or [None])[0]
        t_max = (daily.get("temperature_2m_max") or [None])[0]
        rain = (daily.get("precipitation_probability_max") or [0])[0]

        where = f"{display_name}, {country}" if country else display_name
        header = {
            "ru": f"🌤️ **Погода — {where}**",
            "uz": f"🌤️ **Ob-havo — {where}**",
            "en": f"🌤️ **Weather — {where}**",
        }.get(lang, f"🌤️ **Weather — {where}**")

        labels = {
            "en": {"now": "Now", "feels": "feels like", "today": "Today", "hum": "Humidity", "wind": "Wind", "rain": "Rain chance", "kmh": "km/h"},
            "ru": {"now": "Сейчас", "feels": "ощущается как", "today": "Сегодня", "hum": "Влажность", "wind": "Ветер", "rain": "Вероятность дождя", "kmh": "км/ч"},
            "uz": {"now": "Hozir", "feels": "his qilinadi", "today": "Bugun", "hum": "Namlik", "wind": "Shamol", "rain": "Yomg'ir ehtimoli", "kmh": "km/soat"},
        }.get(lang, {"now": "Now", "feels": "feels like", "today": "Today", "hum": "Humidity", "wind": "Wind", "rain": "Rain chance", "kmh": "km/h"})

        lines = [
            header,
            f"{cur_desc}",
            f"🌡️ **{labels['now']}:** {t}°C ({labels['feels']} {feels}°C)",
            f"📈 **{labels['today']}:** {t_min}°C … {t_max}°C — {day_desc}",
            f"💧 {labels['hum']}: {hum}%  |  💨 {labels['wind']}: {wind} {labels['kmh']}  |  ☔ {labels['rain']}: {rain}%",
        ]
        return "\n".join(lines)

    except Exception as exc:
        logger.error("Weather error for city='%s': %s", city, exc)
        err = {
            "ru": f"❌ Не удалось получить погоду для '{city}'. Проверьте название города.",
            "uz": f"❌ '{city}' uchun ob-havoni olib bo'lmadi. Shahar nomini tekshiring.",
            "en": f"❌ Could not fetch weather for '{city}'. Please check the city name.",
        }
        return err.get(lang, err["en"])
