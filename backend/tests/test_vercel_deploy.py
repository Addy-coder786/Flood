"""Vercel deployment contract: manifest, routing in Vercel mode, serverless-safe state, and the egress-IP check.

Vercel mode is decided at import time (VERCEL=1), so those checks run the app in a subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from floodnet.api.main import app

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- manifest
def test_vercel_manifest_lists_only_runtime_dependencies():
    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    deps = {d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in manifest["project"]["dependencies"]}
    assert deps == {"numpy", "scipy", "shapely", "pyproj", "networkx", "fastapi", "pydantic", "httpx", "pillow"}
    assert manifest["tool"]["vercel"]["entrypoint"] == "app:app"
    backend = tomllib.loads((REPO / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert not any(d.startswith("pytest") for d in backend["project"]["dependencies"])     # test-only -> [dev]


def test_secrets_and_bulk_never_ship():
    ignore = (REPO / ".vercelignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", ".env.*", "backend/.venv/", "backend/tests/", "data/raw/"):
        assert entry in ignore
    fn = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))["functions"]["app.py"]
    assert ".env" in fn["excludeFiles"] and fn["maxDuration"] <= 300        # Hobby plan maximum
    cfg = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))
    assert "VITE_BASE=/" in cfg["buildCommand"] and cfg["regions"] == ["bom1"]   # Static IP region = function region


# ---------------------------------------------------------------- Vercel mode (subprocess)
_PROBE = r"""
import json, sys
sys.path.insert(0, {repo!r})
import app as entry
from fastapi.testclient import TestClient
import floodnet.config as cfg
c = TestClient(entry.app)
out = {{"state_dir": str(cfg.STATE_DIR).replace("\\", "/"), "on_vercel": cfg.ON_VERCEL,
       "health": c.get("/health").json(), "api_health": c.get("/api/health").status_code,
       "static": c.get("/static/").status_code, "api_404": c.get("/api/nope").status_code}}
if cfg.FRONTEND_REACT_DIST.joinpath("index.html").is_file():
    r = c.get("/dashboard", headers={{"Accept": "text/html"}})
    out["dashboard"] = [r.status_code, r.headers.get("content-type", "")]
print("PROBE" + json.dumps(out))
"""


def test_vercel_mode_serves_frontend_and_api_same_origin():
    env = {**os.environ, "VERCEL": "1", "IMD_API_KEY": ""}
    env.pop("FLOODNET_STATE_DIR", None)
    proc = subprocess.run([sys.executable, "-c", _PROBE.format(repo=str(REPO))], env=env, capture_output=True,
                          text=True, timeout=300, cwd=str(REPO))
    line = next((l for l in proc.stdout.splitlines() if l.startswith("PROBE")), None)
    assert line, proc.stderr[-2000:]
    out = json.loads(line[5:])
    assert out["on_vercel"] is True and out["state_dir"] == "/tmp/floodnet"     # only /tmp is writable there
    assert out["health"] == {"ok": True} and out["api_health"] == 200
    assert out["static"] == 404 and out["api_404"] == 404                        # no local-server routes; API 404s stay JSON
    if "dashboard" in out:                                                        # SPA fallback when a build exists
        assert out["dashboard"][0] == 200 and out["dashboard"][1].startswith("text/html")


# ---------------------------------------------------------------- serverless run state
def test_missing_run_reports_a_stable_code_the_client_can_recover_from():
    c = TestClient(app)
    for path in ("/api/simulate/not-here", "/api/simulation/not-here/frame/0", "/api/simulation/not-here/series"):
        r = c.get(path)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "run_not_found"


# ---------------------------------------------------------------- egress IP (for the IMD key's IP binding)
def test_egress_ip_endpoint_is_hidden_without_admin_token(monkeypatch):
    monkeypatch.delenv("FLOODNET_ADMIN_TOKEN", raising=False)
    assert TestClient(app).get("/api/admin/egress-ip").status_code == 404
    monkeypatch.setenv("FLOODNET_ADMIN_TOKEN", "a" * 32)
    assert TestClient(app).get("/api/admin/egress-ip", headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_egress_ip_endpoint_returns_only_the_ip(monkeypatch):
    monkeypatch.setenv("FLOODNET_ADMIN_TOKEN", "a" * 32)
    monkeypatch.setenv("IMD_API_KEY", "K" * 64)

    class R:
        text = "203.0.113.7\n"
    monkeypatch.setattr(httpx, "get", lambda url, timeout: R())
    body = TestClient(app).get("/api/admin/egress-ip", headers={"X-Admin-Token": "a" * 32}).json()
    assert body["backend_public_egress_ip"] == "203.0.113.7" and body["consistent"] is True
    assert "K" * 64 not in json.dumps(body)


def test_egress_ip_reports_disagreement_instead_of_guessing(monkeypatch):
    monkeypatch.setenv("FLOODNET_ADMIN_TOKEN", "a" * 32)
    answers = iter(["203.0.113.7", "198.51.100.9"])

    class R:
        def __init__(self): self.text = next(answers)
    monkeypatch.setattr(httpx, "get", lambda url, timeout: R())
    body = TestClient(app).get("/api/admin/egress-ip", headers={"X-Admin-Token": "a" * 32}).json()
    assert body["backend_public_egress_ip"] is None and body["consistent"] is False


def test_unconfigured_live_message_keeps_the_phrase_the_frontend_matches(monkeypatch):
    """FloodNetContext.classifyLiveFailure matches "live IMD data is disabled" (it never names a credential)."""
    from floodnet.rainfall.provider import IMDObservationProvider, ProviderUnavailable
    monkeypatch.setenv("IMD_API_KEY", "")
    with pytest.raises(ProviderUnavailable, match="live IMD data is disabled"):
        IMDObservationProvider().get()
