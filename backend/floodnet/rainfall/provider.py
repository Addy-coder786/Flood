"""Rainfall provider abstraction: separates rainfall SOURCE from the simulation.

The simulation engine (`floodnet.simulation.engine`) consumes a `RainfallScenario` (see `contracts.py`) as its
only rainfall interface and is untouched by this module. A `RainfallProvider` is simply a different way of
producing a `RainfallScenario`, plus a `RainfallSourceMeta` describing where that scenario's numbers came
from, so callers (the API layer) can ask "give me rainfall input X" without knowing whether X is a hardcoded
design storm, a replayed historical event, or (in the future) a live nowcast feed.

Six providers are wired today:
  - `ScenarioProvider`          SYNTHETIC design storms: moderate / heavy / cloudburst.
  - `HistoricalReplayProvider`  REAL, transcribed historical event: 26 July 2005 (Chitale Committee report).
  - `IMDObservationProvider`    REAL observed rainfall from the IMD API Management Platform
                                 (api.imd.gov.in), when `IMD_API_KEY` is configured -- see
                                 `docs/LIVE_RAINFALL_AUDIT.md` for the full access audit. Raises
                                 `ProviderUnavailable` (not a fabricated value) when no key is configured or
                                 the call fails.
  - `ECMWFForecastProvider`     NWP forecast (0-3h precipitation) from ECMWF via Open-Meteo
                                 (api.open-meteo.com/v1/ecmwf) -- see `docs/ECMWF_OPENMETEO_AUDIT.md`.
                                 (Introduced while IMD API access was pending; IMD access has worked since
                                 2026-09-18 and ECMWF remains the forward-looking NWP source.) This is a
                                 numerical-weather-prediction FORECAST, not a radar nowcast and not an IMD
                                 product; never labelled as either. Raises `ProviderUnavailable` (not a
                                 fabricated value) if the call fails or returns insufficient data.
  - `IMDRadarNowcastProvider`   DISABLED-BY-DEFAULT integration boundary for IMD Doppler Weather Radar. It is
                                 the honest place a radar feed would attach, and it is deliberately inert:
                                 `.get()` always raises `ProviderUnavailable` with an "ACCESS PENDING -- ..."
                                 reason. No radar image is fetched, no pixel is decoded, no rainfall value is
                                 invented or borrowed from another source. See decision D-14
                                 (`docs/DECISIONS.md`) and `docs/LIVE_RAINFALL_AUDIT.md` §8b/§8c.
  - `ExternalNowcastProvider`   DEPRECATED, retained only for import compatibility -- superseded by
                                 `IMDRadarNowcastProvider`. Calling `.get()` raises `NotImplementedError`.

No rainfall data is invented or duplicated here: `ScenarioProvider` and `HistoricalReplayProvider` both
delegate to the existing `floodnet.data.scenarios.scenarios()` dict (see that module's docstring for the
full provenance of each series) rather than re-declaring the numbers.

`RainfallSourceMeta` is a NEW dataclass local to this module (not `contracts.py`) -- it augments, but never
replaces, a scenario's own `.provenance` field.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from .. import config
from ..config import HORIZON_S, RAIN_DT_S
from ..contracts import RainfallScenario
from ..provenance import Provenance, Tag

log = logging.getLogger(__name__)

SCENARIO_IDS: tuple[str, ...] = ("moderate", "heavy", "cloudburst")
HISTORICAL_REPLAY_ID = "july2005"
LIVE_ID = "live"
ECMWF_ID = "ecmwf"
RADAR_ID = "imd_radar"
IMERG_ID = "imerg"        # NASA/JAXA GPM IMERG satellite QPE -- gridded, CC0, NOT radar (see the provider)
SPATIAL_SYNTHETIC_ID = "synthetic_spatial"   # invented moving storm; exercises the spatial pathway
IMD_SRI_ID = "imd_sri"   # EXPERIMENTAL field decoded from IMD's public Mumbai-Veravali DWR SRI *image*


@dataclass(frozen=True)
class RainfallSourceMeta:
    """Metadata about WHERE a `RainfallScenario`'s numbers came from (not what they are)."""
    source_type: str            # "scenario" | "historical_replay" | "live_observation" | "ecmwf_forecast"
                                  # | "radar_nowcast" (reported by provider_status() only -- the radar
                                  # boundary never produces a scenario, so it never produces a meta either)
    source_name: str
    timestamp: str               # ISO8601 fetch/production time; "N/A (design storm)" for synthetic scenarios,
                                  # or the real historical event date for a replay
    forecast_horizon_min: int
    resolution_min: int          # temporal resolution of the underlying source series (minutes)
    data_mode: str                # "SYNTHETIC" | "REAL" | "DEMONSTRATION" | "NWP" (matches provenance.Tag)
    provenance: Provenance
    # Small structured extras a UI can render as distinct labelled fields without parsing prose out of
    # `provenance.note`. Empty for scenario/replay (their note text is already a complete, short story);
    # populated by IMDObservationProvider with exactly the fields a "live" run needs to show honestly:
    # station, the raw observed value, when it was observed, the derived persistence rate, and an explicit
    # "forecast_extension" label the UI must show verbatim (never call it a nowcast/forecast on its own).
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"source_type": self.source_type, "source_name": self.source_name, "timestamp": self.timestamp,
                "forecast_horizon_min": self.forecast_horizon_min, "resolution_min": self.resolution_min,
                "data_mode": self.data_mode, "provenance": self.provenance.to_dict(), "detail": self.detail}


