"""
Live currency converter using the exchangerate.host free API (no API key needed).
"""

import logging
import requests

logger = logging.getLogger(__name__)

CURRENCY_ALIASES: dict[str, str] = {
    "dollar": "USD", "dollars": "USD", "доллар": "USD", "долларов": "USD", "usd": "USD",
    "euro": "EUR", "euros": "EUR", "евро": "EUR", "eur": "EUR",
    "ruble": "RUB", "rubles": "RUB", "рубль": "RUB", "рублей": "RUB", "rub": "RUB",
    "som": "UZS", "soum": "UZS", "сум": "UZS", "uzs": "UZS", "sum": "UZS",
    "pound": "GBP", "pounds": "GBP", "фунт": "GBP", "gbp": "GBP",
    "yuan": "CNY", "юань": "CNY", "cny": "CNY", "rmb": "CNY",
    "yen": "JPY", "иена": "JPY", "jpy": "JPY",
    "tenge": "KZT", "тенге": "KZT", "kzt": "KZT",
    "dirham": "AED", "дирхам": "AED", "aed": "AED",
    "lira": "TRY", "лира": "TRY", "try": "TRY",
}


def resolve_currency(raw: str) -> str:
    cleaned = raw.strip().lower()
    return CURRENCY_ALIASES.get(cleaned, raw.strip().upper())


def convert_currency(from_cur: str, to_cur: str, amount: float) -> str:
    from_iso = resolve_currency(from_cur)
    to_iso = resolve_currency(to_cur)

    try:
        resp = requests.get(
            "https://api.exchangerate.host/convert",
            params={"from": from_iso, "to": to_iso, "amount": amount},
            timeout=8,
        )
        data = resp.json()
        if data.get("success") and data.get("result") is not None:
            result = data["result"]
            rate = data.get("info", {}).get("rate", result / amount if amount else 0)
            return (
                f"💱 **{amount:,.2f} {from_iso}** = **{result:,.2f} {to_iso}**\n"
                f"📊 Rate: 1 {from_iso} = {rate:,.4f} {to_iso}\n"
                f"_(Live rate)_"
            )
    except Exception as exc:
        logger.warning("exchangerate.host failed: %s", exc)

    try:
        resp = requests.get(
            f"https://open.er-api.com/v6/latest/{from_iso}",
            timeout=8,
        )
        data = resp.json()
        if data.get("result") == "success":
            rates = data.get("rates", {})
            if to_iso in rates:
                rate = rates[to_iso]
                result = amount * rate
                return (
                    f"💱 **{amount:,.2f} {from_iso}** = **{result:,.2f} {to_iso}**\n"
                    f"📊 Rate: 1 {from_iso} = {rate:,.4f} {to_iso}\n"
                    f"_(Live rate)_"
                )
    except Exception as exc:
        logger.warning("open.er-api.com failed: %s", exc)

    return f"❌ Could not fetch live exchange rate for {from_iso} → {to_iso}. Please try again later."
