# flake8: noqa: WPS201, WPS202
# WPS201/WPS202 (max-imports=12, max-module-members=7): this is the
# composition root — it wires the FastAPI app, the Twilio webhook, and the
# query-processing pipeline together, so it necessarily imports and defines
# more than a leaf module would. Every individual function below still meets
# WPS's per-function complexity limits (that refactor is the substantive
# part of this pass); see the "moderate split" decision recorded for this
# PR for why further fragmentation wasn't chosen.
"""CyClaw SMS Relay v2 — Twilio webhook sidecar with signature validation,
SQLite-backed sessions, reply paging, and inbound-webhook deduplication.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, status

from sms_relay_config import (
    ACK_TEXT,
    CYCLAW_QUERY_URL,
    CYCLAW_TIMEOUT_SEC,
    ERROR_DETAIL_MAX_CHARS,
    ERROR_SMS_MAX_CHARS,
    SMS_AUTH_WHITELIST,
    SMS_RELAY_HOST,
    SMS_RELAY_PORT,
    TWILIO_FROM_NUMBER,
    twilio_client,
    validator,
)
from sms_relay_db import LogFields, clear_session, get_session, init_db, log_event, mark_seen, seen_msg, set_session
from sms_relay_format import (
    allowed_confirm_commands,
    build_confirm_prompt,
    build_result_footer,
    chunk_text,
    format_page,
    twiml_empty,
    twiml_reply,
)
from sms_relay_util import log_safe, phone_hash

logger = logging.getLogger("cyclaw.sms_relay")

app = FastAPI(title="CyClaw SMS Relay v2", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    logger.info("sms relay db initialized")


# ── CyClaw + Twilio I/O ──────────────────────────────────────────────────────

async def post_cyclaw(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=CYCLAW_TIMEOUT_SEC) as client:
        resp = await client.post(CYCLAW_QUERY_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


# P1: non-blocking send — wraps synchronous Twilio REST call in executor
# to avoid blocking the event loop on every outbound SMS
async def send_sms(to_number: str, text: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: twilio_client.messages.create(
            body=text,
            from_=TWILIO_FROM_NUMBER,
            to=to_number,
        ),
    )


async def handle_cyclaw_result(
    phone: str, query: str, result: dict, msg_sid: str, provider_override: str | None = None,
) -> None:
    if result.get("needs_confirm"):
        allowed = allowed_confirm_commands()
        set_session(phone, {"pending_confirm": True, "original_query": query, "allowed_commands": allowed})
        await send_sms(phone, build_confirm_prompt(result))
        log_event(phone, "needs_confirm", LogFields(
            msg_sid=msg_sid, query=query, detail=json.dumps({"allowed": allowed}),
        ))
        return

    answer = result.get("answer") or "[No answer]"
    footer = build_result_footer(result)
    pages = chunk_text(answer)
    set_session(phone, {
        "pending_confirm": False,
        "pages": pages,
        "page_index": 0,
        "footer": footer,
        "original_query": query,
        "provider_used": provider_override or result.get("model_used"),
    })
    await send_sms(phone, format_page(pages, 0, footer))
    log_event(phone, "answer_sent", LogFields(
        msg_sid=msg_sid, query=query,
        provider=provider_override or result.get("model_used"),
        detail=json.dumps({"pages": len(pages)}),
    ))


# ── Inbound-command handlers ─────────────────────────────────────────────────
# Split out of what was a single dispatcher so each handler stays within
# WPS's per-function complexity limits (locals/returns/awaits/cognitive
# score) instead of one large branch-heavy function.

async def _handle_cancel(phone: str, msg_sid: str) -> None:
    clear_session(phone)
    log_event(phone, "cancel", LogFields(msg_sid=msg_sid))
    await send_sms(phone, "Canceled.")


async def _handle_more(phone: str, session: dict, msg_sid: str) -> None:
    pages = session.get("pages")
    if not pages:
        await send_sms(phone, "Nothing queued. Send a new question.")
        return
    next_idx = session.get("page_index", 0) + 1
    if next_idx >= len(pages):
        await send_sms(phone, "No more pages.")
        return
    session["page_index"] = next_idx
    set_session(phone, session)
    await send_sms(phone, format_page(pages, next_idx, session.get("footer")))
    log_event(phone, "more", LogFields(msg_sid=msg_sid))


async def _handle_pending_confirm(phone: str, session: dict, lowered: str, msg_sid: str) -> None:
    allowed = session.get("allowed_commands") or allowed_confirm_commands()
    if lowered not in allowed:
        await send_sms(phone, f"Reply with: {' / '.join(allowed)} / cancel")
        return
    original_query = session.get("original_query")
    payload = {"query": original_query, "user_confirmed_online": lowered != "local"}
    if lowered != "local":
        payload["online_provider"] = lowered
    log_event(phone, "confirm_submit", LogFields(msg_sid=msg_sid, query=original_query, provider=lowered))
    result = await post_cyclaw(payload)
    clear_session(phone)
    await handle_cyclaw_result(phone, original_query, result, msg_sid, provider_override=lowered)


async def _handle_new_query(phone: str, text: str, msg_sid: str) -> None:
    log_event(phone, "query_submit", LogFields(msg_sid=msg_sid, query=text))
    result = await post_cyclaw({"query": text, "user_confirmed_online": False})
    await handle_cyclaw_result(phone, text, result, msg_sid)


async def _process_query_inner(phone: str, inbound_text: str, msg_sid: str) -> None:
    text = inbound_text.strip()
    if not text:
        await send_sms(phone, "Send a question.")
        return

    session = get_session(phone) or {}
    lowered = text.lower()

    if lowered == "cancel":
        await _handle_cancel(phone, msg_sid)
        return
    if lowered == "more":
        await _handle_more(phone, session, msg_sid)
        return
    if session.get("pending_confirm"):
        await _handle_pending_confirm(phone, session, lowered, msg_sid)
        return
    await _handle_new_query(phone, text, msg_sid)


async def _notify_error(phone: str, msg_sid: str, message: str) -> None:
    try:
        await send_sms(phone, f"CyClaw error: {message[:ERROR_SMS_MAX_CHARS]}")
    except Exception as notify_exc:
        # Best-effort notification only — do not re-raise, so the webhook
        # still returns a valid TwiML response and Twilio doesn't retry
        # endlessly. The failure is logged instead of silently dropped so
        # a broken outbound path (Twilio down, bad creds) is visible.
        safe_sid = json.dumps(msg_sid)
        safe_notify_err = json.dumps(str(notify_exc))
        logger.error(
            "failed to deliver error notification phone=%s sid=%s: %s",
            phone_hash(phone), safe_sid, safe_notify_err, exc_info=True,
        )


# P1: top-level exception handler — silent task failures become error SMS instead of vanishing
async def process_query(phone: str, inbound_text: str, msg_sid: str) -> None:
    try:
        await _process_query_inner(phone, inbound_text, msg_sid)
    except Exception as exc:
        # json.dumps() rather than %s/%r: msg_sid is sanitized at the webhook
        # boundary already (see log_safe()), but JSON-encoding every
        # untrusted argument here additionally escapes control characters
        # into literal \n-style sequences inside a quoted JSON string — a
        # standard, unambiguous encoding step against CWE-117 log injection.
        safe_sid = json.dumps(msg_sid)
        safe_err = json.dumps(str(exc))
        logger.error(
            "process_query failed phone=%s sid=%s: %s",
            phone_hash(phone), safe_sid, safe_err, exc_info=True,
        )
        log_event(phone, "error", LogFields(msg_sid=msg_sid, detail=str(exc)[:ERROR_DETAIL_MAX_CHARS]))
        await _notify_error(phone, msg_sid, str(exc))


# ── Webhook endpoint ─────────────────────────────────────────────────────────

@app.post("/sms")
async def inbound_sms(
    request: Request,
    From: str = Form(...),  # noqa: WPS404 — FastAPI's Form(...) marker is idiomatic, not a real default
    To: str = Form(default=""),  # noqa: WPS404
    Body: str = Form(default=""),  # noqa: WPS404
    MessageSid: str = Form(default=""),  # noqa: WPS404
) -> object:
    form = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(str(request.url), form, signature):
        # phone_hash(), not raw From: this file's own design (see the PR
        # description's "phone numbers hashed in logs" gap fix) hashes
        # phone numbers everywhere else; these two logger.warning calls
        # were the one place that still logged it raw.
        logger.warning("invalid twilio signature from=%s", phone_hash(From))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature")

    # Sanitize once, at the point untrusted webhook data enters the system —
    # MessageSid is logged verbatim at several call sites below, and the
    # Twilio signature check authenticates the request, not the shape of
    # every individual form field (CWE-117 log injection).
    MessageSid = log_safe(MessageSid)

    if SMS_AUTH_WHITELIST and From not in SMS_AUTH_WHITELIST:
        logger.warning("blocked non-whitelist from=%s", phone_hash(From))
        log_event(From, "blocked_sender", LogFields(msg_sid=MessageSid))
        return twiml_empty()

    if MessageSid and seen_msg(MessageSid):
        logger.info("duplicate webhook sid=%s", json.dumps(MessageSid))
        log_event(From, "duplicate_webhook", LogFields(msg_sid=MessageSid))
        return twiml_reply("Duplicate received; ignoring.")

    if MessageSid:
        mark_seen(MessageSid)

    asyncio.create_task(process_query(From, Body, MessageSid))
    return twiml_reply(ACK_TEXT)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SMS_RELAY_HOST, port=SMS_RELAY_PORT)
