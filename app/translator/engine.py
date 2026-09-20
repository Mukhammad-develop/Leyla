"""
Translator engine — translates text and optionally generates a voice note.

- Uses LLM for accurate translation with cultural adaptation
- Adds pinyin on a separate line for Chinese outputs (using pypinyin)
- Calls ElevenLabs TTS to produce a voice note in the target language
"""

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel — multilingual
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"

CHINESE_LANGS = {"zh", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "mandarin", "cantonese", "chinese"}

LANG_NAMES = {
    "en": "English", "ru": "Russian", "uz": "Uzbek",
    "zh": "Chinese (Mandarin, Simplified)", "zh-cn": "Chinese (Mandarin, Simplified)",
    "zh-tw": "Chinese (Traditional)", "mandarin": "Chinese (Mandarin)",
    "cantonese": "Cantonese Chinese",
    "de": "German", "fr": "French", "es": "Spanish", "ar": "Arabic",
    "tr": "Turkish", "ko": "Korean", "ja": "Japanese", "it": "Italian",
    "pt": "Portuguese", "hi": "Hindi", "kk": "Kazakh", "uk": "Ukrainian",
    "fa": "Persian (Farsi)", "tg": "Tajik",
}

# Normalize common user inputs to lang codes
LANG_ALIASES = {
    "chinese": "zh", "mandarin": "zh", "cantonese": "zh-tw",
    "english": "en", "russian": "ru", "uzbek": "uz",
    "german": "de", "french": "fr", "spanish": "es", "arabic": "ar",
    "turkish": "tr", "korean": "ko", "japanese": "ja", "italian": "it",
    "portuguese": "pt", "hindi": "hi", "kazakh": "kk", "ukrainian": "uk",
    "persian": "fa", "farsi": "fa", "tajik": "tg",
    # Russian
    "китайский": "zh", "английский": "en", "русский": "ru", "узбекский": "uz",
    "немецкий": "de", "французский": "fr", "испанский": "es", "арабский": "ar",
    "турецкий": "tr", "корейский": "ko", "японский": "ja", "итальянский": "it",
    "португальский": "pt", "хинди": "hi", "казахский": "kk", "украинский": "uk",
    # Uzbek
    "xitoy": "zh", "ingliz": "en", "rus": "ru", "o'zbek": "uz", "uzbek": "uz",
    "nemis": "de", "fransuz": "fr", "ispan": "es", "arab": "ar",
    "turk": "tr", "koreys": "ko", "yapon": "ja",
}


def normalize_lang(raw: str) -> str:
    """Convert a user-written language name to a normalized code."""
    cleaned = raw.strip().lower()
    return LANG_ALIASES.get(cleaned, cleaned)


def _is_chinese(lang: str) -> bool:
    return lang.lower().strip() in CHINESE_LANGS


def _lang_display_name(lang: str) -> str:
    return LANG_NAMES.get(lang.lower(), lang)


def translate_text(text: str, target_lang: str) -> str:
    """
    Translate text to target_lang using the LLM.
    Returns the translated string (with pinyin appended on a new line if Chinese).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return text

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        lang_display = _lang_display_name(target_lang)
        pinyin_note = (
            " After the translation, on a NEW SEPARATE LINE, add the full pinyin pronunciation of the Chinese text."
            if _is_chinese(target_lang) else ""
        )

        system = (
            f"You are a professional translator. "
            f"Translate the user's message accurately into {lang_display}. "
            "Rules:\n"
            "- Translate faithfully — do NOT add, remove, or rephrase words unnecessarily.\n"
            "- Minor cultural adaptation is allowed ONLY if a direct translation would be nonsensical.\n"
            "- Output ONLY the translated text. Do NOT add labels like 'Translation:' or notes.\n"
            f"- Do NOT include any explanation or meta-commentary.{pinyin_note}"
        )

        completion = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        )
        translated = completion.choices[0].message.content or text

        if _is_chinese(target_lang):
            translated = _ensure_pinyin(translated)

        return translated

    except Exception as exc:
        logger.error("Translation error: %s", exc)
        return text


def _ensure_pinyin(text: str) -> str:
    """
    If the LLM hasn't added pinyin yet (or we want to ensure it's there),
    use pypinyin to add it on a separate line.
    """
    try:
        from pypinyin import pinyin, Style

        lines = text.split("\n")
        result_lines = []
        for line in lines:
            result_lines.append(line)
            # Only add pinyin for lines with Chinese characters that don't already look like pinyin
            if any("\u4e00" <= ch <= "\u9fff" for ch in line):
                py = pinyin(line, style=Style.TONE)
                pinyin_str = " ".join(p[0] for p in py if p)
                if pinyin_str.strip():
                    result_lines.append(f"🔤 {pinyin_str}")
        return "\n".join(result_lines)
    except ImportError:
        logger.warning("pypinyin not installed — skipping pinyin")
        return text
    except Exception as exc:
        logger.warning("Pinyin generation failed: %s", exc)
        return text


def text_to_speech(text: str, target_lang: str) -> bytes | None:
    """
    Call ElevenLabs TTS to produce an audio file.
    Returns raw MP3 bytes on success, or None on failure.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — skipping TTS")
        return None

    # Strip markdown and pinyin lines for clean TTS
    clean_text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    clean_text = re.sub(r"\*(.+?)\*", r"\1", clean_text)
    clean_text = re.sub(r"_(.+?)_", r"\1", clean_text)
    clean_text = re.sub(r"🔤.*", "", clean_text)  # remove pinyin lines
    # Remove emojis
    clean_text = re.sub(
        r"[\U00010000-\U0010ffff]|[\u2600-\u27ff]|[\u2b00-\u2bff]", "", clean_text
    ).strip()

    if not clean_text:
        return None

    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": clean_text,
                "model_id": ELEVENLABS_MODEL_ID,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.content
        else:
            logger.error("ElevenLabs TTS error %d: %s", resp.status_code, resp.text[:200])
            return None
    except Exception as exc:
        logger.error("TTS request failed: %s", exc)
        return None
