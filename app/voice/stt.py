"""
ElevenLabs Speech-to-Text integration.

POST https://api.elevenlabs.io/v1/speech-to-text
Headers: xi-api-key
Body: multipart/form-data with 'file' field + 'model_id' field
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def transcribe_voice(file_path: str) -> str | None:
    """
    Send an audio file to ElevenLabs STT and return the transcribed text.
    Returns None on failure.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.error("ELEVENLABS_API_KEY is not set — cannot transcribe voice")
        return None

    try:
        with open(file_path, "rb") as f:
            response = requests.post(
                STT_URL,
                headers={"xi-api-key": api_key},
                files={"file": (os.path.basename(file_path), f, "audio/ogg")},
                data={"model_id": "scribe_v1"},
                timeout=60,
            )

        if response.status_code != 200:
            logger.error(
                "ElevenLabs STT error %d: %s",
                response.status_code,
                response.text[:500],
            )
            return None

        result = response.json()
        text = result.get("text", "").strip()
        if not text:
            logger.warning("ElevenLabs returned empty transcription")
            return None

        return text

    except Exception as exc:
        logger.error("ElevenLabs STT exception: %s", exc)
        return None
