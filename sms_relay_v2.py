import os
import json
import time
import sqlite3
import asyncio
import hashlib
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import Response
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from xml.sax.saxutils import escape as xml_escape

logging.basicConfig(level=os.environ.get("SMS_LOG_LEVEL", "INFO"))
logger = logging.getLogger("cyclaw.sms_relay")

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]

CYCLAW_QUERY_URL = os.environ.get("CYCLAW_QUERY_URL", "http://127.0.0.1:8787/query")
SMS_AUTH_WHITELIST = {
    x.strip() for x in os.environ.get("SMS_AUTH_WHITELIST", "").split(",") if x.strip()
}

SMS_RELAY_PORT = int(os.environ.get("SMS_RELAY_PORT", "8788"))
SMS_DB_PATH = os.environ.get("SMS_DB_PATH", "sms_relay.db")
SMS_SESSION_TTL_SEC = int(os.environ.get("SMS_SESSION_TTL_SEC", "900"))
SMS_DEDUPE_TTL_SEC = int(os.environ.get("SMS_DEDUPE_TTL_SEC", "3600"))
SMS_SEGMENT_SIZE = max(int(os.environ.get("SMS_SEGMENT_SIZE", "1200")), 100)  # P2: floor guard
SMS_ALLOWED_ONLINE_PROVIDERS = [
    x.strip().lower()
    for x in os.environ.get("SMS_ALLOWED_ONLINE_PROVIDERS", "grok").split(",")
    if x.strip()
]
ACK_TEXT = os.environ.get("SMS_ACK_TEXT", "Processing...")

validator = RequestValidator(TWILIO_AUTH_TOKEN)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI(title="CyClaw SMS Relay v2", docs_url=None, redoc_url=None, openapi_url=None)


# ── DB ──────────────────────────────────────────────────────────────────────────

