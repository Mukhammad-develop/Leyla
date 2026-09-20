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
        images = (data.get("choices") or [{}])[0].get("message", {}).get("images") or []
        for img in images:
            url = (img.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                b64 = url.split(";base64,", 1)[1]
                return base64.b64decode(b64)
        logger.error("Image gen: no images in response: %s", str(data)[:300])
        return None
    except Exception as exc:
        logger.error("Image generation failed: %s", exc)
        return None
