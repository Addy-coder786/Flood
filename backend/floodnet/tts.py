"""Neural text-to-speech for the Guided Briefing, proxied server-side.

WHY THIS IS ON THE BACKEND
The provider needs a secret API key. A key shipped to the browser (VITE_* or otherwise) is a published key,
so the browser never sees it: the frontend POSTs text to `/api/tts` and this module calls the provider with a
key read from the server environment only.

WHAT IT IS NOT
This module does not generate, translate, or alter narration. It receives already-approved sentences that
were built from real FloodNet state (frontend `lib/briefing/facts.js` -> `narration.js`) and returns audio for
them. No model here invents a depth, a street, an alert, a cause or a route.

PROVIDER
Google Cloud Text-to-Speech REST v1, verified against the official reference:
  POST https://texttospeech.googleapis.com/v1/text:synthesize
  body {"input":{"text":...},"voice":{"languageCode":...,"name":?},"audioConfig":{"audioEncoding":"MP3",...}}
  response {"audioContent": "<base64>"}
Marathi is supported (mr-IN-Wavenet-A/B/C, mr-IN-Standard-A/B/C), which is why this provider was chosen over
alternatives -- Marathi is a hard requirement here and is missing from several otherwise-good neural TTS APIs.

CONFIGURATION (all optional; absent key simply disables the neural voice)
  GOOGLE_TTS_API_KEY   secret API key. Without it every request returns "unavailable" and the frontend
                       falls back to browser speech. Never logged, never returned in an error body.
  FLOODNET_TTS_VOICE_EN / _HI / _MR
                       optional explicit voice names (e.g. "mr-IN-Wavenet-A"). When unset, only
                       `languageCode` is sent and the provider selects a voice for that language -- the
                       safest default, since it cannot 400 on a voice name that does not exist in a region.
  FLOODNET_TTS_RATE    speaking rate, default 0.95 (slightly below 1.0: a briefing read at normal pace is
                       easier to follow than one read fast).
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
VOICES_ENDPOINT = "https://texttospeech.googleapis.com/v1/voices"
API_KEY_ENV = "GOOGLE_TTS_API_KEY"
RATE_ENV = "FLOODNET_TTS_RATE"
DEFAULT_RATE = 0.95
TIMEOUT_S = 20.0
MAX_CHARS = 1200  # a briefing sentence is far shorter; this is an abuse/cost guard, not a product limit

# BCP-47 tag per supported briefing language. These are the only languages the briefing narrates in.
LANG_TAGS = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
VOICE_ENV = {"en": "FLOODNET_TTS_VOICE_EN", "hi": "FLOODNET_TTS_VOICE_HI", "mr": "FLOODNET_TTS_VOICE_MR"}


class TTSUnavailable(RuntimeError):
    """The neural voice cannot serve this request (no key, upstream failure, bad response).

    Mirrors `rainfall.provider.ProviderUnavailable`: the caller must degrade honestly -- here that means the
    frontend falls back to browser speech and shows a 'voice unavailable' state. It must never be treated as
    a reason to invent audio or to alter the narration text.
    """


@dataclass(frozen=True)
class Synthesized:
    audio: bytes
    media_type: str
    voice: str
    language_code: str


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV))


def unavailable_reason() -> str | None:
    if not is_configured():
        return (f"neural voice not configured: set {API_KEY_ENV} in the server environment "
                "(the browser never receives this key). Browser speech is used as a fallback.")
    return None


def _rate() -> float:
    try:
        return float(os.environ.get(RATE_ENV, DEFAULT_RATE))
    except (TypeError, ValueError):
        return DEFAULT_RATE


# Voice-name preference, highest quality first. Matched as a substring against the REAL voice names the API
# reports for a language -- never used to construct a name, because a fabricated voice id is a hard 400.
_VOICE_PREFERENCE = ("Neural2", "Wavenet", "Standard")

# language_code -> resolved voice name (or None when the language has no voices). Populated once per process
# from the live voices.list response; cleared by clear_voice_cache() in tests.
_voice_cache: dict[str, str | None] = {}


def clear_voice_cache() -> None:
    _voice_cache.clear()


def _discover_voice(language_code: str) -> str | None:
    """Resolve a REAL voice name for `language_code` by asking the API which voices exist.

    Why discovery instead of a hardcoded table: hardcoding voice ids means either inventing identifiers
    (which fail with a 400) or pinning names that Google may retire. `voices.list` is the authoritative
    source, so the name we send is always one the service actually offers for the requested language.

    Returns None when the language genuinely has no voices -- the caller must then fail loudly rather than
    fall back to another language.
    """
    if language_code in _voice_cache:
        return _voice_cache[language_code]

    import httpx
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            r = client.get(VOICES_ENDPOINT,
                           params={"key": os.environ[API_KEY_ENV], "languageCode": language_code})
            r.raise_for_status()
            payload = r.json()
    except Exception as ex:  # noqa: BLE001
        # Same key-safety rule as synthesize(): only the exception CLASS, never the message (which can
        # contain the request URL, which carries the key).
        log.warning("TTS voices.list failed for %s: %s", language_code, type(ex).__name__)
        raise TTSUnavailable(
            f"could not list voices for {language_code} ({type(ex).__name__})") from None

    voices = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(voices, list):
        raise TTSUnavailable(f"voices.list returned an unexpected shape for {language_code}")

    # Keep only voices that genuinely declare support for the requested language. The API filters already,
    # but this makes the guarantee local and testable rather than assumed.
    usable = [v.get("name") for v in voices
              if isinstance(v, dict) and language_code in (v.get("languageCodes") or []) and v.get("name")]
    chosen: str | None = None
    for tier in _VOICE_PREFERENCE:
        match = next((n for n in usable if tier in n), None)
        if match:
            chosen = match
            break
    if chosen is None and usable:
        chosen = usable[0]      # some other tier (Chirp/Studio/Journey) -- still a real voice for this language

    _voice_cache[language_code] = chosen
    if chosen:
        log.info("TTS voice for %s resolved to %s", language_code, chosen)
    return chosen


def synthesize(text: str, lang: str) -> Synthesized:
    """Synthesize one already-approved narration sentence. Raises TTSUnavailable on any failure."""
    reason = unavailable_reason()
    if reason:
        raise TTSUnavailable(reason)

    text = (text or "").strip()
    if not text:
        raise TTSUnavailable("no text supplied")
    if len(text) > MAX_CHARS:
        raise TTSUnavailable(f"text exceeds {MAX_CHARS} characters")

    language_code = LANG_TAGS.get(lang)
    if not language_code:
        raise TTSUnavailable(f"unsupported language {lang!r}; expected one of {sorted(LANG_TAGS)}")

    # ---- resolve an EXPLICIT voice for the SELECTED language -------------------------------------------
    # Both `languageCode` and `name` must come from `lang`. Sending Hindi/Marathi text under an English
    # voice (or under an English languageCode) is the exact defect this guards against, so the chosen voice
    # is verified to declare support for `language_code` before it is used.
    name = os.environ.get(VOICE_ENV[lang], "").strip()
    if name:
        # An operator override still has to be for the right language -- a mismatched override (e.g.
        # FLOODNET_TTS_VOICE_MR set to an en-IN voice) would silently produce English audio for Marathi
        # text, which is precisely the bug being fixed. Fail loudly instead.
        prefix = language_code.lower()
        if not name.lower().startswith(prefix):
            raise TTSUnavailable(
                f"configured voice {name!r} for {lang!r} does not belong to {language_code}; "
                f"set {VOICE_ENV[lang]} to a {language_code} voice or unset it to auto-select")
    else:
        name = _discover_voice(language_code)
        if not name:
            # No voice exists for this language. Refuse explicitly -- never substitute another language's
            # voice, which would speak the text in the wrong accent/phonetics and mislead the user.
            raise TTSUnavailable(
                f"no {language_code} voice is available from the text-to-speech service; "
                "neural narration is unavailable for this language")

    voice: dict = {"languageCode": language_code, "name": name}

    payload = {
        "input": {"text": text},
        "voice": voice,
        # MP3 so the browser can play it straight from a blob with no decoding work.
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": _rate(), "pitch": 0.0},
    }

    import httpx
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            # The key goes in the query string per Google's API-key auth; it is read from the server
            # environment and never returned to the caller or written to a log line.
            r = client.post(ENDPOINT, params={"key": os.environ[API_KEY_ENV]}, json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as ex:  # noqa: BLE001
        # Deliberately does not interpolate the exception's text: an httpx error for a request carrying a
        # key in the query string can echo that URL back. Only the exception CLASS is reported.
        log.warning("TTS request failed: %s", type(ex).__name__)
        raise TTSUnavailable(f"text-to-speech request failed ({type(ex).__name__})") from None

    audio_b64 = data.get("audioContent") if isinstance(data, dict) else None
    if not audio_b64:
        raise TTSUnavailable("text-to-speech response did not include audioContent")
    try:
        audio = base64.b64decode(audio_b64)
    except Exception:  # noqa: BLE001
        raise TTSUnavailable("text-to-speech returned audioContent that was not valid base64") from None
    if not audio:
        raise TTSUnavailable("text-to-speech returned empty audio")

    return Synthesized(audio=audio, media_type="audio/mpeg",
                       voice=voice["name"], language_code=language_code)