class ProviderUnavailable(RuntimeError):
    """Raised by a REAL (non-stub) provider that cannot serve a value right now -- missing credentials, the
    upstream call failed, the credentials are wrong, the source is deliberately disabled, or the response
    couldn't be parsed. Distinct from `NotImplementedError` (raised by the deprecated
    `ExternalNowcastProvider`, where no adapter exists at all): this means the provider exists and is wired,
    but this particular attempt cannot legitimately produce a value. Callers must treat this as "show the
    user the source is unavailable and let them pick replay/scenario instead" -- never as a signal to
    substitute synthetic data while still claiming LIVE, and never as a signal to fall through to a
    different provider under the requested source's label."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _staleness(age_min: Optional[float], fresh_max_min: float, aging_max_min: float) -> str:
    """FRESH / AGING / STALE from an age in minutes against named thresholds (inclusive upper bounds). UNKNOWN
    when the age cannot be computed, or is negative (a source time in the future is not trusted as fresh)."""
    if age_min is None or age_min < 0:
        return "UNKNOWN"
    if age_min <= fresh_max_min:
        return "FRESH"
    if age_min <= aging_max_min:
        return "AGING"
    return "STALE"


def timing_staleness(timing: Optional[dict], now: Optional[datetime] = None) -> Optional[dict]:
    """Re-evaluate a provider's `detail["timing"]` block at `now` (default: now, UTC) -> {"age_min", "staleness"},
    or None when there is no timing block. Uses only what the block itself declares (its age basis and its
    thresholds), so status endpoints can report CURRENT staleness for a cached fetch without a network call."""
    if not isinstance(timing, dict) or "age_basis" not in timing:
        return None
    now = now or _utc_now()
    age = None
    try:
        basis = datetime.fromisoformat(str(timing.get(timing["age_basis"])))
        if basis.tzinfo is not None:
            age = round((now - basis).total_seconds() / 60.0, 1)
    except (TypeError, ValueError):
        age = None
    return {"age_min": age,
            "staleness": _staleness(age, timing.get("fresh_max_min", 0), timing.get("aging_max_min", 0))}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def _parse_imd_obs_utc(date_s: object, clock_s: object) -> Optional[datetime]:
    """IMD current_wx `Date of Observation` + hour/time (UTC per the IMD API reference) -> aware UTC datetime.
    Accepts the live API's bare hour ("3" -> 03:00:00) and the documented "HH:MM[:SS]". Anything else -> None:
    an observation time is never guessed (a date without a time is not assumed to be 00 UTC)."""
    try:
        day = datetime.strptime(str(date_s).strip(), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    c = str(clock_s).strip() if clock_s is not None else ""
    if not c:
        return None
    parts = c.split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in parts + ["0"] * (3 - len(parts)))
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return None
    return day.replace(hour=h, minute=m, second=s, tzinfo=timezone.utc)


class RainfallProvider(ABC):
    """Common interface for "a source of rainfall input to the simulation engine".

    Implementations must never fabricate rainfall: if a source cannot produce a real value, raise (see
    `ExternalNowcastProvider`) rather than inventing one.
    """

    @abstractmethod
    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        """Return (scenario, meta) for the requested rainfall input."""
        ...


class ScenarioProvider(RainfallProvider):
    """SYNTHETIC design-storm scenarios (moderate / heavy / cloudburst).

    Sourced from `floodnet.data.scenarios.scenarios()` -- the numbers are not duplicated here.
    """

    def __init__(self, ids: tuple[str, ...] = SCENARIO_IDS):
        self.ids = ids

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        from ..data.scenarios import scenarios as _scenarios
        sid = scenario_id or self.ids[0]
        if sid not in self.ids:
            raise KeyError(f"ScenarioProvider does not serve {sid!r}; available: {list(self.ids)}")
        scen = _scenarios()[sid]
        meta = RainfallSourceMeta(source_type="scenario", source_name=scen.name,
                                   timestamp="N/A (design storm)", forecast_horizon_min=HORIZON_S // 60,
                                   resolution_min=RAIN_DT_S // 60, data_mode=Tag.SYNTHETIC.value,
                                   provenance=scen.provenance)
        return scen, meta


class HistoricalReplayProvider(RainfallProvider):
    """REAL historical event replay: 26 July 2005, IMD Santacruz gauge, via the Chitale Committee report.

    Sourced from `floodnet.data.scenarios.scenarios()['july2005']` -- does not re-declare the Chitale
    Committee / Santacruz numbers, only reads them from the existing scenario object.
    """

    SCENARIO_ID = HISTORICAL_REPLAY_ID

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        from ..data.scenarios import scenarios as _scenarios
        sid = scenario_id or self.SCENARIO_ID
        if sid != self.SCENARIO_ID:
            raise KeyError(f"HistoricalReplayProvider only serves {self.SCENARIO_ID!r}, got {sid!r}")
        scen = _scenarios()[self.SCENARIO_ID]
        meta = RainfallSourceMeta(source_type="historical_replay", source_name=scen.name,
                                   timestamp="2005-07-26", forecast_horizon_min=HORIZON_S // 60,
                                   resolution_min=60, data_mode=Tag.REAL.value, provenance=scen.provenance)
        return scen, meta


class IMDObservationProvider(RainfallProvider):
    """REAL, currently-observed rainfall from the IMD API Management Platform (api.imd.gov.in), used to
    drive a "LIVE" run -- see `docs/LIVE_RAINFALL_AUDIT.md` for the full source audit this class implements.

    WHAT THIS ACTUALLY GETS, and why it cannot be a true radar/NWP nowcast
    ------------------------------------------------------------------------
    The audit found no IMD API product that returns a quantitative rainfall INTENSITY time series at the
    engine's native 5-min resolution. The best real, quantitative, station-specific value documented is the
    `current_wx` endpoint's "Last 24 hrs Rainfall" (mm) -- a single cumulative total for the last 24 hours at
    one station, current as of its last observation time. `district_nowcast`/`station_nowcast` exist but are
    CATEGORICAL warning bands (e.g. "Cat12: Heavy rain: > 15 mm/hr"), not a numeric mm/h value -- using them
    to synthesise a quantitative hyetograph would be exactly the kind of silent fabrication this project
    forbids, so this class does not use them.

    NORMALISATION (the only transformation applied, and it is a real one, not hidden):
    the observed 24h total is divided by 24 to get a mean mm/h rate, then held CONSTANT (a "persistence"
    assumption -- the simplest, most conservative real nowcasting baseline, not a radar extrapolation) across
    every 5-min step of the 3h forecast window. This is why the resulting `RainfallScenario` is tagged
    `Tag.ESTIMATED`, not `Tag.REAL`: the OBSERVATION is real, the 3h SHAPE is a stated, simple assumption
    layered on top of it -- exactly the same "REAL input + stated derivation rule = ESTIMATED" pattern
    already used elsewhere in this codebase (e.g. Manning's n, node inverts).

    AUTHENTICATION (resolved -- two DIFFERENT credentials, not one value sent twice)
    --------------------------------------------------------------------------------
    The IMD API Management Platform evaluates auth BEFORE routing (proven with a nonsense-path control that
    returned a byte-identical 401), and it requires BOTH headers on every request:
        X-API-Key:     <subscription key>     -> IMD_API_KEY
        Authorization: Bearer <JWT>            -> IMD_API_TOKEN   (a different value; NOT the API key)
    An earlier version of this class sent the single `IMD_API_KEY` value as both headers and additionally as
    an `apikey` query parameter, because the transmission mechanism was then unverified. That is now settled:
    the `apikey` query parameter is NOT accepted (a request carrying it and nothing else returns
    `{"error":"API key missing"}`), so it has been removed -- it could never authenticate and only risked
    leaking the key into URLs, logs and proxies. See docs/LIVE_RAINFALL_AUDIT.md.
    """

    STATION_ID_ENV = "IMD_STATION_ID"
    API_KEY_ENV = "IMD_API_KEY"                   # value of the X-API-Key header (subscription key)
    API_TOKEN_ENV = "IMD_API_TOKEN"               # value of the Authorization: Bearer header (JWT)
    API_KEY_HEADER_ENV = "IMD_API_KEY_HEADER"     # override the API-key header NAME only; default X-API-Key
    DEFAULT_STATION_ID = "43003"                  # Mumbai-Santacruz; see audit doc for how this was verified
    BASE_URL = "https://api.imd.gov.in/api/v1"
    TIMEOUT_S = 10.0
    CACHE_TTL_S = 600.0    # 10 min: IMD's own portal guidance says "use client-side caching to optimise
                            # performance during peak weather events" -- current_wx is a synoptic observation
                            # that does not change faster than this, so re-fetching more often than every
                            # ~10 min would only hammer the API without adding real information.
    # Staleness of the OBSERVATION (age = now - observed_at_utc), D-36 P0-3. current_wx is a 3-hourly synoptic
    # report: within 3 h it is the latest report (FRESH); up to 6 h one report has been missed (AGING); beyond
    # that it is STALE. These are FloodNet's display thresholds, not an IMD specification.
    FRESH_MAX_MIN = 180
    AGING_MAX_MIN = 360

    def __init__(self, station_id: Optional[str] = None, api_key: Optional[str] = None,
                 api_token: Optional[str] = None, clock=None):
        self.station_id = station_id or os.environ.get(self.STATION_ID_ENV, self.DEFAULT_STATION_ID)
        self._api_key = api_key  # if None, read fresh from the environment on every call (see _api_key_now)
        self._api_token = api_token
        self._clock = clock or _utc_now   # -> aware UTC datetime; injectable so staleness is testable
        self._cache: Optional[tuple[float, RainfallScenario, RainfallSourceMeta]] = None

    def _api_key_now(self) -> Optional[str]:
        from .imd_auth import manager
        return self._api_key if self._api_key is not None else manager.api_key()

    def _api_token_now(self) -> Optional[str]:
        # Server-side token manager: hot-reloads a rotated token (file / .env / admin endpoint) with no restart.
        from .imd_auth import manager
        return self._api_token if self._api_token is not None else manager.token()

    def is_configured(self) -> bool:
        """True when the API key is present. The JWT (`IMD_API_TOKEN`) is a SECOND, separate credential the
        platform also requires; a key-only configuration is reported as configured here (so the operator sees
        the same "not configured" message they always did when nothing is set) but the request will be
        rejected by IMD with a specific 401 that `_auth_failure_reason` turns into a precise diagnostic --
        never into a fabricated rainfall value."""
        return bool(self._api_key_now())

    def clear_cache(self) -> None:
        self._cache = None

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        sid = scenario_id or LIVE_ID
        if sid != LIVE_ID:
            raise KeyError(f"IMDObservationProvider only serves {LIVE_ID!r}, got {sid!r}")
        key = self._api_key_now()
        if not key:
            raise ProviderUnavailable(
                f"{self.API_KEY_ENV} is not configured -- live IMD data is disabled, not faked. "
                f"Live access needs BOTH {self.API_KEY_ENV} (sent as X-API-Key) and {self.API_TOKEN_ENV} "
                f"(sent as Authorization: Bearer <JWT>) -- they are different credentials. "
                f"Set them in .env (see .env.example) to enable. Registration is not self-service: see "
                f"docs/LIVE_RAINFALL_AUDIT.md.")
        import time
        now = time.monotonic()
        if self._cache is not None and (now - self._cache[0]) < self.CACHE_TTL_S:
            return self._cache[1], self._cache[2]
        from .imd_auth import manager
        managed = self._api_token is None
        if managed:
            # A token KNOWN to be unusable (IMD rejected it, or its own exp has passed) is renewed first; if no
            # newer token can be obtained the request is not sent (it could only fail).
            token = manager.token_for_request()
            if token is None or not manager.usable():
                raise ProviderUnavailable(f"IMD live source unavailable: {manager.status().detail}")
        else:
            token = self._api_token_now()
        try:
            payload, retrieved_at = self._fetch_current_wx(key, token)
        except ProviderUnavailable as ex:
            if not managed:
                raise
            if "IMD returned 403" in str(ex):
                manager.report_auth_failure(token, status_code=403)       # key / IP: renewing a token cannot help
                raise
            if "IMD returned 401" not in str(ex):
                raise                                                    # timeout, 5xx, bad payload: not auth
            manager.report_auth_failure(token, status_code=401)
            renewed = manager.renew(token)                                # single-flight
            if renewed is None:
                raise ProviderUnavailable(f"{ex} {manager.refresh_detail()}") from None
            try:
                payload, retrieved_at = self._fetch_current_wx(key, renewed)   # retry the original request ONCE
            except ProviderUnavailable as ex2:
                if "IMD returned 401" in str(ex2):
                    manager.report_auth_failure(renewed, status_code=401)
                elif "IMD returned 403" in str(ex2):
                    manager.report_auth_failure(renewed, status_code=403)
                raise
        if managed:
            manager.report_success()
        scen, meta = self._normalize(payload, retrieved_at)
        self._cache = (now, scen, meta)
        return scen, meta

    def _auth_failure_reason(self, body: object) -> str:
        """Turn one of IMD's three distinct HTTP 401 bodies into a diagnostic that names the exact credential
        at fault. These bodies are precise signals -- the platform authenticates before routing, so they say
        which of the two required credentials the gateway objected to, not merely "unauthorised".

        Never includes a credential value in its output (this text reaches the HTTP client verbatim).
        """
        msg = ""
        if isinstance(body, dict):
            msg = str(body.get("error") or body.get("message") or "").strip().lower()
        elif isinstance(body, str):
            msg = body.strip().lower()
        if "api key" in msg:                       # {"error": "API key missing"}
            return (f"IMD returned 401 'API key missing': the X-API-Key header was absent or not recognised. "
                    f"Check {self.API_KEY_ENV} (this is the subscription key, not the JWT).")
        if "authorization header" in msg:          # {"error": "Authorization header missing or invalid"}
            return (f"IMD returned 401 'Authorization header missing or invalid': the "
                    f"'Authorization: Bearer <JWT>' header was absent or malformed. Set {self.API_TOKEN_ENV} "
                    f"to the JWT issued with your account -- it is a DIFFERENT value from {self.API_KEY_ENV}.")
        if "jwt" in msg or "expired" in msg:       # {"error": "Invalid or expired JWT token"}
            return (f"IMD returned 401 'Invalid or expired JWT token': the X-API-Key was accepted but "
                    f"{self.API_TOKEN_ENV} is wrong or has expired -- re-issue the token.")
        return "IMD returned HTTP 401 with an unrecognised authentication error body."

    def _forbidden_reason(self, body: object) -> str:
        """IMD's HTTP 403 bodies (observed live 2026-09-18): {"error": "IP address <ip> not authorized"} when the
        caller's public IP is not bound to the key, and {"error": "Invalid API key"} when the key itself is
        wrong. The caller's IP is deliberately not echoed (this text reaches the HTTP client)."""
        msg = str(body.get("error") or body.get("message") or "").strip().lower() if isinstance(body, dict) else ""
        if "ip address" in msg and "not authorized" in msg:
            return (f"IMD returned 403 'IP address not authorized': IMD does not accept this server's public IP "
                    f"for {self.API_KEY_ENV}. Either the key is registered for a different IP, or IMD has not "
                    f"activated the IP registration yet. Check the key's IP on the IMD portal (a new token "
                    f"will not fix this).")
        if "invalid api key" in msg:
            return f"IMD returned 403 'Invalid API key': check {self.API_KEY_ENV}."
        return "IMD returned HTTP 403 with an unrecognised error body."

    def _fetch_current_wx(self, key: str, token: Optional[str]) -> tuple[dict, datetime]:
        import httpx
        # AUTH (resolved -- see the class docstring): the platform requires BOTH an X-API-Key header (the
        # subscription key) and an Authorization: Bearer <JWT> header, carrying DIFFERENT values. The
        # `apikey` query parameter is not accepted and is no longer sent. If the JWT is missing we still
        # make the request rather than guessing a value: IMD's own 401 body then tells the operator exactly
        # which credential is at fault (see _auth_failure_reason), which is more useful -- and more honest --
        # than a locally invented diagnosis.
        header_name = os.environ.get(self.API_KEY_HEADER_ENV, "X-API-Key")
        headers = {header_name: key}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            log.warning("%s is not set: sending %s only. IMD requires an Authorization: Bearer <JWT> header "
                        "as well and will very likely reject this request with HTTP 401.",
                        self.API_TOKEN_ENV, header_name)
        url = f"{self.BASE_URL}/current_wx"
        status_code: Optional[int] = None
        auth_reason: Optional[str] = None
        try:
            with httpx.Client(headers=headers, timeout=self.TIMEOUT_S) as client:
                r = client.get(url, params={"id": self.station_id})
                status_code = r.status_code
                if status_code in (401, 403):
                    explain = self._auth_failure_reason if status_code == 401 else self._forbidden_reason
                    try:
                        auth_reason = explain(r.json())
                    except Exception:  # noqa: BLE001 -- body wasn't JSON; fall back to the generic text
                        auth_reason = explain(None)
                r.raise_for_status()
                data = r.json()
        except Exception as ex:  # noqa: BLE001
            # SECURITY: never put `ex` (or the request URL) into the message that reaches the HTTP client.
            # Credentials now travel in headers only (never in the URL), but httpx exception text embeds the
            # full request URL and this defensive sanitisation is kept regardless: full detail (safe:
            # server-side only) goes to the log, only a sanitised, credential-free summary is raised.
            log.warning("IMD current_wx request failed (station %s): %s", self.station_id, ex)
            if auth_reason:
                raise ProviderUnavailable(f"{auth_reason} (station {self.station_id})") from None
            reason = f"HTTP {status_code}" if status_code else type(ex).__name__
            raise ProviderUnavailable(f"IMD current_wx request failed (station {self.station_id}): {reason}") from None
        return data, datetime.now(timezone.utc)

    def _normalize(self, payload: dict, retrieved_at: datetime) -> tuple[RainfallScenario, RainfallSourceMeta]:
        # Response is a list with one object per IMD's documented sample shape, or a single object -- accept
        # either rather than assuming, but never guess a rainfall number if the field is genuinely absent.
        row = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(row, dict):
            raise ProviderUnavailable(f"IMD current_wx returned an unexpected shape: {type(payload).__name__}")
        raw = row.get("Last 24 hrs Rainfall")
        if raw is None:
            raise ProviderUnavailable("IMD current_wx response did not include a 'Last 24 hrs Rainfall' field")
        try:
            total_24h_mm = float(raw)
        except (TypeError, ValueError) as ex:
            raise ProviderUnavailable(f"IMD current_wx 'Last 24 hrs Rainfall' was not numeric: {raw!r}") from ex

        station_name = row.get("Station") or f"station {self.station_id}"
        # The reference names the field "Time of Observation" (UTC); the live API (verified 2026-09-18) sends it
        # as "Time" (e.g. "3" = the 03 UTC synoptic hour). Accept either; the raw value is kept, never reformatted.
        obs_clock = row.get("Time of Observation", row.get("Time", ""))
        obs_time = f"{row.get('Date of Observation', '')} {obs_clock}".strip()
        if obs_clock not in ("", None):
            obs_time += " UTC"
        obs_utc = _parse_imd_obs_utc(row.get("Date of Observation"), obs_clock)   # None if it does not parse

        mean_mm_h = max(0.0, total_24h_mm) / 24.0
        t_s = np.arange(0, HORIZON_S + 1, RAIN_DT_S, dtype=float)
        intensity_mm_h = np.full_like(t_s, mean_mm_h)

        note = (f"IMD current_wx, station {station_name} (id {self.station_id}): observed 24h cumulative "
                f"rainfall = {total_24h_mm:.1f} mm as of {obs_time or 'unknown time'}. "
                f"Forecast intensity is a PERSISTENCE assumption: {total_24h_mm:.1f} mm / 24h = "
                f"{mean_mm_h:.2f} mm/h, held constant for the 3h forecast window -- this is NOT a radar or "
                f"NWP nowcast; IMD publishes no quantitative sub-hourly nowcast product via this API (see "
                f"docs/LIVE_RAINFALL_AUDIT.md). District/station-level input only, applied uniformly over "
                f"the Hindmata/Dadar pilot grid. The 24-h mean rate is an antecedent indicator, NOT the current "
                f"rainfall rate.")
        prov = Provenance(Tag.ESTIMATED, "IMD API Management Platform (api.imd.gov.in), current_wx endpoint", note)
        scen = RainfallScenario(id=LIVE_ID, name=f"Live (IMD-observed, {station_name})", t_s=t_s,
                                 intensity_mm_h=intensity_mm_h, provenance=prov,
                                 description="Persistence forecast from IMD's live 24h observed rainfall total; "
                                             "see provenance note for the exact figures and the assumption used.")
        detail = {
            "source": "IMD", "station": station_name, "station_id": self.station_id,
            "retrieved_at": retrieved_at.isoformat(),
            "observed_at": obs_time or None,
            "observation_type": "24h cumulative rainfall (observed)",
            # The two halves of this input carry DIFFERENT tags; the scenario/meta tag (ESTIMATED) describes
            # the forecast driver the engine actually consumes. Labels only -- no value changes here.
            "source_label": "Live IMD observation + 3-hour persistence estimate",
            "observation_tag": Tag.REAL.value,
            "forecast_driver_tag": Tag.ESTIMATED.value,
            "observed_rainfall_mm": round(total_24h_mm, 2),
            "persistence_intensity_mm_h": round(mean_mm_h, 3),
            # Exact label the UI must display verbatim -- NEVER "nowcast"/"forecast"/"radar" on their own.
            "forecast_extension_label": "3-HOUR PERSISTENCE ESTIMATE",
            "forecast_extension_note": ("Not an official IMD nowcast or forecast -- a simple persistence "
                                        "assumption (last observed rate held constant) applied by FloodNet, "
                                        "documented in docs/LIVE_RAINFALL_AUDIT.md."),
            # D-36 P0-2: what this number is and is not, in one sentence the UI can show verbatim.
            "caveat": ("IMD 24-h cumulative rainfall at the station expressed as a 24-h mean rate -- an antecedent "
                       "indicator held constant as an ASSUMPTION; NOT a nowcast and NOT the current rainfall rate"),
            "timing": self._timing(obs_utc, retrieved_at),
        }
        meta = RainfallSourceMeta(source_type="live_observation", source_name=f"IMD {station_name}",
                                   timestamp=retrieved_at.isoformat(), forecast_horizon_min=HORIZON_S // 60,
                                   resolution_min=24 * 60, data_mode=Tag.ESTIMATED.value, provenance=prov,
                                   detail=detail)
        return scen, meta

    def _timing(self, obs_utc: Optional[datetime], retrieved_at: datetime) -> dict:
        """D-36 P0-3 timing block. Age is measured from the OBSERVATION time (not the fetch time). The validity
        window is the 3-h synoptic cycle (the report is the latest one until the next is due); the 24-h
        accumulation window's exact boundaries are not documented by IMD and are not asserted here."""
        age = round((self._clock() - obs_utc).total_seconds() / 60.0, 1) if obs_utc is not None else None
        valid_until = obs_utc + timedelta(minutes=self.FRESH_MAX_MIN) if obs_utc is not None else None
        return {
            "observed_at_utc": _iso_or_none(obs_utc),
            "valid_from_utc": _iso_or_none(obs_utc),
            "valid_until_utc": _iso_or_none(valid_until),
            "validity_basis": "latest 3-hourly synoptic report until the next one is due (FloodNet convention)",
            "received_at_utc": retrieved_at.isoformat(),
            "time_basis": "UTC documented",       # IMD API reference: observation time is UTC
            "units": {"observed_total": "mm", "observation_window": "24 h", "intensity": "mm/h"},
            "accumulation_interval": "24 h (window boundaries not documented by IMD) [UNVERIFIED]",
            "age_min": age,
            "staleness": _staleness(age, self.FRESH_MAX_MIN, self.AGING_MAX_MIN),
            "age_basis": "observed_at_utc",
            "fresh_max_min": self.FRESH_MAX_MIN, "aging_max_min": self.AGING_MAX_MIN,
        }


class ECMWFForecastProvider(RainfallProvider):
    """REAL rainfall FORECAST source: ECMWF numerical weather prediction, fetched from Open-Meteo's
    free ECMWF endpoint (api.open-meteo.com/v1/ecmwf). Introduced while IMD API access was pending; IMD
    access has worked since 2026-09-18 and this remains the forward-looking NWP source. See `docs/ECMWF_OPENMETEO_AUDIT.md` for the full source
    audit this class implements: endpoint, exact fields, temporal resolution, and limitations.

    WHAT THIS IS, AND WHAT IT IS NOT
    ---------------------------------
    Open-Meteo's `precipitation` hourly variable is ECMWF IFS model output: "Total precipitation (rain,
    showers, snow) sum of the preceding hour" (mm) -- an NWP FORECAST, not an observation and not a radar
    nowcast. It carries no relationship to IMD; it is never labelled IMD, radar, or nowcast anywhere in this
    codebase. Tagged `Tag.NWP` (see provenance.py), a tag that already existed in this codebase specifically
    for this purpose (see `ExternalNowcastProvider`'s docstring) and was, until now, unused.

    NORMALISATION (the only transformation applied, and it is a real one, not hidden): the engine needs
    `intensity_mm_h` at RAIN_DT_S (5-min) resolution; Open-Meteo returns hourly values. Since Open-Meteo's own
    hourly figure already represents the whole hour ("sum of the preceding hour"), each real hourly value is
    held CONSTANT across that hour's 5-min steps -- the exact same convention already used by
    `floodnet.data.scenarios.scenarios()['july2005']` for the (also hourly) Chitale/Santacruz replay ("mm in
    1 h == mm/h; hourly values held constant across each hour's 5-min steps"). No value is interpolated,
    invented, or estimated between real hourly points.

    The FloodNet 0-3h engine window needs 3 consecutive future hourly values; if Open-Meteo returns fewer
    (edge-of-forecast-window, malformed response, a null in the needed hours, etc.) this raises
    `ProviderUnavailable` rather than zero-filling the missing hour(s) -- an unfilled hour would silently read
    as "no rain", which is exactly the kind of fabrication this project forbids.
    """

    BASE_URL = "https://api.open-meteo.com/v1/ecmwf"
    TIMEOUT_S = 15.0
    CACHE_TTL_S = 600.0     # 10 min courtesy TTL -- mirrors IMDObservationProvider; Open-Meteo's own model
                            # update cadence is far coarser than this, so more frequent re-fetching would
                            # only hammer their (free, no-key) endpoint without adding new information.
    FORECAST_HOURS = 4      # request one hour of buffer beyond the 3 we need, in case the first returned
                            # hourly bucket is already the (partially elapsed) current hour.
    MIN_HOURS_REQUIRED = 3  # FloodNet's engine window is HORIZON_S = 3 h; anything less is reported as
                            # unavailable, never silently zero-filled.
    # D-36 P0-1: request IFS HRES (9 km, native hourly). Without `models=` /v1/ecmwf serves the 0.25 deg open-data
    # grid at 3-hourly steps (verified 2026-10-11, see docs/ECMWF_OPENMETEO_AUDIT.md).
    MODEL = "ecmwf_ifs"
    # Staleness of the RETRIEVAL (age = now - received_at_utc), D-36 P0-3: FRESH within 60 min; AGING until the
    # 3-h engine window it was fetched for has elapsed; STALE after. FloodNet display thresholds, not ECMWF's.
    FRESH_MAX_MIN = 60
    AGING_MAX_MIN = 180

    def __init__(self, lat: Optional[float] = None, lon: Optional[float] = None, clock=None):
        # No coordinates invented: default is the Hindmata/Dadar pilot bbox centroid, read from the single
        # source of truth for pilot geometry (config.PILOT_BBOX_LONLAT), not a re-typed literal.
        if lat is None or lon is None:
            west, south, east, north = config.PILOT_BBOX_LONLAT
            lon = lon if lon is not None else (west + east) / 2.0
            lat = lat if lat is not None else (south + north) / 2.0
        self.lat = lat
        self.lon = lon
        self._clock = clock or _utc_now   # -> aware UTC datetime; injectable so staleness is testable
        self._cache: Optional[tuple[float, RainfallScenario, RainfallSourceMeta]] = None

    def clear_cache(self) -> None:
        self._cache = None

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        sid = scenario_id or ECMWF_ID
        if sid != ECMWF_ID:
            raise KeyError(f"ECMWFForecastProvider only serves {ECMWF_ID!r}, got {sid!r}")
        import time
        now = time.monotonic()
        if self._cache is not None and (now - self._cache[0]) < self.CACHE_TTL_S:
            return self._cache[1], self._cache[2]
        payload, retrieved_at = self._fetch()
        scen, meta = self._normalize(payload, retrieved_at)
        self._cache = (now, scen, meta)
        return scen, meta

    def _fetch(self) -> tuple[dict, datetime]:
        import httpx
        params = {"latitude": round(self.lat, 5), "longitude": round(self.lon, 5),
                   "hourly": "precipitation", "forecast_hours": self.FORECAST_HOURS, "models": self.MODEL}
        try:
            with httpx.Client(timeout=self.TIMEOUT_S) as client:
                r = client.get(self.BASE_URL, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as ex:  # noqa: BLE001
            # No API key is used here (Open-Meteo's ECMWF endpoint is free for non-commercial use), so unlike
            # IMDObservationProvider there is no secret to leak -- but the message is still sanitised to a
            # short, generic summary rather than a raw exception string, for the same "never dump internals
            # to the HTTP client" discipline used everywhere else in this module.
            log.warning("Open-Meteo ECMWF request failed (lat=%s lon=%s): %s", self.lat, self.lon, ex)
            raise ProviderUnavailable(f"Open-Meteo ECMWF request failed: {type(ex).__name__}") from None
        return data, datetime.now(timezone.utc)

    def _normalize(self, payload: dict, retrieved_at: datetime) -> tuple[RainfallScenario, RainfallSourceMeta]:
        if not isinstance(payload, dict) or "hourly" not in payload:
            raise ProviderUnavailable("Open-Meteo ECMWF response did not include an 'hourly' block")
        hourly = payload["hourly"]
        if not isinstance(hourly, dict):
            raise ProviderUnavailable(f"Open-Meteo ECMWF 'hourly' block had an unexpected shape: {type(hourly).__name__}")
        times = hourly.get("time")
        precip = hourly.get("precipitation")
        if not times or not precip:
            raise ProviderUnavailable("Open-Meteo ECMWF response is missing 'time' or 'precipitation' in its hourly block")
        if len(times) != len(precip):
            raise ProviderUnavailable(
                f"Open-Meteo ECMWF 'time' ({len(times)}) and 'precipitation' ({len(precip)}) arrays have mismatched lengths")

        now_naive = retrieved_at.replace(tzinfo=None)
        parsed: list[tuple[str, datetime, object]] = []
        for t, v in zip(times, precip):
            try:
                dt = datetime.strptime(t, "%Y-%m-%dT%H:%M")
            except (TypeError, ValueError) as ex:
                raise ProviderUnavailable(f"Open-Meteo ECMWF returned an unparseable timestamp: {t!r}") from ex
            parsed.append((t, dt, v))

        future = [row for row in parsed if row[1] > now_naive]
        if not future:
            raise ProviderUnavailable("Open-Meteo ECMWF returned no forecast timestamps after the retrieval time")
        selected = future[: self.MIN_HOURS_REQUIRED]
        if len(selected) < self.MIN_HOURS_REQUIRED:
            raise ProviderUnavailable(
                f"Open-Meteo ECMWF returned only {len(selected)} usable forecast hour(s); "
                f"FloodNet's 0-3h window needs {self.MIN_HOURS_REQUIRED}")
        if any(v is None for _, _, v in selected):
            raise ProviderUnavailable("Open-Meteo ECMWF returned a null precipitation value within the needed 0-3h window")
        try:
            hourly_mm = [max(0.0, float(v)) for _, _, v in selected]
        except (TypeError, ValueError) as ex:
            raise ProviderUnavailable(f"Open-Meteo ECMWF precipitation value was not numeric: {ex}") from None

        t_s = np.arange(0, HORIZON_S + 1, RAIN_DT_S, dtype=float)
        tmin = t_s / 60.0
        intensity_mm_h = np.zeros_like(t_s)
        for h, mm in enumerate(hourly_mm):
            # mm accumulated in 1 h == mean mm/h over that hour (Open-Meteo docs: "sum of the preceding
            # hour"); held constant across the hour's 5-min steps, same convention as the july2005 replay.
            intensity_mm_h[(tmin >= 60 * h) & (tmin < 60 * (h + 1))] = mm

        timestamps = [t for t, _, _ in selected]
        hour_lines = "; ".join(f"{t} = {mm:.2f} mm" for t, mm in zip(timestamps, hourly_mm))
        grid_lat, grid_lon = payload.get("latitude"), payload.get("longitude")   # the model cell Open-Meteo used
        try:
            grid_lat, grid_lon = float(grid_lat), float(grid_lon)
            dist_km: Optional[float] = round(_haversine_km(self.lat, self.lon, grid_lat, grid_lon), 2)
        except (TypeError, ValueError):
            grid_lat = grid_lon = dist_km = None                                   # not echoed: not invented
        grid_txt = (f"model grid point {grid_lat:.4f} N {grid_lon:.4f} E, {dist_km:.1f} km from the requested point"
                    if dist_km is not None else "model grid point not reported in the response")
        note = (f"ECMWF IFS HRES 9 km hourly via Open-Meteo — NWP forecast, not a nowcast, not an IMD product "
                f"(api.open-meteo.com/v1/ecmwf, models={self.MODEL}), requested at "
                f"lat={self.lat:.4f} lon={self.lon:.4f} (Hindmata/Dadar pilot bbox centroid); {grid_txt}. Retrieved "
                f"{retrieved_at.isoformat()}. Hourly precipitation (mm, sum of the preceding hour = mean "
                f"mm/h over that hour): {hour_lines}. This is a numerical-weather-prediction FORECAST, not a "
                f"radar nowcast and not an IMD product, "
                f"see docs/ECMWF_OPENMETEO_AUDIT.md. Hourly values held constant across each hour's 5-min "
                f"steps (same convention as the 26 July 2005 historical replay).")
        prov = Provenance(Tag.NWP, f"Open-Meteo (https://open-meteo.com), ECMWF IFS HRES 9 km forecast model "
                                   f"(models={self.MODEL})", note)
        scen = RainfallScenario(id=ECMWF_ID, name="ECMWF NWP forecast (Open-Meteo)", t_s=t_s,
                                 intensity_mm_h=intensity_mm_h, provenance=prov,
                                 description="Next 3 forecast hours of ECMWF precipitation via Open-Meteo, "
                                             "hourly values held constant across 5-min steps. NWP FORECAST -- "
                                             "not a radar nowcast, not an official IMD product.")
        detail = {
            "source": "Open-Meteo", "model": "ECMWF", "data_type": "Numerical Weather Prediction",
            "classification": "FORECAST", "retrieved_at": retrieved_at.isoformat(),
            "forecast_timestamps": timestamps, "precipitation_mm": [round(mm, 3) for mm in hourly_mm],
            "location_lonlat": [round(self.lon, 5), round(self.lat, 5)],
            "location_source": "Hindmata/Dadar pilot bbox centroid (config.PILOT_BBOX_LONLAT)",
            "endpoint": self.BASE_URL,
            "models": self.MODEL, "model_description": "ECMWF IFS HRES 9 km, native hourly (via Open-Meteo)",
            "grid_point_lat": grid_lat, "grid_point_lon": grid_lon, "distance_km_to_pilot": dist_km,
            "timing": self._timing(selected[0][1], selected[-1][1], retrieved_at),
        }
        meta = RainfallSourceMeta(source_type="ecmwf_forecast", source_name="ECMWF NWP (Open-Meteo)",
                                   timestamp=retrieved_at.isoformat(), forecast_horizon_min=HORIZON_S // 60,
                                   resolution_min=60, data_mode=Tag.NWP.value, provenance=prov, detail=detail)
        return scen, meta

    def _timing(self, first_stamp: datetime, last_stamp: datetime, retrieved_at: datetime) -> dict:
        """D-36 P0-3 timing block. Open-Meteo returns times in GMT by default (timezone not requested) and each
        hourly stamp is the END of a "sum of the preceding hour" accumulation, so the selected values cover
        [first stamp - 1 h, last stamp]. Open-Meteo returns no model run (issue) time, so issued_at_utc is None
        rather than guessed. Age is measured from the RETRIEVAL time."""
        first = first_stamp.replace(tzinfo=timezone.utc)
        last = last_stamp.replace(tzinfo=timezone.utc)
        age = round((self._clock() - retrieved_at).total_seconds() / 60.0, 1)
        return {
            "issued_at_utc": None,                 # model run time is not in the Open-Meteo response
            "valid_from_utc": (first - timedelta(hours=1)).isoformat(),
            "valid_until_utc": last.isoformat(),
            "validity_basis": "accumulation window of the selected hourly values (stamp = end of preceding hour)",
            "received_at_utc": retrieved_at.isoformat(),
            "time_basis": "UTC documented",       # Open-Meteo API default timezone=GMT
            "units": {"precipitation": "mm per hour", "intensity": "mm/h"},
            "accumulation_interval": "1 h (sum of the preceding hour)",
            "age_min": age,
            "staleness": _staleness(age, self.FRESH_MAX_MIN, self.AGING_MAX_MIN),
            "age_basis": "received_at_utc",
            "fresh_max_min": self.FRESH_MAX_MIN, "aging_max_min": self.AGING_MAX_MIN,
        }


class IMDRadarNowcastProvider(RainfallProvider):
    """Integration boundary for IMD Doppler Weather Radar (DWR Mumbai) -- WIRED, DOCUMENTED, AND DISABLED
    BY DEFAULT. It never produces a rainfall value; `.get()` always raises `ProviderUnavailable`.

    This class exists so that (a) radar has one honest, named place to attach if access is ever granted, and
    (b) `/api/status` can say "ACCESS PENDING" out loud instead of radar being silently absent. It fetches
    nothing: no HTTP client is imported, no image is downloaded, no pixel is decoded, no capture job exists.

    WHY IT IS DISABLED (decision D-14, docs/DECISIONS.md; evidence docs/LIVE_RAINFALL_AUDIT.md §8b/§8c)
    -----------------------------------------------------------------------------------------------------
    Two independent investigations (a specialist audit and an adversarial re-check, both against official IMD
    sources) concluded that radar is BLOCKED for defensible quantitative use:

    1. NO DOCUMENTED ENDPOINT. IMD's API reference indexes 28 APIs, but the document body ends at §20.
       "Radar Image" (`#api-25`) is a dead anchor: no URL, no parameters, no response schema is published
       anywhere. There is nothing to implement against.
    2. EXISTENCE CANNOT EVEN BE PROBED. The platform evaluates auth BEFORE routing (proven with a
       nonsense-path control returning a byte-identical 401), so without a credential one cannot tell a real
       endpoint from a typo. Auth needs two headers (X-API-Key and Authorization: Bearer <JWT>).
    3. THE PUBLIC IMAGERY IS NOT A QUANTITATIVE INPUT. `mausam.imd.gov.in/Radar/sri_mum.gif` genuinely is
       Surface Rainfall Intensity in mm/hr with its Z-R relation printed in-band (Z = 152 * R^1.5) -- but its
       top colour bin is OPEN-ENDED at ">100 mm/h", below FloodNet's own `cloudburst` (120 mm/h) and
       `july2005` (190.3 mm/h) intensities, so precisely the events this project exists to model would be
       clipped to an unknown value. Add ~+-3.33 mm/h bin quantisation, ~12.5% of the pilot footprint hidden
       under the drawn coastline, 30-40 min latency, and a rain rate inferred at 2 km altitude. Decoding a
       rendered picture is a lossy reconstruction of a visualisation, not an observation, and must never
       carry an observation-class provenance tag.
    4. NO ARCHIVE, SO NO HINDCAST. Directory listings return 403 and there are ~3 Wayback captures in six
       years; a historical radar-driven validation run is impossible from free sources.
    5. LICENCE UNRESOLVED. `copyRightPolicy.php`/`termscondition.php` 404 while `disclaimer.php` asserts IMD
       copyright with no grant; the official supply route (`radarapi.imd.gov.in`, Radar Division) requires an
       account, a written data request and payment. No harvesting has been started, deliberately.
    6. ~~THE ENGINE COULD NOT INGEST IT ANYWAY (decision D-15).~~ **NO LONGER TRUE as of 2026-09-14.**
       `RainfallScenario` now carries an optional `intensity_field_mm_h` `[T, ny, nx]` + `field_grid`,
       `runoff_fn` accepts a per-cell field, and `engine.run_simulation` routes it through the 2D surface
       model; `floodnet.rainfall.gridded` handles reprojection/resampling onto the model grid. A gridded
       radar field now has somewhere to go. **The remaining blockers are 1-5 above -- all of them data
       access and licensing, none of them architecture.** See D-15 (resolved) and D-16 in docs/DECISIONS.md.

    WHAT WOULD BE REQUIRED TO ENABLE IT (all of these, not any of them)
    -------------------------------------------------------------------
    a. A written, published (or licensed) radar data endpoint from IMD with a real response schema -- product
       type, units, grid definition, timestamps -- not a rendered image.
    b. Credentials for it: `IMD_API_KEY` (X-API-Key) AND `IMD_API_TOKEN` (Bearer JWT).
    c. A resolved licence permitting programmatic retrieval and retention for this use.
    d. ~~A `RainfallScenario` that can carry a gridded field (D-15)~~ -- **DONE.** Build the scenario with
       `floodnet.rainfall.gridded.scenario_from_gridded(...)`, which resamples the radar grid onto the model
       grid and records the effective resolution honestly (it will state plainly if the source cannot resolve
       structure inside the pilot). `IMERGSatelliteProvider` is a worked example of this exact shape.
    e. Then, and only then: set `IMD_RADAR_ENABLED=1` and implement `_fetch()` in place of the raise below,
       tagging output `Tag.REAL` for a true QPE product or `Tag.ESTIMATED` for anything derived.

    FAILURE BEHAVIOUR: raises `ProviderUnavailable` -- the same contract the live/ECMWF providers use, which
    callers must treat as "tell the user this source is unavailable". It never falls through to another
    provider and never returns a substituted or synthesised series under a radar label.
    """

    ENABLED_ENV = "IMD_RADAR_ENABLED"
    API_KEY_ENV = IMDObservationProvider.API_KEY_ENV      # same platform credentials as the live provider
    API_TOKEN_ENV = IMDObservationProvider.API_TOKEN_ENV
    AUDIT_DOC = "docs/LIVE_RAINFALL_AUDIT.md"
    _TRUTHY = ("1", "true", "yes", "on", "enabled")

    # The single sentence a status consumer sees. Deliberately leads with ACCESS PENDING and points at the
    # evidence, so an operator can check the claim rather than take this class's word for it.
    STATUS_REASON = (
        "ACCESS PENDING -- IMD Doppler Weather Radar is not connected and is disabled by default "
        "(IMD_RADAR_ENABLED unset). IMD publishes no documented radar data endpoint (the API reference's "
        "'Radar Image' entry is a dead anchor; the document body ends at section 20), and the public radar "
        "imagery is a rendered picture whose top intensity bin is open-ended at >100 mm/h -- below this "
        "project's own cloudburst (120 mm/h) and 2005 (190.3 mm/h) intensities -- so it is not usable as a "
        "quantitative input. No radar value is estimated, decoded or substituted from another source. "
        "Evidence: docs/LIVE_RAINFALL_AUDIT.md sections 8b/8c; decision D-14 in docs/DECISIONS.md.")

    def is_enabled(self) -> bool:
        """True only if an operator has explicitly set the enable flag. Default (unset) is False: this
        provider cannot activate by accident, e.g. because IMD credentials happened to be configured for the
        separate `live` observation path."""
        return os.environ.get(self.ENABLED_ENV, "").strip().lower() in self._TRUTHY

    def is_configured(self) -> bool:
        """Both platform credentials present. Note this is NOT sufficient to enable the provider -- the
        explicit flag is required as well (see `is_enabled`)."""
        return bool(os.environ.get(self.API_KEY_ENV)) and bool(os.environ.get(self.API_TOKEN_ENV))

    def unavailable_reason(self) -> str:
        """The exact reason string reported by `provider_status()` and raised by `.get()`."""
        if not self.is_enabled():
            return self.STATUS_REASON
        # Flag set: report what is still missing, and still refuse -- setting a flag cannot conjure an
        # endpoint that IMD does not publish.
        missing = []
        if not os.environ.get(self.API_KEY_ENV):
            missing.append(self.API_KEY_ENV)
        if not os.environ.get(self.API_TOKEN_ENV):
            missing.append(self.API_TOKEN_ENV)
        creds = f" Missing credentials: {', '.join(missing)}." if missing else ""
        return (f"ACCESS PENDING -- {self.ENABLED_ENV} is set, but no radar adapter is implemented and none "
                f"can be: IMD publishes no documented radar data endpoint to call, and the public radar "
                f"imagery is a rendered picture, not a quantitative rainfall field (see {self.AUDIT_DOC} "
                f"sections 8b/8c, decision D-14).{creds} Nothing is fabricated or substituted; implement "
                f"_fetch() only once a real, licensed, documented endpoint exists.")

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        sid = scenario_id or RADAR_ID
        if sid != RADAR_ID:
            raise KeyError(f"IMDRadarNowcastProvider only serves {RADAR_ID!r}, got {sid!r}")
        # Always. There is no code path in this class that returns a RainfallScenario, by design.
        raise ProviderUnavailable(self.unavailable_reason())


class ExternalNowcastProvider(RainfallProvider):
    """DEPRECATED -- superseded by `IMDRadarNowcastProvider`, which names the actual source, documents why it
    is off, and reports itself honestly through `provider_status()`.

    Kept only so existing imports (`floodnet.rainfall.__init__`, tests) keep working; still fully inert. Do
    not wire anything new to this class -- a new source deserves its own named provider, as
    `IMDRadarNowcastProvider` and `ECMWFForecastProvider` are.

    Calling `.get()` raises `NotImplementedError` rather than returning fabricated numbers.
    """

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        raise NotImplementedError(
            "ExternalNowcastProvider is a deprecated, inert interface stub (see IMDRadarNowcastProvider for "
            "the radar integration boundary). No adapter is connected in this build.")


class IMERGSatelliteProvider(RainfallProvider):
    """REAL, CC0-licensed, GRIDDED satellite precipitation for Mumbai: NASA/JAXA GPM **IMERG Early Run V07**.

    This is the first provider in FloodNet that produces a genuine `[T, ny, nx]` spatial rainfall field
    (via `floodnet.rainfall.gridded`) rather than one number per timestep. It exists because SR-01 asks for
    gridded, high-resolution rainfall, and because the engine can finally accept a field (decision D-15 was
    the blocker; the contract now supports it).

    ============================================================================================
    WHAT THIS IS -- AND, PRECISELY, WHAT IT IS NOT
    ============================================================================================
    **It is NOT ground radar, and it is NOT a radar nowcast. Do not label it as either, anywhere.**

    IMERG's half-hourly 0.1 deg grid is built from a constellation of **passive-microwave** sensors plus
    **geostationary infrared**, morphed in time. A spaceborne radar (the GPM Core Observatory's Dual-frequency
    Precipitation Radar) enters only *indirectly*: the PMW estimates are intercalibrated to the GPM Combined
    Radar-Radiometer Analysis (CORRA) product, which is where DPR contributes. The pixels you download are
    not radar measurements of Mumbai.

    The only honest one-line description, and the one used in this class's provenance note, is:
    *"satellite passive-microwave/infrared precipitation, intercalibrated against a spaceborne-radar
    (GPM DPR/CORRA) reference"*. Anything shorter that contains the word "radar" unqualified is misleading.

    ============================================================================================
    THE RESOLUTION LIMITATION -- STATED UP FRONT, NOT BURIED
    ============================================================================================
    IMERG's cell is 0.1 deg. At the pilot's latitude (~19.02 N) that is about **11.1 km north-south and
    ~10.5 km east-west, i.e. ~117 km2**. The Hindmata/Dadar pilot box (72.835-72.855 E, 19.010-19.030 N) is
    about **2.4 x 2.55 km ~ 6 km2** and falls **entirely inside the single IMERG cell spanning
    72.8-72.9 E / 19.0-19.1 N** -- it does not even straddle a cell boundary.

    **Therefore this provider supplies real, correctly-scaled, time-varying rainfall with a verifiable
    licence and provenance, but ZERO spatial variation across the pilot.** Going from the uniform scalar
    providers to IMERG is an upgrade in *provenance and temporal realism*, not in spatial detail. The field
    this provider returns is genuinely a field -- and it is genuinely (near-)constant in space at pilot
    scale. `gridded.scenario_from_gridded()` detects this automatically and writes it into the scenario's
    provenance note; that note must not be edited out downstream.

    A second consequence, equally important: at 30-minute, ~11 km resolution IMERG cannot represent the
    120 mm/h and 190 mm/h convective cores this model is built around -- those are sub-grid and sub-half-hour.
    The synthetic design storms remain the right tool for stress-testing; IMERG is the observed/baseline mode.

    ============================================================================================
    ACCESS
    ============================================================================================
    Product      GPM_3IMERGHHE.07 (Early Run), 0.1 deg, 30 min, mm/hr, global.
    Host         https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07/
    Licence      **CC0** -- NASA's data-use policy: data from a NASA-led mission are licensed CC0 unless
                 marked otherwise. This is the only rainfall source wired here with a verified open licence.
    Latency      ~4 h for the Early Run (Late ~12-14 h, Final ~3 months).
    Credential   A free **Earthdata Login** is required. This provider reads a bearer token from
                 `EARTHDATA_TOKEN`. With no token it raises `ProviderUnavailable` -- it never substitutes,
                 estimates, or falls back to another source.
    Format       HDF5, so parsing needs `h5py`, which is an OPTIONAL dependency (`pip install -e .[imerg]`).
                 Without it this provider raises `ProviderUnavailable` naming the missing package.

    ============================================================================================
    VERIFICATION STATUS -- READ BEFORE TRUSTING THE NETWORK PATH
    ============================================================================================
    The product, resolution, cadence, licence, host and authentication scheme above were verified against
    NASA's own pages. **The request path below (directory layout and the IMERG half-hourly filename pattern)
    follows GES DISC's documented convention but has NOT been executed against the live service, because no
    Earthdata credential was available in this environment.** It is therefore in exactly the same honest
    state `IMDObservationProvider` was in before 2026-09-18: implemented and wired, never executed against
    the real API (the IMD provider has since been verified live; this one has not). The first
    authenticated run must confirm the filename pattern; `ProviderUnavailable` (not a fabricated value) is
    the failure mode if it is wrong.
    """

    TOKEN_ENV = "EARTHDATA_TOKEN"
    BASE_URL = "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07"
    # IMERG half-hourly granule naming, per GES DISC convention. UNVERIFIED against the live service.
    FILENAME = ("3B-HHR-E.MS.MRG.3IMERG.{ymd}-S{start}-E{end}.{minutes:04d}.V07B.HDF5")
    PRECIP_DATASET = "/Grid/precipitation"      # [time, lon, lat] in IMERG V07 HDF5
    LAT_DATASET = "/Grid/lat"
    LON_DATASET = "/Grid/lon"
    TIMEOUT_S = 60.0

    def __init__(self, token: Optional[str] = None):
        self._token = token

    @property
    def token(self) -> Optional[str]:
        return self._token if self._token is not None else os.environ.get(self.TOKEN_ENV)

    def is_configured(self) -> bool:
        return bool(self.token)

    def unavailable_reason(self) -> Optional[str]:
        if not self.is_configured():
            return (f"NASA Earthdata credential not configured: set {self.TOKEN_ENV} to a bearer token from "
                    "https://urs.earthdata.nasa.gov (free registration). GPM IMERG itself is CC0-licensed; "
                    "the login is an access-tracking requirement, not a paywall.")
        try:
            import h5py  # noqa: F401
        except ImportError:
            return ("GPM IMERG granules are HDF5; the optional 'h5py' dependency is not installed. "
                    "Install it with: pip install -e .[imerg]")
        return None

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        reason = self.unavailable_reason()
        if reason:
            raise ProviderUnavailable(reason)
        grid = kwargs.get("grid")
        if grid is None:
            raise ProviderUnavailable(
                "IMERGSatelliteProvider needs the model grid to resample onto; pass grid=<Terrain.grid>. "
                "Resampling is the provider's responsibility -- the engine refuses a mismatched field.")
        horizon_s = int(kwargs.get("horizon_s", HORIZON_S))
        fields, lons, lats, stamps = self._fetch_fields(horizon_s)

        from .gridded import scenario_from_gridded, describe_effective_resolution
        t_s = np.arange(0, len(fields) * 1800, 1800, dtype=float)[: len(fields)]
        eff = describe_effective_resolution(lons, lats, grid)
        prov = Provenance(
            Tag.REAL,
            "NASA/JAXA GPM IMERG Early Run V07 (GPM_3IMERGHHE.07), GES DISC; licence CC0",
            "Satellite passive-microwave/infrared precipitation, intercalibrated against a spaceborne-radar "
            "(GPM DPR via the CORRA product) reference. NOT ground radar and NOT a radar nowcast. "
            f"Native grid 0.1 deg (~{eff['source_pixel_m'][0]/1000:.1f} x {eff['source_pixel_m'][1]/1000:.1f} km "
            f"at this latitude), 30-min cadence, mm/hr. Retrieved {stamps[0] if stamps else 'n/a'}.")
        scen = scenario_from_gridded(
            IMERG_ID, "GPM IMERG satellite precipitation (Early Run V07)", t_s, fields,
            src_lons=lons, src_lats=lats, grid=grid, provenance=prov,
            description=("Real, CC0-licensed gridded satellite precipitation over Mumbai. Not radar. At 0.1 deg "
                         "the whole pilot falls inside one source cell, so this carries no intra-pilot spatial "
                         "variation -- see the provenance note."))
        meta = RainfallSourceMeta(
            source_type="satellite_qpe", source_name="GPM IMERG Early V07 (NASA/JAXA)",
            timestamp=datetime.now(timezone.utc).isoformat(), forecast_horizon_min=horizon_s // 60,
            resolution_min=30, data_mode="REAL", provenance=scen.provenance,
            detail={"product": "GPM_3IMERGHHE.07", "licence": "CC0", "native_resolution_deg": 0.1,
                    "cadence_min": 30, "is_ground_radar": False, "is_radar_nowcast": False,
                    "radar_relationship": "PMW estimates intercalibrated to GPM CORRA (DPR) -- indirect only",
                    "spatial_field": True, "resolves_within_pilot": eff["resolves_within_pilot"],
                    "pilot_spans_source_pixels": eff["pilot_spans_source_pixels"],
                    "granule_timestamps": stamps})
        return scen, meta

    def _fetch_fields(self, horizon_s: int):
        """Download and parse the IMERG granules covering the last `horizon_s` seconds.

        UNVERIFIED network path -- see the class docstring. Any failure raises ProviderUnavailable; this
        method never returns a synthesised or partially-filled field."""
        import httpx, h5py, io
        from ..config import PILOT_BBOX_LONLAT

        n = max(1, horizon_s // 1800)
        now = datetime.now(timezone.utc)
        # IMERG half-hour granules are stamped on :00 and :30 boundaries; walk backwards from the most
        # recent complete one, allowing for the ~4 h Early-Run latency.
        anchor = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        anchor = anchor.fromtimestamp(anchor.timestamp() - 4 * 3600, tz=timezone.utc)
        w, s, e, nn = PILOT_BBOX_LONLAT
        fields, stamps = [], []
        lons = lats = None
        headers = {"Authorization": f"Bearer {self.token}"}
        for k in range(n):
            t = anchor.fromtimestamp(anchor.timestamp() - (n - 1 - k) * 1800, tz=timezone.utc)
            minutes = t.hour * 60 + t.minute
            end_t = t.fromtimestamp(t.timestamp() + 1799, tz=timezone.utc)
            fname = self.FILENAME.format(ymd=t.strftime("%Y%m%d"), start=t.strftime("%H%M%S"),
                                         end=end_t.strftime("%H%M%S"), minutes=minutes)
            url = f"{self.BASE_URL}/{t.strftime('%Y')}/{t.strftime('%j')}/{fname}"
            try:
                with httpx.Client(timeout=self.TIMEOUT_S, follow_redirects=True, headers=headers) as c:
                    r = c.get(url)
                    r.raise_for_status()
                    raw = r.content
            except Exception as ex:
                raise ProviderUnavailable(
                    f"GPM IMERG granule request failed ({type(ex).__name__}) for {fname}. Note the request "
                    "path is UNVERIFIED against the live service -- confirm the GES DISC directory/filename "
                    "convention and that the Earthdata token is valid.") from None
            try:
                with h5py.File(io.BytesIO(raw), "r") as h:
                    if lons is None:
                        lons = np.asarray(h[self.LON_DATASET][:], dtype=float)
                        lats = np.asarray(h[self.LAT_DATASET][:], dtype=float)
                        li = np.where((lons >= w - 0.3) & (lons <= e + 0.3))[0]
                        la = np.where((lats >= s - 0.3) & (lats <= nn + 0.3))[0]
                        if li.size == 0 or la.size == 0:
                            raise ProviderUnavailable("GPM IMERG grid does not cover the pilot bbox")
                        self._li, self._la = li, la
                        lons, lats = lons[li], lats[la]
                    p = h[self.PRECIP_DATASET]
                    # IMERG V07 stores precipitation as [time, lon, lat]; transpose to [lat, lon]
                    arr = np.asarray(p[0, self._li[0]:self._li[-1] + 1, self._la[0]:self._la[-1] + 1],
                                     dtype=float).T
                    arr = np.where(arr < 0, 0.0, arr)     # IMERG uses negative fill values for no-data
            except ProviderUnavailable:
                raise
            except Exception as ex:
                raise ProviderUnavailable(f"GPM IMERG granule parse failed for {fname}: {type(ex).__name__}") from None
            fields.append(arr)
            stamps.append(t.isoformat())
        return fields, lons, lats, stamps


class SyntheticSpatialProvider(RainfallProvider):
    """SYNTHETIC spatially-varying storm -- the test/demo vehicle for the gridded-rainfall pathway.

    Produces a genuine `[T, ny, nx]` field (a Gaussian rain cell tracking across the pilot) so that the full
    spatial chain -- field -> per-cell runoff -> 2D surface routing -> drainage coupling -> street depth --
    can be exercised, tested and demonstrated without waiting on a data licence.

    **It is invented. It is not radar, not observed, and not a nowcast.** Its provenance is tagged SYNTHETIC
    and says so explicitly, exactly like the existing `moderate`/`heavy`/`cloudburst` design storms. The only
    difference from those is that this one varies in SPACE as well as time. Any UI, API or document that
    presents it as observed rainfall of any kind is wrong.
    """

    SCENARIO_ID = SPATIAL_SYNTHETIC_ID

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        grid = kwargs.get("grid")
        if grid is None:
            raise ProviderUnavailable(
                "SyntheticSpatialProvider needs the model grid to build a field on; pass grid=<Terrain.grid>.")
        horizon_s = int(kwargs.get("horizon_s", HORIZON_S))
        from .gridded import synthetic_moving_storm
        scen = synthetic_moving_storm(grid, horizon_s=horizon_s, dt_s=RAIN_DT_S)
        meta = RainfallSourceMeta(
            source_type="scenario", source_name=scen.name, timestamp="N/A (invented spatial test storm)",
            forecast_horizon_min=horizon_s // 60, resolution_min=RAIN_DT_S // 60, data_mode="SYNTHETIC",
            provenance=scen.provenance,
            detail={"spatial_field": True, "field_shape": list(scen.intensity_field_mm_h.shape),
                    "peak_cell_mm_h": float(np.max(scen.intensity_field_mm_h)),
                    "is_radar": False, "is_observed": False,
                    "warning": "INVENTED spatial field for testing the gridded pathway; never present as observed"})
        return scen, meta


class IMDVeravaliSRIImageProvider(RainfallProvider):
    """RADAR_IMAGE_DERIVED_ESTIMATE from IMD's public Mumbai-Veravali DWR Surface Rainfall Intensity image.

    Fetches https://mausam.imd.gov.in/Radar/sri_vrv.gif directly (no page scraping), decodes it with
    `imd_sri_image` (legend bins, conservative echo mask, tick-fitted georeference -- see that module), and
    resamples the pilot window onto the model grid, producing a genuine [T, ny, nx] field.

    TIME -- a source-aware horizon, never silently blended (each period is recorded in
    `detail["forecast_segments"]`):
      t = 0          the decoded observed frame (RADAR_IMAGE_DERIVED_ESTIMATE).
      0 < t <= 30'   "Experimental radar-image nowcast" -- ONLY when FloodNet's own frame history holds two
                     distinct, recent frames and `radar_nowcast.estimate_motion` accepts them; otherwise the
                     observed frame is simply held (persistence). IMD publishes one frame at a time, so history
                     accrues only from frames this provider has itself fetched.
      30' < t        ECMWF NWP (uniform over the pilot, aligned by wall-clock to the frame's observation time)
                     when the caller supplies it; otherwise persistence of the last radar-based field.
    None of this is an IMD nowcast.

    FAILURE: any decode doubt, a stale image (older than MAX_AGE_MIN), or too little valid data over the pilot
    raises ProviderUnavailable. It never substitutes another source or synthetic rain under this label.
    """

    SCENARIO_ID = IMD_SRI_ID
    TIMEOUT_S = 30.0
    CACHE_TTL_S = 300.0          # courtesy: never re-download the ~1.5 MB image more than every 5 min
    MAX_AGE_MIN = 90.0           # one observed publication latency was ~39 min; older frames are refused
    MIN_WINDOW_VALID_PCT = 50.0  # below this the pilot is mostly text/lines/unknown pixels -> refuse
    USER_AGENT = "FloodNet-research/1.0 (academic prototype; low-frequency single-image fetch)"

    def __init__(self, frame_dir=None):
        self._cache: Optional[tuple[float, bytes, str]] = None     # (monotonic time, bytes, retrieved_at)
        self._frame_dir = frame_dir
        self._store = None

    @property
    def store(self):
        if self._store is None:
            from .radar_nowcast import FrameStore
            self._store = FrameStore(self._frame_dir)
        return self._store

    def clear_cache(self) -> None:
        self._cache = None

    def _ecmwf_tail(self):
        """(intensity_mm_h[T], retrieved_at) from the shared ECMWF provider, or None if it is unavailable --
        in which case the tail falls back to persistence and says so."""
        try:
            scen, meta = _ecmwf_provider.get(ECMWF_ID)
            return scen.t_s, scen.intensity_mm_h, datetime.fromisoformat(meta.detail["retrieved_at"])
        except Exception as ex:  # noqa: BLE001 -- the tail is optional; never fail a radar run over it
            log.info("ECMWF tail unavailable for the radar-derived run (%s); using persistence", type(ex).__name__)
            return None

    def _fetch(self) -> tuple[bytes, str]:
        import time
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < self.CACHE_TTL_S:
            return self._cache[1], self._cache[2]
        import httpx
        from .imd_sri_image import SRI_URL
        try:
            with httpx.Client(timeout=self.TIMEOUT_S, headers={"User-Agent": self.USER_AGENT}) as client:
                r = client.get(SRI_URL)
                r.raise_for_status()
                data = r.content
        except Exception as ex:  # noqa: BLE001
            log.warning("IMD SRI image fetch failed: %s", ex)
            raise ProviderUnavailable(
                f"IMD Mumbai-Veravali SRI image could not be fetched ({type(ex).__name__})") from None
        retrieved = datetime.now(timezone.utc).isoformat()
        self._cache = (now, data, retrieved)
        return data, retrieved

    def get(self, scenario_id: Optional[str] = None, **kwargs) -> tuple[RainfallScenario, RainfallSourceMeta]:
        sid = scenario_id or IMD_SRI_ID
        if sid != IMD_SRI_ID:
            raise KeyError(f"IMDVeravaliSRIImageProvider only serves {IMD_SRI_ID!r}, got {sid!r}")
        grid = kwargs.get("grid")
        if grid is None:
            raise ProviderUnavailable("IMDVeravaliSRIImageProvider needs grid=<Terrain.grid> to resample onto.")
        horizon_s = int(kwargs.get("horizon_s", HORIZON_S))
        data, retrieved = self._fetch()
        return self.from_image(data, grid, horizon_s, retrieved_at=retrieved,
                               now=kwargs.get("now") or datetime.now(timezone.utc),
                               store=self.store, tail=self._ecmwf_tail())

    def from_image(self, data: bytes, grid, horizon_s: int, retrieved_at: str, now: datetime,
                   store=None, tail=None) -> tuple[RainfallScenario, RainfallSourceMeta]:
        from . import imd_sri_image as sri
        from . import radar_nowcast as rn
        from .gridded import scenario_from_gridded
        try:
            dec = sri.decode_sri(data)
        except sri.SRIDecodeError as ex:
            raise ProviderUnavailable(f"IMD SRI image not decodable without guessing: {ex}") from None
        age_min = (now - dec.observed_at_utc).total_seconds() / 60.0
        if age_min > self.MAX_AGE_MIN:
            raise ProviderUnavailable(f"IMD SRI image is {age_min:.0f} min old (observed "
                                      f"{dec.observed_at_utc:%Y-%m-%d %H:%M} UTC); refusing a stale radar frame")
        if age_min < -10:
            raise ProviderUnavailable("IMD SRI image timestamp is in the future; refusing an unverifiable frame")
        field2d, _, win = sri.to_model_grid(dec, grid, config.PILOT_BBOX_LONLAT)
        if win["window_valid_pct"] < self.MIN_WINDOW_VALID_PCT:
            raise ProviderUnavailable(f"only {win['window_valid_pct']:.0f}% of the SRI pixels over the pilot are "
                                      "decodable (text/lines/ambiguous colours); refusing to fill the rest")
        t_s = np.arange(0, horizon_s + 1, RAIN_DT_S, dtype=float)
        obs = f"{dec.observed_at_utc:%Y-%m-%d %H:%M:%S} UTC"
        g = dec.georef

        # ---- source-aware horizon: observed frame -> (optional) advection nowcast -> ECMWF / persistence
        frame = rn.frame_from_decoded(dec, config.PILOT_BBOX_LONLAT)
        motion = None
        if store is not None:
            store.add(frame)                                 # history is always kept; it costs nothing
            pair = store.latest_pair()
            # The advection nowcast is OFF unless FLOODNET_RADAR_NOWCAST=1: IMD's feed has not yet supplied two
            # reliable consecutive scans to verify it against (docs/RADAR_NOWCAST_STATUS.md). Default path =
            # observed frame + persistence, then ECMWF/persistence.
            if (os.environ.get("FLOODNET_RADAR_NOWCAST") == "1" and pair is not None
                    and pair[1].observed_at == frame.observed_at):
                motion = rn.estimate_motion(*pair)
        tail_at = None
        if tail is not None:
            tail_t, tail_v, tail_ret = tail
            age_s = max(0.0, (tail_ret - dec.observed_at_utc).total_seconds())
            tail_at = lambda t: float(np.interp(max(0.0, t - age_s), tail_t, tail_v))   # noqa: E731 wall-clock aligned
        fields, last_radar = [], field2d
        for t in t_s:
            if t == 0:
                fields.append(field2d)
            elif t <= rn.MAX_LEAD_S:
                if motion is not None:
                    last_radar, _ = sri.window_to_model_grid(rn.advect(frame, motion, t), frame.lons, frame.lats, grid)
                fields.append(last_radar)
            elif tail_at is not None:
                fields.append(np.full_like(field2d, tail_at(t)))
            else:
                fields.append(last_radar)
        lead_min = rn.MAX_LEAD_S // 60
        segments = [{"from_min": 0, "to_min": 0, "source": "IMD Mumbai-Veravali DWR", "label": "Radar-derived estimate",
                     "tag": sri.MODE}]
        segments.append({"from_min": 5, "to_min": lead_min, "source": "IMD Mumbai-Veravali DWR",
                         "label": rn.LABEL if motion is not None else "Persistence of the observed frame",
                         "tag": "EXPERIMENTAL_NOWCAST" if motion is not None else Tag.ESTIMATED.value})
        if horizon_s > rn.MAX_LEAD_S:
            segments.append({"from_min": lead_min, "to_min": horizon_s // 60,
                             "source": "ECMWF NWP (Open-Meteo)" if tail_at is not None else "IMD Mumbai-Veravali DWR",
                             "label": "ECMWF forecast" if tail_at is not None else "Persistence estimate",
                             "tag": Tag.NWP.value if tail_at is not None else Tag.ESTIMATED.value})
        if motion is None and tail_at is None:
            extension = "3-HOUR PERSISTENCE ESTIMATE"
        else:
            extension = " + ".join(f"{s['label'].upper()} {s['from_min']}-{s['to_min']} MIN" for s in segments[1:])
        horizon_note = "; ".join(f"{s['from_min']}-{s['to_min']} min: {s['label']} [{s['source']}]" for s in segments)
        prov = Provenance(
            Tag.ESTIMATED, f"IMD Mumbai-Veravali DWR SRI image ({sri.SRI_URL})",
            f"{sri.MODE}: rainfall-rate bins read from the colours of IMD's public SRI image observed {obs} -- "
            f"not IMD numerical QPE, not an IMD gridded product, not validated. Legend: {len(dec.legend)} "
            f"colour bins (0.1 to >={dec.legend[0].lo:.1f} mm/hr, capped); bin midpoints used. Georeference: "
            f"{g['method']} (tick RMS {g['tick_rms_px']:.2f} px, radar-site check "
            f"{g['site_vs_crosshair_km']:.2f} km); displayed pixel ~{g['pixel_km'][0]:.2f} km, native radar "
            f"resolution not published. Forecast horizon by source -- {horizon_note}. "
            + (f"Nowcast motion: {rn.METHOD}; {motion.speed_kmh:.0f} km/h toward {motion.toward_deg:.0f} deg, "
               f"correlation {motion.correlation:.2f}, from frames {motion.gap_min:.0f} min apart; experimental, "
               f"not an IMD nowcast. " if motion is not None else
               "No sequential-frame motion was available, so no radar nowcast is made. ")
            + f"Known limits: legend colours shared with the basemap "
            f"(~23.7-26.2 mm/h) are excluded, so such echoes are missed; values above the cap are held at the cap; "
            f"georeference is approximate (from the image's own axes); IMD reuse/automated-retrieval terms for "
            f"the public image are unconfirmed.")
        scen = scenario_from_gridded(
            IMD_SRI_ID, "IMD Mumbai-Veravali DWR (radar-derived estimate)", t_s, fields,
            src_lons=None, src_lats=None, grid=grid, provenance=prov, already_on_model_grid=True,
            description="Experimental rainfall field decoded from IMD's Mumbai-Veravali DWR SRI image; see the "
                        "provenance note for which source drives each part of the 3-hour window.")
        stats = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in dec.stats.items()}
        widths = [b.hi - b.lo for b in dec.legend[1:]]
        detail = {
            "source": "IMD", "mode": sri.MODE, "radar_site": sri.SITE_NAME,
            "radar_site_lonlat": [sri.SITE_LON, sri.SITE_LAT], "source_url": sri.SRI_URL,
            "page_url": sri.PAGE_URL, "product": "Surface Rainfall Intensity (SRI)",
            "image_observed_at_utc": dec.observed_at_utc.isoformat(), "image_age_min": round(age_min, 1),
            "retrieved_at": retrieved_at, "legend_bins": len(dec.legend),
            "legend_bin_width_mm_h": [round(min(widths), 2), round(max(widths), 2)],
            "legend_cap_mm_h": dec.legend[0].lo, "excluded_legend_colours": stats["excluded_legend_colours"],
            "georeference_method": g["method"], "georeference_tick_rms_px": round(g["tick_rms_px"], 3),
            "radar_site_check_km": round(g["site_vs_crosshair_km"], 3),
            "approx_pixel_km": [round(g["pixel_km"][0], 3), round(g["pixel_km"][1], 3)],
            "native_resolution": "not published by IMD",
            "confidence": {"echo_pct": stats["echo_pct"], "no_echo_pct": stats["no_echo_pct"],
                           "unknown_pct": stats["unknown_pct"]},
            "pilot_window": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in win.items()},
            "pilot_mean_mm_h": round(float(field2d.mean()), 3), "pilot_max_mm_h": round(float(field2d.max()), 3),
            "frame_kind": "t=0 observed image frame; t>0 per forecast_segments",
            "forecast_segments": segments,
            "nowcast": motion.to_dict() if motion is not None else None,
            "frames_in_history": len(store) if store is not None else 1,
            "source_label": "IMD Mumbai-Veravali DWR \u00b7 radar-derived estimate",
            "observation_tag": sri.MODE, "forecast_driver_tag": Tag.ESTIMATED.value,
            "forecast_extension_label": extension,
            "is_official_qpe": False, "is_nowcast": False, "is_imd_nowcast": False,
            "is_experimental_image_nowcast": motion is not None,
        }
        meta = RainfallSourceMeta(
            source_type="radar_image_derived", source_name="IMD Mumbai-Veravali DWR",
            timestamp=dec.observed_at_utc.isoformat(), forecast_horizon_min=horizon_s // 60,
            resolution_min=None, data_mode=Tag.ESTIMATED.value, provenance=scen.provenance, detail=detail)
        return scen, meta


# Module-level singletons: both caching providers keep their last successful fetch internally (see
# CACHE_TTL_S) so repeated status checks / run requests within one process don't re-hit their upstream API
# needlessly. list_providers() must always return these SAME instances, not fresh ones, or the cache would
# never persist across calls.
_imd_live_provider = IMDObservationProvider()
_ecmwf_provider = ECMWFForecastProvider()
_imerg_provider = IMERGSatelliteProvider()
# Stateless (it fetches nothing and caches nothing), but shared for the same reason: one object identity for
# the radar boundary everywhere it is reported.
_imd_radar_provider = IMDRadarNowcastProvider()
_synthetic_spatial_provider = SyntheticSpatialProvider()
_imd_sri_provider = IMDVeravaliSRIImageProvider(frame_dir=config.STATE_DIR / "radar_frames")


def list_providers() -> dict[str, RainfallProvider]:
    """One place to ask "give me rainfall input X" without knowing which provider class backs it.

    Keyed by scenario/source id: 'moderate', 'heavy', 'cloudburst' -> the shared `ScenarioProvider`;
    'july2005' -> `HistoricalReplayProvider`; 'live' -> the shared `IMDObservationProvider` (may raise
    `ProviderUnavailable` if `IMD_API_KEY` isn't configured -- that is expected, not an error to hide);
    'ecmwf' -> the shared `ECMWFForecastProvider` (NWP forecast source, may raise
    `ProviderUnavailable` if Open-Meteo is unreachable or returns insufficient data);
    'imd_radar' -> the shared `IMDRadarNowcastProvider` (disabled-by-default integration boundary; ALWAYS
    raises `ProviderUnavailable` with an "ACCESS PENDING" reason -- that is its designed behaviour, not a
    failure, and it must never be swapped for another provider when it raises);
    'external_nowcast' -> the deprecated, inert `ExternalNowcastProvider`.
    """
    out: dict[str, RainfallProvider] = {}
    scen_provider = ScenarioProvider()
    for sid in scen_provider.ids:
        out[sid] = scen_provider
    out[HistoricalReplayProvider.SCENARIO_ID] = HistoricalReplayProvider()
    out[LIVE_ID] = _imd_live_provider
    out[ECMWF_ID] = _ecmwf_provider
    out[RADAR_ID] = _imd_radar_provider
    # Gridded/spatial sources. Both need grid=<Terrain.grid> passed to .get(); without it they raise
    # ProviderUnavailable rather than guessing a grid.
    out[IMERG_ID] = _imerg_provider
    out[SPATIAL_SYNTHETIC_ID] = _synthetic_spatial_provider
    out[IMD_SRI_ID] = _imd_sri_provider
    out["external_nowcast"] = ExternalNowcastProvider()
    return out


def get_source_meta(scenario_id: str) -> Optional[RainfallSourceMeta]:
    """Convenience for API callers: `RainfallSourceMeta` for a known, currently-servable scenario id, or
    None if unrecognised or unavailable (e.g. a scenario id present in a loaded pilot's scenarios.json that
    isn't one of the provider-wired ids, the inert 'external_nowcast' stub, the always-unavailable
    'imd_radar' boundary, or 'live' when IMD_API_KEY isn't configured / the request failed). Use
    `provider_status()` instead of this function when you need to
    distinguish "unavailable" from "doesn't exist" (e.g. to show *why* LIVE isn't offered right now)."""
    providers = list_providers()
    p = providers.get(scenario_id)
    if p is None or scenario_id == "external_nowcast":
        return None
    try:
        _, meta = p.get(scenario_id)
    except (KeyError, NotImplementedError, ProviderUnavailable):
        return None
    return meta


def provider_status() -> list[dict]:
    """Rainfall sources worth showing to a caller (excludes the deprecated `ExternalNowcastProvider`, which
    names no source and can never work in this build under any configuration; the radar boundary IS listed,
    permanently unavailable, because "radar is pending" is information an operator needs). Used by
    GET /api/status.

    Unlike `get_source_meta`, this does NOT call `IMDObservationProvider.get()` (which performs a real
    network request) -- a status endpoint that's polled routinely must stay cheap. It reports whether the
    live provider is *configured* (no network call) and, if a cached fetch already succeeded, includes that
    cached metadata; it never blocks a status check on IMD's network being reachable.
    """
    out = []
    for sid in list(ScenarioProvider().ids) + [HistoricalReplayProvider.SCENARIO_ID]:
        meta = get_source_meta(sid)
        if meta is None:
            continue
        out.append({"id": sid, "source_type": meta.source_type, "source_name": meta.source_name,
                     "data_mode": meta.data_mode, "timestamp": meta.timestamp,
                     "resolution_min": meta.resolution_min, "available": True})

    # data_mode is the class of what this source PRODUCES (known without a fetch), so a status consumer never
    # has to show "unknown" for a source whose nature is established; `available` says whether it can run now.
    live_row: dict = {"id": LIVE_ID, "source_type": "live_observation", "source_name": "IMD (live observation)",
                       "data_mode": Tag.ESTIMATED.value, "timestamp": None, "resolution_min": None,
                       "status_label": "VERIFIED"}
    from .imd_auth import manager as _imd_auth
    _auth = _imd_auth.status()
    live_row["auth_state"] = _auth.state
    if not _imd_live_provider.is_configured():
        live_row.update(available=False, reason=f"{IMDObservationProvider.API_KEY_ENV} not configured")
    elif not _imd_auth.usable():
        # key present but the token is missing / expired / rejected: the live source cannot answer right now
        live_row.update(available=False, reason=_auth.detail)
    elif _imd_live_provider._cache is not None:  # a cached successful fetch exists -- report it, no new call
        _, scen, meta = _imd_live_provider._cache
        live_row.update(available=True, source_name=meta.source_name, data_mode=meta.data_mode,
                         timestamp=meta.timestamp, resolution_min=meta.resolution_min)
        live_row.update(timing_staleness((meta.detail or {}).get("timing"), _imd_live_provider._clock()) or {})
    else:
        live_row.update(available=True, reason="configured, not yet fetched this process")
    out.append(live_row)

    ecmwf_row: dict = {"id": ECMWF_ID, "source_type": "ecmwf_forecast", "source_name": "ECMWF NWP (Open-Meteo)",
                        "data_mode": Tag.NWP.value, "timestamp": None, "resolution_min": None,
                        "status_label": "VERIFIED"}
    if _ecmwf_provider._cache is not None:  # a cached successful fetch exists -- report it, no new call
        _, _, meta = _ecmwf_provider._cache
        ecmwf_row.update(available=True, source_name=meta.source_name, data_mode=meta.data_mode,
                          timestamp=meta.timestamp, resolution_min=meta.resolution_min)
        ecmwf_row.update(timing_staleness((meta.detail or {}).get("timing"), _ecmwf_provider._clock()) or {})
    else:
        # Unlike IMD, ECMWF via Open-Meteo needs no credentials -- "available" reflects that a fetch is
        # possible on demand, not that one has already succeeded this process.
        ecmwf_row.update(available=True, reason="no credentials required; fetched on demand")
    out.append(ecmwf_row)

    out.append({"id": IMD_SRI_ID, "source_type": "radar_image_derived",
                "source_name": "IMD Mumbai-Veravali DWR (radar-derived estimate)", "data_mode": "ESTIMATED",
                "timestamp": None, "resolution_min": None, "available": True,
                "status_label": "RADAR-DERIVED ESTIMATE",
                "reason": "public IMD image; fetched and decoded on demand"})

    # IMD radar: listed so the honest answer to "where is radar?" is visible in the API rather than absent,
    # and permanently available=False. `unavailable_reason()` is a pure string computation -- no network
    # call, no credential use -- so a routinely-polled status endpoint stays cheap. This row is never
    # available=True in this build: see IMDRadarNowcastProvider's docstring and decision D-14.
    out.append({"id": RADAR_ID, "source_type": "radar_nowcast",
                "source_name": "IMD Doppler Weather Radar (integration boundary -- not connected)",
                "data_mode": None, "timestamp": None, "resolution_min": None,
                "available": False, "reason": _imd_radar_provider.unavailable_reason(),
                "enabled": _imd_radar_provider.is_enabled()})
    return out
