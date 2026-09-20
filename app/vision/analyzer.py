"""
Vision analyzer — sends images to GPT-4o for analysis.

Used for: Read This For Me (OCR), Plant/Food identifier, Photo Vault descriptions.
We deliberately use gpt-4o here because gpt-5.6-luna does not accept image inputs.
"""

import base64
import logging
import os

logger = logging.getLogger(__name__)


def _encode_image(image_path: str) -> str:
    """Base64-encode a local image file."""
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def analyze_image(image_path: str, user_prompt: str, lang: str = "en") -> str:
    """
    Send an image to GPT-4o Vision with a user-defined prompt.
    Returns the model's text response, or an error string.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _error_msg(lang, "no_api_key")

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        b64 = _encode_image(image_path)
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")

        lang_instruction = {
            "ru": "Отвечай на русском языке.",
            "uz": "O'zbek tilida javob ber.",
            "en": "Reply in English.",
        }.get(lang, "Reply in English.")

        system = (
            f"You are Laila, a helpful personal assistant. {lang_instruction} "
            "Be concise, warm, and use emojis where appropriate. "
            "Use **bold** for key terms."
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
            max_tokens=1000,
        )
        return response.choices[0].message.content or ""

    except Exception as exc:
        logger.error("Vision API error: %s", exc)
        return _error_msg(lang, "api_error")


def _error_msg(lang: str, kind: str) -> str:
    msgs = {
        "no_api_key": {
            "ru": "❌ Не удалось проанализировать изображение (нет ключа API).",
            "uz": "❌ Rasmni tahlil qilib bo'lmadi (API kaliti yo'q).",
            "en": "❌ Could not analyse the image (no API key).",
        },
        "api_error": {
            "ru": "❌ Произошла ошибка при анализе изображения. Попробуйте ещё раз.",
            "uz": "❌ Rasmni tahlil qilishda xatolik yuz berdi. Qaytadan urinib ko'ring.",
            "en": "❌ An error occurred while analysing the image. Please try again.",
        },
    }
    return msgs.get(kind, msgs["api_error"]).get(lang, msgs[kind]["en"])
