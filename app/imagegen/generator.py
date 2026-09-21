"""
Image generation via OpenRouter (image-capable chat models, e.g.
google/gemini-2.5-flash-image-preview).

The model is called through the chat-completions endpoint with
`modalities: ["image", "text"]`; the reply carries base64 images in
`message.images`.
"""

import base64
import logging
import os

import requests

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _bytes_from_image_value(value: str) -> bytes | None:
    """Decode a data URL, remote image URL, or raw base64 payload."""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("data:") and ";base64," in value:
        return base64.b64decode(value.split(";base64,", 1)[1])
    if value.startswith("http://") or value.startswith("https://"):
        resp = requests.get(value, timeout=30)
        if resp.status_code == 200 and resp.content:
            return resp.content
        return None
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        return None


def _iter_image_values(message: dict):
    """Yield possible image payloads from known OpenRouter response shapes."""
    for img in message.get("images") or []:
        if isinstance(img, str):
            yield img
        elif isinstance(img, dict):
            yield (img.get("image_url") or {}).get("url", "")
            yield img.get("url", "")
            yield img.get("b64_json", "")

    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                yield (part.get("image_url") or {}).get("url", "")
                yield part.get("url", "")
                yield part.get("b64_json", "")


def generate_image(prompt: str) -> bytes | None:
    """Generate an image from a text prompt. Returns image bytes or None."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.error("OPENROUTER_API_KEY is not set — cannot generate images")
        return None

    model = os.environ.get("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image")
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "Leyla Assistant",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": f"Generate an image: {prompt}"},
                ],
                "modalities": ["image", "text"],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            logger.error("Image gen HTTP %d: %s", resp.status_code, resp.text[:300])
            return None

        data = resp.json()
        message = (data.get("choices") or [{}])[0].get("message", {}) or {}
        for value in _iter_image_values(message):
            try:
                img_bytes = _bytes_from_image_value(value)
            except Exception as exc:
                logger.warning("Image gen: could not decode image payload: %s", exc)
                continue
            if img_bytes:
                return img_bytes

        logger.error("Image gen: no images in response: %s", str(data)[:500])
        return None
    except Exception as exc:
        logger.error("Image generation failed: %s", exc)
        return None
