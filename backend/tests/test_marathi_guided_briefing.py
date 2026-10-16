"""Marathi Guided Briefing — voice resolution regression tests.

Marathi is the language most likely to silently degrade: it shares the Devanagari script with Hindi, and hi-IN
voices are far more commonly available than mr-IN ones, so the tempting failure mode is "Hindi voice reads
Marathi text and nobody notices." Every test here would FAIL if Marathi accidentally resolved to en-IN, hi-IN,
an undefined voice, or a wrong-language override. They mock the provider; no API key is required.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from floodnet import tts
from floodnet.api.main import app

KEY = "SENTINEL-KEY-NOT-REAL"
MARATHI_TEXT = "Hindmata Flyover येथे सर्वाधिक धोका केंद्रित आहे, अंदाजे 68 सेंटीमीटर."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    tts.clear_voice_cache()
    monkeypatch.setenv(tts.API_KEY_ENV, KEY)
    for L in ("en", "hi", "mr"):
        monkeypatch.delenv(tts.VOICE_ENV[L], raising=False)
    yield
    tts.clear_voice_cache()


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _client(captured, voices_by_lang):
    """Fake httpx.Client. `voices_by_lang` maps a requested languageCode to the voices.list payload."""
    class Fake:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def get(self, url, params=None):
            code = (params or {}).get("languageCode")
            captured.setdefault("list_calls", []).append(code)
            return _Resp({"voices": voices_by_lang.get(code, [])})

        def post(self, url, params=None, json=None):
            captured["body"] = json
            return _Resp({"audioContent": base64.b64encode(b"ID3marathi").decode()})
    return Fake


REAL_MR = [
    {"name": "mr-IN-Standard-A", "languageCodes": ["mr-IN"], "ssmlGender": "FEMALE"},
    {"name": "mr-IN-Wavenet-A", "languageCodes": ["mr-IN"], "ssmlGender": "FEMALE"},
]
REAL_HI = [{"name": "hi-IN-Neural2-A", "languageCodes": ["hi-IN"], "ssmlGender": "FEMALE"}]
REAL_EN = [{"name": "en-IN-Neural2-D", "languageCodes": ["en-IN"], "ssmlGender": "MALE"}]


# ---------------------------------------------------------------- the core Marathi contract
def test_marathi_request_binds_mr_in_language_code_and_voice(monkeypatch):
    cap = {}
    monkeypatch.setattr("httpx.Client", _client(cap, {"mr-IN": REAL_MR}))
    out = tts.synthesize(MARATHI_TEXT, "mr")

    voice = cap["body"]["voice"]
    assert voice["languageCode"] == "mr-IN"
    assert voice["name"].startswith("mr-IN")
    assert voice["name"] in {v["name"] for v in REAL_MR}   # a name the service actually offered
    assert out.language_code == "mr-IN"
    assert cap["body"]["input"]["text"] == MARATHI_TEXT        # text not altered/translated


def test_marathi_discovery_queries_mr_in_not_a_default_language(monkeypatch):
    cap = {}
    monkeypatch.setattr("httpx.Client", _client(cap, {"mr-IN": REAL_MR}))
    tts.synthesize(MARATHI_TEXT, "mr")
    assert cap["list_calls"] == ["mr-IN"]


@pytest.mark.parametrize("wrong", ["en-IN", "hi-IN"])
def test_marathi_never_resolves_to_english_or_hindi(monkeypatch, wrong):
    cap = {}
    monkeypatch.setattr("httpx.Client", _client(cap, {"mr-IN": REAL_MR, "hi-IN": REAL_HI, "en-IN": REAL_EN}))
    tts.synthesize(MARATHI_TEXT, "mr")
    voice = cap["body"]["voice"]
    assert voice["languageCode"] != wrong
    assert not voice["name"].startswith(wrong)


# ---------------------------------------------------------------- the Devanagari trap
def test_marathi_refuses_when_only_hindi_and_english_voices_exist(monkeypatch):
    """The exact failure mode this file guards: hi-IN is available, mr-IN is not. Borrowing the Hindi voice
    would read Marathi text in Hindi phonetics under a Marathi label. It must refuse instead."""
    cap = {}
    monkeypatch.setattr("httpx.Client",
                        _client(cap, {"mr-IN": [], "hi-IN": REAL_HI, "en-IN": REAL_EN}))
    with pytest.raises(tts.TTSUnavailable, match="no mr-IN voice"):
        tts.synthesize(MARATHI_TEXT, "mr")
    assert "body" not in cap   # no synthesis request was ever sent


def test_marathi_ignores_a_hindi_voice_mislabelled_into_the_mr_list(monkeypatch):
    """Defensive: even if the provider returned a hi-IN voice under a mr-IN query, it must not be chosen,
    because the voice itself does not declare mr-IN support."""
    cap = {}
    monkeypatch.setattr("httpx.Client",
                        _client(cap, {"mr-IN": REAL_HI}))       # wrong-language voice in the mr-IN response
    with pytest.raises(tts.TTSUnavailable, match="no mr-IN voice"):
        tts.synthesize(MARATHI_TEXT, "mr")


@pytest.mark.parametrize("bad_override", ["hi-IN-Neural2-A", "en-IN-Neural2-D"])
def test_marathi_override_with_hindi_or_english_voice_is_rejected(monkeypatch, bad_override):
    monkeypatch.setenv(tts.VOICE_ENV["mr"], bad_override)
    monkeypatch.setattr("httpx.Client", _client({}, {"mr-IN": REAL_MR}))
    with pytest.raises(tts.TTSUnavailable, match="does not belong to mr-IN"):
        tts.synthesize(MARATHI_TEXT, "mr")


def test_marathi_override_with_a_real_mr_in_voice_is_honoured(monkeypatch):
    cap = {}
    monkeypatch.setenv(tts.VOICE_ENV["mr"], "mr-IN-Wavenet-A")
    monkeypatch.setattr("httpx.Client", _client(cap, {}))
    tts.synthesize(MARATHI_TEXT, "mr")
    assert cap["body"]["voice"] == {"languageCode": "mr-IN", "name": "mr-IN-Wavenet-A"}
    assert not cap.get("list_calls")   # explicit override skips discovery


# ---------------------------------------------------------------- stale cache across languages
def test_marathi_does_not_inherit_a_voice_cached_for_another_language(monkeypatch):
    """A briefing switched Hindi -> Marathi in one process must not reuse the hi-IN voice."""
    cap = {}
    monkeypatch.setattr("httpx.Client",
                        _client(cap, {"hi-IN": REAL_HI, "mr-IN": REAL_MR}))
    tts.synthesize("हिन्दी", "hi")
    hi_voice = cap["body"]["voice"]["name"]
    tts.synthesize(MARATHI_TEXT, "mr")
    mr_voice = cap["body"]["voice"]["name"]
    assert hi_voice.startswith("hi-IN") and mr_voice.startswith("mr-IN")
    assert hi_voice != mr_voice


# ---------------------------------------------------------------- through the HTTP endpoint
def test_marathi_endpoint_returns_marathi_tagged_audio(monkeypatch):
    cap = {}
    monkeypatch.setattr("httpx.Client", _client(cap, {"mr-IN": REAL_MR}))
    r = TestClient(app).post("/api/tts", json={"text": MARATHI_TEXT, "lang": "mr"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["X-TTS-Language"] == "mr-IN"
    assert r.headers["X-TTS-Voice"].startswith("mr-IN")
    assert r.content == b"ID3marathi"


def test_marathi_endpoint_503s_honestly_when_no_marathi_voice_exists(monkeypatch):
    """The frontend relies on this 503 to show 'Marathi voice unavailable' instead of playing anything."""
    cap = {}
    monkeypatch.setattr("httpx.Client", _client(cap, {"mr-IN": [], "hi-IN": REAL_HI}))
    r = TestClient(app).post("/api/tts", json={"text": MARATHI_TEXT, "lang": "mr"})
    assert r.status_code == 503
    assert "mr-IN" in r.json()["detail"]
    assert KEY not in r.text
