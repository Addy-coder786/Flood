"""Guided-briefing TTS proxy: contract, language mapping, failure handling and key safety.

These tests never call the real provider. The point is to prove the boundary behaves correctly WITHOUT a
GOOGLE_TTS_API_KEY (the normal state of this repo) and to prove that when a key is present the request we
would send matches the provider's documented contract -- and that the key cannot escape through an error.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from floodnet import tts
from floodnet.api.main import app

SENTINEL_KEY = "SECRET-KEY-MUST-NEVER-LEAK-4242"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_voice_cache():
    """Voice discovery memoizes per language for the process; isolate every test from that cache."""
    tts.clear_voice_cache()
    yield
    tts.clear_voice_cache()


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _voices_for(language_code):
    """A realistic voices.list payload: several real-looking names, all declaring `language_code`."""
    return {"voices": [
        {"name": f"{language_code}-Standard-A", "languageCodes": [language_code], "ssmlGender": "FEMALE"},
        {"name": f"{language_code}-Wavenet-B", "languageCodes": [language_code], "ssmlGender": "MALE"},
        {"name": f"{language_code}-Neural2-C", "languageCodes": [language_code], "ssmlGender": "FEMALE"},
    ]}


def _fake_client_factory(captured, payload=None, raise_exc=None, voices_payload=None):
    """Fake httpx.Client covering BOTH calls the module makes: GET voices.list and POST text:synthesize.

    `voices_payload` may be a dict (used for every language) or a callable taking the requested
    languageCode, so a test can simulate "this language has no voices at all".
    """
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            captured.setdefault("voice_list_calls", []).append(params)
            code = (params or {}).get("languageCode")
            if callable(voices_payload):
                return _Resp(voices_payload(code))
            return _Resp(voices_payload if voices_payload is not None else _voices_for(code))

        def post(self, url, params=None, json=None):
            if raise_exc:
                raise raise_exc
            captured["url"] = url
            captured["params"] = params
            captured["body"] = json
            return _Resp(payload if payload is not None
                         else {"audioContent": base64.b64encode(b"ID3fake").decode()})

    return FakeClient


# ---------------------------------------------------------------- unconfigured (the repo's default state)
def test_status_reports_unconfigured_without_a_key(client, monkeypatch):
    monkeypatch.delenv(tts.API_KEY_ENV, raising=False)
    body = client.get("/api/tts/status").json()
    assert body["available"] is False
    assert tts.API_KEY_ENV in body["reason"]
    assert body["languages"] == ["en", "hi", "mr"]


def test_synthesize_503s_honestly_without_a_key(client, monkeypatch):
    """No key must produce an honest 503 -- never silence presented as success, never fabricated audio."""
    monkeypatch.delenv(tts.API_KEY_ENV, raising=False)
    r = client.post("/api/tts", json={"text": "Test.", "lang": "en"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()


# ---------------------------------------------------------------- input validation
@pytest.mark.parametrize("payload", [
    {"text": "", "lang": "en"},            # empty text
    {"text": "x", "lang": "fr"},           # unsupported language
    {"text": "x" * 5000, "lang": "en"},    # over the length guard
    {"lang": "en"},                        # missing text
])
def test_malformed_requests_are_rejected_not_crashed(client, payload):
    assert client.post("/api/tts", json=payload).status_code == 422


def test_unsupported_language_raises_rather_than_crashing(monkeypatch):
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    with pytest.raises(tts.TTSUnavailable, match="unsupported language"):
        tts.synthesize("x", "fr")


# ---------------------------------------------------------------- provider contract + language mapping
@pytest.mark.parametrize("lang,expected_code", [("en", "en-IN"), ("hi", "hi-IN"), ("mr", "mr-IN")])
def test_language_maps_to_the_right_bcp47_code(monkeypatch, lang, expected_code):
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.delenv(tts.VOICE_ENV[lang], raising=False)
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))

    out = tts.synthesize("Hindmata Flyover.", lang)

    assert captured["url"] == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert captured["body"]["voice"]["languageCode"] == expected_code
    # BOTH fields must come from the selected language. Sending the right text under an English voice was
    # the actual reported defect, so the voice NAME is asserted against the language too, not just the code.
    assert captured["body"]["voice"]["name"].startswith(expected_code)
    assert captured["body"]["audioConfig"]["audioEncoding"] == "MP3"
    assert out.media_type == "audio/mpeg"
    assert out.language_code == expected_code
    assert out.voice.startswith(expected_code)


def test_explicit_voice_name_is_forwarded_when_configured(monkeypatch):
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.setenv(tts.VOICE_ENV["mr"], "mr-IN-Wavenet-A")
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))
    tts.synthesize("x", "mr")
    assert captured["body"]["voice"]["name"] == "mr-IN-Wavenet-A"
    assert captured["body"]["voice"]["languageCode"] == "mr-IN"
    # An explicitly configured voice short-circuits discovery entirely.
    assert not captured.get("voice_list_calls")


# ------------------------------------------------ THE REPORTED BUG: selected language must reach the audio
@pytest.mark.parametrize("lang", ["hi", "mr"])
def test_hindi_and_marathi_never_produce_an_english_request(monkeypatch, lang):
    """Regression test for the reported defect: narration text was Hindi/Marathi but the audio was English.

    Neither the languageCode NOR the voice name may be English when hi/mr is selected."""
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.delenv(tts.VOICE_ENV[lang], raising=False)
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))

    tts.synthesize("Hindmata Flyover.", lang)

    voice = captured["body"]["voice"]
    assert voice["languageCode"] != "en-IN"
    assert not voice["name"].lower().startswith("en-")
    assert voice["languageCode"] == tts.LANG_TAGS[lang]
    assert voice["name"].startswith(tts.LANG_TAGS[lang])


def test_voice_discovery_asks_for_the_selected_language(monkeypatch):
    """Discovery must query voices.list for the SELECTED language, not a default one."""
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.delenv(tts.VOICE_ENV["mr"], raising=False)
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))
    tts.synthesize("x", "mr")
    assert captured["voice_list_calls"][0]["languageCode"] == "mr-IN"


def test_discovery_only_ever_uses_a_name_the_service_offered(monkeypatch):
    """The chosen name must be one voices.list actually returned — never constructed from a pattern."""
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.delenv(tts.VOICE_ENV["hi"], raising=False)
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))
    tts.synthesize("x", "hi")
    offered = [v["name"] for v in _voices_for("hi-IN")["voices"]]
    assert captured["body"]["voice"]["name"] in offered
    assert "Neural2" in captured["body"]["voice"]["name"]   # preference order honoured


def test_language_with_no_voices_fails_loudly_instead_of_speaking_english(monkeypatch):
    """If a language genuinely has no voice, refuse — never substitute another language's voice."""
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.delenv(tts.VOICE_ENV["mr"], raising=False)
    monkeypatch.setattr("httpx.Client",
                        _fake_client_factory({}, voices_payload=lambda code: {"voices": []}))
    with pytest.raises(tts.TTSUnavailable, match="no mr-IN voice"):
        tts.synthesize("x", "mr")


