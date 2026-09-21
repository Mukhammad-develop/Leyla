"""Telegram message formatting helpers.

The assistant and feature modules produce GitHub-style markdown (``**bold**``).
Telegram's HTML parse mode does not understand those markers, so outgoing
messages must be converted before they are sent.
"""

import html
import re


def format_telegram_html(text: str) -> str:
    """Convert the bot's markdown-ish text into Telegram-supported HTML."""
    code_blocks = []

    def save_code_block(match):
        code_blocks.append(match.group(1))
        return f"\x00CB{len(code_blocks)-1}\x00"

    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    inline_codes = []

    def save_inline_code(match):
        inline_codes.append(match.group(1))
        return f"\x00IC{len(inline_codes)-1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", f"<code>{html.escape(code, quote=False)}</code>")
    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{html.escape(code, quote=False)}</code></pre>")

    return text


def strip_telegram_markdown(text: str) -> str:
    """Return readable plain text when Telegram rejects the formatted HTML."""
    code_blocks = []

    def save_code_block(match):
        code_blocks.append(match.group(1))
        return f"\x00CB{len(code_blocks)-1}\x00"

    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    inline_codes = []

    def save_inline_code(match):
        inline_codes.append(match.group(1))
        return f"\x00IC{len(inline_codes)-1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)

    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", code)
    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", code)

    return text
