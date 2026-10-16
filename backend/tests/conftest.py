"""Shared test isolation: process-wide singletons that remember upstream failures must start clean per test."""
import base64
import os

import pytest

# A well-formed FAKE token in the IMD portal's own format (base64url JSON {"uid", "exp"} + "." + signature), valid until
# 2100. Tests never see the developer's real .env credentials unless live tests are explicitly opted into.
FAKE_IMD_TOKEN = base64.urlsafe_b64encode(b'{"uid":1,"exp":4102444800}').decode().rstrip("=") + "." + "s" * 64


@pytest.fixture(autouse=True)
def _no_real_imd_credentials(monkeypatch):
    if os.environ.get("RUN_LIVE_IMD") == "1":
        yield
        return
    monkeypatch.setenv("IMD_API_TOKEN", FAKE_IMD_TOKEN)
    monkeypatch.setenv("FLOODNET_IMD_AUTO_RENEW", "0")
    for name in ("IMD_API_TOKEN_FILE", "IMD_TOKEN_PROVIDER", "IMD_EMAIL", "IMD_PASSWORD", "IMD_CREDENTIALS_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    from floodnet.rainfall import imd_auth
    monkeypatch.setattr(imd_auth.manager, "_dotenv", imd_auth.config.REPO_DIR / "__no_dotenv_in_tests__")
    imd_auth.manager._source_key = None
    yield


@pytest.fixture(autouse=True)
def _reset_imd_token_manager():
    from floodnet.rainfall.imd_auth import manager
    manager.reset()
    yield
    manager.reset()


@pytest.fixture(autouse=True)
def _isolated_source_manager(monkeypatch):
    """`auto` runs must never read or write the real on-disk last-good cache during tests."""
    from floodnet.api import state
    from floodnet.rainfall.source_manager import SourceManager
    monkeypatch.setattr(state, "source_manager", SourceManager(cache_dir=None))
    yield