def test_mismatched_voice_override_is_rejected_not_silently_used(monkeypatch):
    """A Marathi slot configured with an English voice would read Marathi text in English. Refuse it."""
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.setenv(tts.VOICE_ENV["mr"], "en-IN-Wavenet-D")
    monkeypatch.setattr("httpx.Client", _fake_client_factory({}))
    with pytest.raises(tts.TTSUnavailable, match="does not belong to mr-IN"):
        tts.synthesize("x", "mr")


def test_each_language_resolves_independently(monkeypatch):
    """Three languages in one process must not share a cached voice — a cache keyed wrongly would let
    whichever language ran first win, which is another way the reported bug could manifest."""
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    for L in ("en", "hi", "mr"):
        monkeypatch.delenv(tts.VOICE_ENV[L], raising=False)
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))

    seen = {}
    for L in ("en", "hi", "mr"):
        tts.synthesize("x", L)
        seen[L] = captured["body"]["voice"]["name"]

    assert seen["en"].startswith("en-IN")
    assert seen["hi"].startswith("hi-IN")
    assert seen["mr"].startswith("mr-IN")
    assert len(set(seen.values())) == 3


def test_key_is_sent_as_a_query_param_not_in_the_body(monkeypatch):
    """The key must never end up in the JSON body (which is easier to log/echo than query params)."""
    captured = {}
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.setattr("httpx.Client", _fake_client_factory(captured))
    tts.synthesize("x", "en")
    assert captured["params"]["key"] == SENTINEL_KEY
    assert SENTINEL_KEY not in str(captured["body"])


# ---------------------------------------------------------------- key safety on failure
def test_api_key_never_leaks_into_an_error_message(monkeypatch):
    """httpx errors can embed the request URL, which carries the key. Only the exception CLASS may surface."""
    import httpx
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    boom = httpx.ConnectError(f"failed connecting to https://...?key={SENTINEL_KEY}")
    monkeypatch.setattr("httpx.Client", _fake_client_factory({}, raise_exc=boom))

    with pytest.raises(tts.TTSUnavailable) as ei:
        tts.synthesize("x", "en")
    assert SENTINEL_KEY not in str(ei.value)
    assert "ConnectError" in str(ei.value)


def test_api_key_never_leaks_through_the_http_endpoint(client, monkeypatch):
    import httpx
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    boom = httpx.ConnectError(f"boom ?key={SENTINEL_KEY}")
    monkeypatch.setattr("httpx.Client", _fake_client_factory({}, raise_exc=boom))
    r = client.post("/api/tts", json={"text": "x", "lang": "en"})
    assert r.status_code == 503
    assert SENTINEL_KEY not in r.text


# ---------------------------------------------------------------- malformed upstream responses
@pytest.mark.parametrize("payload,match", [
    ({}, "audioContent"),
    ({"audioContent": ""}, "audioContent"),
    ({"audioContent": "!!!not-base64!!!"}, "base64"),
    ({"audioContent": base64.b64encode(b"").decode()}, "audioContent"),
])
def test_malformed_upstream_response_degrades_honestly(monkeypatch, payload, match):
    """A bad provider response must raise TTSUnavailable (-> 503 -> browser fallback), never return junk
    audio and never crash the service."""
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.setattr("httpx.Client", _fake_client_factory({}, payload=payload))
    with pytest.raises(tts.TTSUnavailable, match=match):
        tts.synthesize("x", "en")


def test_successful_synthesis_returns_playable_bytes(monkeypatch):
    monkeypatch.setenv(tts.API_KEY_ENV, SENTINEL_KEY)
    monkeypatch.setattr("httpx.Client", _fake_client_factory({}))
    out = tts.synthesize("Risk is increasing.", "en")
    assert out.audio == b"ID3fake"
    assert out.media_type == "audio/mpeg"
