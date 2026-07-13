# CyClaw SMS Relay v2 — Setup Guide

SMS interface to CyClaw. Lets you query your local RAG stack via text message.

## Architecture

```
Your Phone (SMS)
      │
Twilio US Number
      │ POST webhook
ngrok tunnel :8788
      │
sms_relay_v2.py (FastAPI, port 8788)  ← NEW sidecar
      │ POST /query (loopback)
gate.py (FastAPI, 127.0.0.1:8787)     ← unchanged
      │
LangGraph → LM Studio / Grok
```

**Critical:** `gate.py` stays on `127.0.0.1` only. The relay is the only public-facing surface.

## Quick start

### 1. Twilio

1. Sign up at https://twilio.com (free trial ~$15 credit)
2. Get a US SMS-capable number
3. Note `ACCOUNT_SID` and `AUTH_TOKEN`

### 2. Install relay deps

```bash
pip install twilio>=9.0 httpx>=0.27
```

### 3. Configure

```bash
cp sms_relay.env.example .env
# Edit .env with your Twilio creds and your phone number
```

### 4. Run

```bash
# Terminal 1 — CyClaw (unchanged)
python gate.py

# Terminal 2 — SMS relay
export $(cat .env | xargs)
python sms_relay_v2.py

# Terminal 3 — ngrok tunnel
ngrok http 8788
```

### 5. Configure Twilio webhook

In Twilio Console → Phone Numbers → your number → Messaging:
- Set **A Message Comes In** → Webhook → `https://YOUR-NGROK.ngrok-free.app/sms` (HTTP POST)

## SMS commands

| Command | Action |
|---|---|
| Any text | Query CyClaw |
| `more` | Next page of last answer |
| `local` | Confirm vault-miss with offline best-effort |
| `grok` | Confirm vault-miss via Grok (requires `mode=hybrid`) |
| `claude` | Confirm vault-miss via Claude (requires `mode=hybrid` + key) |
| `cancel` | Clear pending confirm state |

## Provider config note

Default `config.yaml` has `app.mode=offline`. Only `local` confirm works until
you enable online providers. See CyClaw README for hybrid mode setup.

## systemd (optional)

Create `/etc/systemd/system/cyclaw-sms-relay.service`:

```ini
[Unit]
Description=CyClaw SMS Relay
After=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/CyClaw
EnvironmentFile=/path/to/CyClaw/.env
ExecStart=/path/to/CyClaw/.venv/bin/python /path/to/CyClaw/sms_relay_v2.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cyclaw-sms-relay
```

## What's improved over PR #526

- Twilio `X-Twilio-Signature` validation (security — was TODO in #526)
- Durable SQLite session state (needs_confirm flow works across multiple SMS)
- Webhook deduplication (Twilio retries if no reply in 15s)
- Answer paging (`more` command)
- Phone number hashing in relay_log (privacy-consistent with CyClaw audit design)
- `.env.example` and this setup doc
