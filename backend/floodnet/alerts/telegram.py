"""Telegram alerts: the phone channel that works on a free account (docs/ALERTS_WHATSAPP.md, DECISIONS.md D-31).

Same rules as whatsapp.py: the TEST banner always first (shared compose()), only the chat ids in TELEGRAM_CHAT_IDS,
de-duplicated and rate-limited, never logs or returns the token, chat ids masked.

  TELEGRAM_BOT_TOKEN   from @BotFather (backend .env only, never VITE_)
  TELEGRAM_CHAT_IDS    comma-separated chat ids; a person must press Start on the bot first (groups: negative ids)
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Callable

from .whatsapp import BANNER, DEDUP_WINDOW_S, RATE_MAX, RATE_WINDOW_S, compose

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$")
CHAT_RE = re.compile(r"^-?\d{3,20}$")
TIMEOUT_S = 15.0
HINTS = {
    401: "Telegram rejected the bot token: copy it again from @BotFather",
    400: "Telegram does not know this chat: open the bot on the phone and press Start (for a group, add the bot first)",
    403: "the bot was blocked or removed from the chat: unblock it / add it back and press Start",
}


def mask(chat_id: str) -> str:
    return f"telegram:{chat_id[:2]}{'•' * max(len(chat_id) - 5, 0)}{chat_id[-3:]}" if len(chat_id) > 5 else "telegram:•••"


def config() -> dict:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chats = [c.strip() for c in (os.environ.get("TELEGRAM_CHAT_IDS") or "").split(",") if c.strip()]
    missing = []
    if not TOKEN_RE.match(token):
        missing.append("TELEGRAM_BOT_TOKEN (from @BotFather, like 123456789:AA...)")
    if not chats:
        missing.append("TELEGRAM_CHAT_IDS (your chat id; press Start on the bot first)")
    elif any(not CHAT_RE.match(c) for c in chats):
        missing.append("TELEGRAM_CHAT_IDS must be numbers separated by commas")
    return {"token": token, "chats": [c for c in chats if CHAT_RE.match(c)], "missing": missing}


def status() -> dict:
    c = config()
    return {"configured": not c["missing"], "missing": c["missing"], "recipients": [mask(x) for x in c["chats"]], "banner": BANNER}


def _http_post(url: str, payload: dict) -> tuple[int, dict]:
    import httpx
    r = httpx.post(url, json=payload, timeout=TIMEOUT_S)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


class TelegramSender:
    def __init__(self, post: Callable = _http_post, clock: Callable[[], float] = time.monotonic):
        self._post, self._clock = post, clock
        self._lock = threading.Lock()
        self._recent: dict[str, float] = {}
        self._sent_at: list[float] = []

    def send(self, lines: list[str], kind: str = "test") -> dict:
        c = config()
        if c["missing"]:
            return {"sent": 0, "skipped": "not configured", "missing": c["missing"], "results": []}
        body = compose(lines)
        digest = hashlib.sha256(body.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            self._recent = {k: t for k, t in self._recent.items() if now - t < DEDUP_WINDOW_S}
            self._sent_at = [t for t in self._sent_at if now - t < RATE_WINDOW_S]
            if digest in self._recent:
                return {"sent": 0, "skipped": f"identical alert already sent in the last {DEDUP_WINDOW_S / 60:.0f} min", "results": []}
            if len(self._sent_at) + len(c["chats"]) > RATE_MAX:
                return {"sent": 0, "skipped": f"rate limit: at most {RATE_MAX} messages every {RATE_WINDOW_S / 60:.0f} min", "results": []}
        url = f"https://api.telegram.org/bot{c['token']}/sendMessage"
        results = []
        for chat in c["chats"]:
            try:
                code, payload = self._post(url, {"chat_id": chat, "text": body, "disable_web_page_preview": True})
            except Exception as ex:  # noqa: BLE001 -- the exception text could contain the URL with the token
                results.append({"to": mask(chat), "ok": False, "error": {"code": None, "message": f"Telegram unreachable ({type(ex).__name__})", "hint": ""}})
                continue
            if code == 200 and payload.get("ok"):
                results.append({"to": mask(chat), "ok": True, "status": "delivered to Telegram"})
            else:
                msg = str(payload.get("description") or f"HTTP {code}").replace(c["token"], "<token>")[:200]
                results.append({"to": mask(chat), "ok": False, "error": {"code": code, "message": msg, "hint": HINTS.get(code, "")}})
        n_ok = sum(r["ok"] for r in results)
        if n_ok:
            with self._lock:
                self._recent[digest] = now
                self._sent_at.extend([now] * n_ok)
        return {"sent": n_ok, "kind": kind, "chars": len(body), "results": results}


sender = TelegramSender()