def db():
    conn = sqlite3.connect(SMS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            phone TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inbound_seen (
            msg_sid TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relay_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            phone_hash TEXT NOT NULL,
            msg_sid TEXT,
            event_type TEXT NOT NULL,
            query_hash TEXT,
            provider TEXT,
            detail TEXT
        );
    """)
    conn.commit()
    conn.close()


@app.on_event("startup")
async def on_startup():
    init_db()
    logger.info("sms relay db initialized at %s", SMS_DB_PATH)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def now_ts() -> int:
    return int(time.time())


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def phone_hash(phone: str) -> str:
    return sha256_text(phone)[:16]


def log_event(phone: str, event_type: str, msg_sid: Optional[str] = None,
              query: Optional[str] = None, provider: Optional[str] = None,
              detail: Optional[str] = None):
    conn = db()
    conn.execute(
        "INSERT INTO relay_log (created_at, phone_hash, msg_sid, event_type, query_hash, provider, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_ts(), phone_hash(phone), msg_sid, event_type,
         sha256_text(query) if query else None, provider, detail),
    )
    conn.commit()
    conn.close()


def cleanup_db():
    # TODO(tech-debt): move to periodic background task rather than per-read call
    conn = db()
    conn.execute("DELETE FROM sessions WHERE updated_at < ?", (now_ts() - SMS_SESSION_TTL_SEC,))
    conn.execute("DELETE FROM inbound_seen WHERE created_at < ?", (now_ts() - SMS_DEDUPE_TTL_SEC,))
    conn.commit()
    conn.close()


def get_session(phone: str) -> Optional[dict]:
    cleanup_db()
    conn = db()
    row = conn.execute(
        "SELECT state_json FROM sessions WHERE phone = ?", (phone,)
    ).fetchone()
    conn.close()
    return json.loads(row["state_json"]) if row else None


def set_session(phone: str, state: dict):
    conn = db()
    conn.execute(
        "INSERT INTO sessions (phone, state_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(phone) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
        (phone, json.dumps(state), now_ts())
    )
    conn.commit()
    conn.close()


def clear_session(phone: str):
    conn = db()
    conn.execute("DELETE FROM sessions WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()


def seen_msg(msg_sid: str) -> bool:
    conn = db()
    row = conn.execute("SELECT 1 FROM inbound_seen WHERE msg_sid = ?", (msg_sid,)).fetchone()
    conn.close()
    return bool(row)


def mark_seen(msg_sid: str):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO inbound_seen (msg_sid, created_at) VALUES (?, ?)",
        (msg_sid, now_ts())
    )
    conn.commit()
    conn.close()


def twiml_reply(text: str) -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Message>{xml_escape(text)}</Message></Response>'
    )
    return Response(content=body, media_type="application/xml")


def twiml_empty() -> Response:
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml"
    )


def chunk_text(text: str, size: int = SMS_SEGMENT_SIZE) -> list[str]:
    # P2: floor prevents zero/negative size infinite loop
    size = max(size, 100)
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
            if split > start + int(size * 0.6):
                end = split
        chunks.append(text[start:end].strip())
        start = end
    return chunks


def format_page(chunks: list[str], idx: int, footer: Optional[str] = None) -> str:
    total = len(chunks)
    page = f"\n\n[{idx+1}/{total}]"
    if idx < total - 1:
        page += " Reply MORE"
    if footer:
        page += f" {footer}"
    return chunks[idx] + page


def allowed_confirm_commands() -> list[str]:
    cmds = ["local"] + [p for p in SMS_ALLOWED_ONLINE_PROVIDERS if p]
    seen: list[str] = []
    for c in cmds:
        if c not in seen:
            seen.append(c)
    return seen


async def post_cyclaw(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=180.0) as client:
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


def build_confirm_prompt(data: dict) -> str:
    msg = data.get("confirm_message") or "Vault miss."
    choices = " / ".join(allowed_confirm_commands() + ["cancel"])
    return f"{msg} Reply: {choices}"


def build_result_footer(data: dict) -> str:
    model = data.get("model_used", "?")
    hits = data.get("hit_count", 0)
    mode = data.get("mode", "")
    bits = [model]
    if mode:
        bits.append(mode)
    bits.append(f"{hits} src")
    return "[" + " | ".join(bits) + "]"


# ── Core logic ──────────────────────────────────────────────────────────────────

async def handle_cyclaw_result(phone: str, query: str, data: dict, msg_sid: str,
                               provider_override: Optional[str] = None):
    if data.get("needs_confirm"):
        allowed = allowed_confirm_commands()
        set_session(phone, {"pending_confirm": True, "original_query": query, "allowed_commands": allowed})
        await send_sms(phone, build_confirm_prompt(data))
        log_event(phone, "needs_confirm", msg_sid=msg_sid, query=query,
                  detail=json.dumps({"allowed": allowed}))
        return

    answer = data.get("answer") or "[No answer]"
    footer = build_result_footer(data)
    pages = chunk_text(answer, SMS_SEGMENT_SIZE)
    set_session(phone, {
        "pending_confirm": False,
        "pages": pages,
        "page_index": 0,
        "footer": footer,
        "original_query": query,
        "provider_used": provider_override or data.get("model_used"),
    })
    await send_sms(phone, format_page(pages, 0, footer))
    log_event(phone, "answer_sent", msg_sid=msg_sid, query=query,
              provider=provider_override or data.get("model_used"),
              detail=json.dumps({"pages": len(pages)}))


# P1: top-level exception handler — silent task failures become error SMS instead of vanishing
async def process_query(phone: str, inbound_text: str, msg_sid: str):
    try:
        await _process_query_inner(phone, inbound_text, msg_sid)
    except Exception as e:
        logger.error("process_query failed phone=%s sid=%s: %s",
                     phone_hash(phone), msg_sid, e, exc_info=True)
        log_event(phone, "error", msg_sid=msg_sid, detail=str(e)[:300])
        try:
            await send_sms(phone, f"CyClaw error: {str(e)[:120]}")
        except Exception:
            pass


async def _process_query_inner(phone: str, inbound_text: str, msg_sid: str):
    text = inbound_text.strip()
    if not text:
        await send_sms(phone, "Send a question.")
        return

    session = get_session(phone) or {}
    lowered = text.lower()

    if lowered == "cancel":
        clear_session(phone)
        log_event(phone, "cancel", msg_sid=msg_sid)
        await send_sms(phone, "Canceled.")
        return

    if lowered == "more":
        pages = session.get("pages")
        page_index = session.get("page_index", 0)
        footer = session.get("footer")
        if not pages:
            await send_sms(phone, "Nothing queued. Send a new question.")
            return
        next_idx = page_index + 1
        if next_idx >= len(pages):
            await send_sms(phone, "No more pages.")
            return
        session["page_index"] = next_idx
        set_session(phone, session)
        await send_sms(phone, format_page(pages, next_idx, footer))
        log_event(phone, "more", msg_sid=msg_sid)
        return

    pending = session.get("pending_confirm")
    if pending:
        allowed = session.get("allowed_commands") or allowed_confirm_commands()
        if lowered in allowed:
            original_query = session.get("original_query")
            payload = {
                "query": original_query,
                "user_confirmed_online": lowered != "local",
            }
            if lowered != "local":
                payload["online_provider"] = lowered
            log_event(phone, "confirm_submit", msg_sid=msg_sid, query=original_query, provider=lowered)
            data = await post_cyclaw(payload)
            clear_session(phone)
            await handle_cyclaw_result(phone, original_query, data, msg_sid, provider_override=lowered)
            return
        await send_sms(phone, f"Reply with: {' / '.join(allowed)} / cancel")
        return

    log_event(phone, "query_submit", msg_sid=msg_sid, query=text)
    data = await post_cyclaw({"query": text, "user_confirmed_online": False})
    await handle_cyclaw_result(phone, text, data, msg_sid)


# ── Webhook endpoint ─────────────────────────────────────────────────────────────

@app.post("/sms")
async def inbound_sms(
    request: Request,
    From: str = Form(...),
    To: str = Form(default=""),
    Body: str = Form(default=""),
    MessageSid: str = Form(default=""),
):
    form = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(str(request.url), form, signature):
        logger.warning("invalid twilio signature from=%s", From)
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    if SMS_AUTH_WHITELIST and From not in SMS_AUTH_WHITELIST:
        logger.warning("blocked non-whitelist from=%s", From)
        log_event(From, "blocked_sender", msg_sid=MessageSid)
        return twiml_empty()

    if MessageSid and seen_msg(MessageSid):
        logger.info("duplicate webhook sid=%s", MessageSid)
        log_event(From, "duplicate_webhook", msg_sid=MessageSid)
        return twiml_reply("Duplicate received; ignoring.")

    if MessageSid:
        mark_seen(MessageSid)

    asyncio.create_task(process_query(From, Body, MessageSid))
    return twiml_reply(ACK_TEXT)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SMS_RELAY_PORT)
