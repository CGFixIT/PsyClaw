"""TwiML rendering and reply-paging helpers for the SMS relay."""
from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape

from fastapi.responses import Response

from sms_relay_config import SMS_ALLOWED_ONLINE_PROVIDERS, SMS_SEGMENT_SIZE

_SEGMENT_SIZE_FLOOR = 100
_SPLIT_SEARCH_RATIO = 0.6


def twiml_reply(text: str) -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Message>{xml_escape(text)}</Message></Response>'
    )
    return Response(content=body, media_type="application/xml")


def twiml_empty() -> Response:
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


def chunk_text(text: str, size: int = SMS_SEGMENT_SIZE) -> list[str]:  # noqa: WPS210
    # P2: floor prevents zero/negative size infinite loop
    size = max(size, _SEGMENT_SIZE_FLOOR)
    text = text.strip()
    if not text:
        return ["[empty]"]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            split = text.rfind("\n", start, end)
            if split == -1:
                split = text.rfind(" ", start, end)
            if split > start + int(size * _SPLIT_SEARCH_RATIO):
                end = split
        chunks.append(text[start:end].strip())
        start = end
    return chunks


def format_page(chunks: list[str], idx: int, footer: str | None = None) -> str:
    total = len(chunks)
    page_number = idx + 1
    parts = [chunks[idx], f"\n\n[{page_number}/{total}]"]
    if idx < total - 1:
        parts.append(" Reply MORE")
    if footer:
        parts.append(f" {footer}")
    return "".join(parts)


def allowed_confirm_commands() -> list[str]:
    commands = ["local"] + [provider for provider in SMS_ALLOWED_ONLINE_PROVIDERS if provider]
    seen: list[str] = []
    for command in commands:
        if command not in seen:
            seen.append(command)
    return seen


def build_confirm_prompt(cyclaw_result: dict) -> str:
    message = cyclaw_result.get("confirm_message") or "Vault miss."
    choices = " / ".join(allowed_confirm_commands() + ["cancel"])
    return f"{message} Reply: {choices}"


def build_result_footer(cyclaw_result: dict) -> str:
    model = cyclaw_result.get("model_used", "?")
    hits = cyclaw_result.get("hit_count", 0)
    mode = cyclaw_result.get("mode", "")
    bits = [model]
    if mode:
        bits.append(mode)
    bits.append(f"{hits} src")
    joined_bits = " | ".join(bits)
    return f"[{joined_bits}]"
