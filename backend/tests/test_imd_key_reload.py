"""IMD API key hot reload (2026-10-11 fix) and the response-format cases that had no test yet.

Background: the token was already re-read from .env on every request, but the API key was read only from the
process environment (filled from .env once at start-up). A new key for a new IP therefore did nothing until the
backend was restarted. These tests pin the fixed behaviour; no test makes a network call.
"""
from __future__ import annotations

import pytest

from floodnet.rainfall.imd_auth import IMDTokenManager
from floodnet.rainfall.provider import IMDObservationProvider, ProviderUnavailable
from tests.test_rainfall_provider import _FakeResponse, _patch_httpx_client

OLD, NEW = "a" * 64, "b" * 64


def _env_file(tmp_path, key):
    p = tmp_path / ".env"
    p.write_text(f"IMD_EMAIL=x@example.org\nIMD_API_KEY={key}\nIMD_API_TOKEN=t\n", encoding="utf-8")
    return p


def test_key_changed_in_env_file_is_used_without_restart(tmp_path, monkeypatch):
    p = _env_file(tmp_path, OLD)
    monkeypatch.setenv("IMD_API_KEY", OLD)                    # what config.py copied in at start-up
    m = IMDTokenManager(dotenv_path=p)
    assert m.api_key() == OLD
    p.write_text(p.read_text().replace(OLD, NEW), encoding="utf-8")   # operator pastes the new key
    assert m.api_key() == NEW


def test_explicit_environment_variable_still_wins(tmp_path, monkeypatch):
    p = _env_file(tmp_path, OLD)
    monkeypatch.setenv("IMD_API_KEY", "c" * 64)               # set by Docker/Vercel, not from .env
    assert IMDTokenManager(dotenv_path=p).api_key() == "c" * 64


def test_missing_key_reads_as_not_configured(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("IMD_API_TOKEN=t\n", encoding="utf-8")
    monkeypatch.delenv("IMD_API_KEY", raising=False)
    assert IMDTokenManager(dotenv_path=p).api_key() is None


def test_key_removed_from_file_falls_back_to_environment_value(tmp_path, monkeypatch):
    p = _env_file(tmp_path, OLD)
    monkeypatch.setenv("IMD_API_KEY", OLD)
    m = IMDTokenManager(dotenv_path=p)
    p.write_text("IMD_API_TOKEN=t\n", encoding="utf-8")
    assert m.api_key() == OLD                                 # never silently becomes "no key"


class _NotJson(_FakeResponse):
    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_http_200_with_non_json_body_is_unavailable_not_fake_data(monkeypatch):
    _patch_httpx_client(monkeypatch, _NotJson(200, None))
    with pytest.raises(ProviderUnavailable) as e:
        IMDObservationProvider(api_key="k", api_token="t").get()
    assert "IMD current_wx request failed" in str(e.value) and "k" * 3 not in str(e.value)


@pytest.mark.parametrize("payload", ["maintenance", [], {"Station": "Mumbai"}, [{"Last 24 hrs Rainfall": "n/a"}]])
def test_http_200_with_unexpected_shape_is_unavailable(monkeypatch, payload):
    _patch_httpx_client(monkeypatch, _FakeResponse(200, payload))
    with pytest.raises(ProviderUnavailable) as e:
        IMDObservationProvider(api_key="k", api_token="t").get()
    assert "IMD current_wx" in str(e.value)


def test_403_ip_message_is_explained_and_secret_free(monkeypatch):
    key = "d" * 64
    _patch_httpx_client(monkeypatch, _FakeResponse(403, {"error": "IP address 103.115.201.202 not authorized"}))
    with pytest.raises(ProviderUnavailable) as e:
        IMDObservationProvider(api_key=key, api_token="secret-token").get()
    msg = str(e.value)
    assert "403" in msg and "IP" in msg and key not in msg and "secret-token" not in msg
