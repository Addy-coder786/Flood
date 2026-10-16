# WhatsApp test alerts (Twilio)

**Status (2026-10-10): built and tested with a fake Twilio; a real send needs WHATSAPP_TO and a sandbox join.**
Decision: `DECISIONS.md` D-31.

Every message starts with **"⚠ FloodNet TEST ALERT (simulation, not an official warning)"**. FloodNet does not issue
official warnings; these are demo notifications to the team's own phones.

## What is sent

| Button (Alerts tab) | Content |
|---|---|
| Send test | a short test message naming the scenario on screen |
| Send forecast alert | headline alerts and the 5 worst streets by peak forecast depth (`lib/alerts.js`), labelled simulated |
| Send response update | plan totals, unmet needs with reasons, missions needing a decision, latest changes |

Sent only when an operator presses a button; never automatic. A delivery check runs 5 s after sending and shows
`queued / sent / delivered / read / failed` with Twilio's error code and a plain-language hint.

## Safeguards

- Recipients come only from `WHATSAPP_TO` in the backend `.env`; the API never accepts a number from the browser.
- Same text not re-sent within 5 min; at most 20 messages per 10 min; body trimmed to 1,500 characters; lines ≤ 240.
- Credentials are never logged or returned; recipient numbers are masked in every response.
- Tests (`backend/tests/test_alerts_whatsapp.py`) use a fake Twilio: the suite never sends a real message.

## Setup

1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message**: note the sandbox number and the join code.
2. From each phone, send `join <code>` on WhatsApp to the sandbox number.
3. In the repo-root `.env`: `TWILIO_ACCOUNT_SID` (AC…), `TWILIO_AUTH_TOKEN` (or an API key pair),
   `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`, `WHATSAPP_TO=whatsapp:+91XXXXXXXXXX[,…]`. Restart the backend.
4. Alerts tab → **Send test**.

## Finding on this account (2026-10-10): Trial blocks free-text WhatsApp

Checked against the live account: inbound messages arrive (`join twilio-trial`, "Hi"), but **every** outbound API
send, from the trial sender +1 737 250 8034 and from the classic sandbox +1 415 523 8886 alike, is refused with
**21654 ContentSid Required**, even inside the 24-hour window. The Content API that creates templates answers
**20003 "not available on a Trial account"**. So on this Trial account WhatsApp can only send Twilio's preset
templates; FloodNet's own alert text cannot be sent until the account is upgraded and a FloodNet template is approved.

## Sandbox limits (check in the Twilio Console)

- A phone must rejoin the sandbox after some days; rejoin on demo day.
- Free-form messages only within 24 h of the recipient's last message to the sandbox (error 63016 otherwise): have
  each recipient message the sandbox shortly before the demo.
- Trial account: messages come from Twilio's sandbox number, not a FloodNet sender name.

## Telegram: the channel that works on a free account

Because the Twilio Trial account cannot send free-text WhatsApp, the same buttons also send to Telegram
(`floodnet/alerts/telegram.py`): `TELEGRAM_BOT_TOKEN` (from @BotFather) and `TELEGRAM_CHAT_IDS` in `.env`. One click
sends to every configured channel (`POST /api/alerts/phone/send`). Same banner, recipients only from `.env`, same
de-duplication and rate limit; tests in `backend/tests/test_alerts_telegram.py` use a fake Telegram.

## API

`GET /api/alerts/phone/status` · `POST /api/alerts/phone/send {kind, lines}` (all channels) ·

`GET /api/alerts/whatsapp/status` · `POST /api/alerts/whatsapp/send {kind, lines}` · `GET /api/alerts/whatsapp/message/{sid}`
