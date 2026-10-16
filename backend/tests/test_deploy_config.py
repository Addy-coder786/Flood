"""Deployment hardening guards (docs/DEPLOYMENT.md): static checks that would otherwise only fail after a deploy."""
from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from floodnet import config
from floodnet.api.main import app
from floodnet.data.load import REQUIRED

ROOT = config.REPO_DIR
SECRET_NAMES = ("IMD_API_KEY", "IMD_API_TOKEN", "GOOGLE_TTS_API_KEY")


def test_env_example_carries_names_only_and_the_required_ones():
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
    for name in (*SECRET_NAMES, "FLOODNET_TTS_RATE", "VITE_API_BASE_URL"):
        assert name in values, name
    for name in SECRET_NAMES:
        assert values[name].strip() == "", f"{name} must be empty in .env.example"
    assert not [n for n in values if n.startswith("VITE_") and any(s in n for s in ("KEY", "TOKEN", "SECRET"))]


def test_env_is_ignored_by_git_vercel_and_docker():
    for name in (".gitignore", ".vercelignore", ".dockerignore"):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        assert ".env" in [ln.strip() for ln in lines], name
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*COPY\s+\.env", docker, re.M) and "COPY . " not in docker


def test_vercel_builds_the_frontend_and_serves_it_from_the_fastapi_project():
    """One Vercel project: the FastAPI function plus the React build, attached with app.frontend (SPA fallback is
    FastAPI's own, see test_vercel_deploy.py) -- no static-only rewrite table any more."""
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["framework"] == "fastapi" and "outputDirectory" not in cfg and "rewrites" not in cfg
    assert "VITE_BASE=/" in cfg["buildCommand"] and "frontend-react" in cfg["buildCommand"]
    main = (ROOT / "backend/floodnet/api/main.py").read_text(encoding="utf-8")
    assert 'app.frontend("/", directory=str(config.FRONTEND_REACT_DIST), fallback="index.html"' in main


def test_frontend_has_one_configurable_api_origin_and_no_secret_names_behind_vite():
    client = (ROOT / "frontend-react/src/api/client.js").read_text(encoding="utf-8")
    assert "VITE_API_BASE_URL" in client and "export const apiUrl" in client
    vite = (ROOT / "frontend-react/vite.config.js").read_text(encoding="utf-8")
    assert "process.env.VITE_BASE" in vite


def test_pilot_data_needed_at_startup_is_shipped_in_the_image():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY data/processed/pilot data/processed/pilot" in docker
    assert config.DATA_PROCESSED == ROOT / "data" / "processed" / "pilot"      # resolved from the source tree
    for name in REQUIRED:
        assert (config.DATA_PROCESSED / name).is_file(), name


def test_cors_allows_a_separately_hosted_frontend_and_exposes_tts_headers():
    r = TestClient(app).options("/api/health", headers={
        "Origin": "https://floodnet.vercel.app", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "https://floodnet.vercel.app")
    g = TestClient(app).get("/api/health", headers={"Origin": "https://floodnet.vercel.app"})
    assert "X-TTS-Voice" in g.headers.get("access-control-expose-headers", "")


def test_production_build_ships_the_lazy_3d_terrain_chunk_and_no_terrain_service():
    import pytest
    assets = ROOT / "frontend-react" / "dist" / "assets"
    if not assets.is_dir():
        pytest.skip("frontend not built (npm run build)")
    chunks = list(assets.glob("Terrain3D-*.js"))
    assert chunks, "3D terrain chunk missing from the production build"
    # Since the per-route split (D-45: public page, driver page, dashboard) the API client is a shared chunk, not part
    # of index-*.js: search every shipped script. This is stricter than the old check (index + terrain chunk only).
    code = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in assets.glob("*.js"))
    assert "/api/terrain/dem" in code                      # the DEM comes from FloodNet's own API (via apiUrl)
    for host in ("api.mapbox.com", "cesium.com", "maptiler.com", "terrarium", "elevation-tiles"):
        assert host not in code


def test_built_frontend_contains_no_credential_names_values_or_local_urls():
    import os
    import pytest
    assets = ROOT / "frontend-react" / "dist"
    if not (assets / "assets").is_dir():
        pytest.skip("frontend not built (npm run build)")
    code = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in assets.rglob("*") if p.suffix in (".js", ".html", ".css"))
    for word in ("IMD_API_TOKEN", "GOOGLE_TTS_API_KEY", "FLOODNET_ADMIN_TOKEN", "X-Admin-Token", "localhost", "127.0.0.1"):
        assert word not in code, word
    for word in ("IMD_API_KEY", "IMD_EMAIL", "IMD_PASSWORD", "access_token", "Authorization", "oauth/token"):
        assert word not in code, word                  # not even the names: the browser handles no IMD credential
    for name in ("IMD_API_KEY", "IMD_API_TOKEN", "IMD_EMAIL", "IMD_PASSWORD", "GOOGLE_TTS_API_KEY"):
        value = os.environ.get(name)
        if value and len(value) >= 8:
            assert value not in code, f"{name} VALUE is in the frontend bundle"
