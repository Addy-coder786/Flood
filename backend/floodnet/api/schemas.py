"""Pydantic request models for the FloodNet API."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class BlockageSpec(BaseModel):
    mode: Literal["none", "fraction", "edges", "random", "near"] = "none"
    fraction: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="blockage fraction 0..1 applied to selected edges")
    edge_ids: Optional[list[str]] = None
    share: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="share of edges to block (mode=random)")
    lonlat: Optional[list[float]] = Field(default=None, min_length=2, max_length=2)
    radius_m: Optional[float] = Field(default=None, gt=0)
    seed: Optional[int] = None

    def to_spec(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class SimulateRequest(BaseModel):
    scenario_id: str
    blockage: BlockageSpec = Field(default_factory=BlockageSpec)
    horizon_min: int = Field(default=180, ge=5, le=720)


class ReplayRequest(BaseModel):
    blockage: BlockageSpec = Field(default_factory=BlockageSpec)
    horizon_min: int = Field(default=180, ge=5, le=720)


class CompareRequest(BaseModel):
    scenario_id: str
    blockage: BlockageSpec = Field(default_factory=BlockageSpec)
    horizon_min: int = Field(default=180, ge=5, le=720)


class RouteRequest(BaseModel):
    origin: list[float] = Field(min_length=2, max_length=2, description="[lon, lat]")
    dest: list[float] = Field(min_length=2, max_length=2, description="[lon, lat]")
    t_min: float = Field(default=0.0, ge=0.0)
    vehicle: str = "car"
    run_id: Optional[str] = None
    n_candidates: int = Field(default=3, ge=2, le=5,
                              description="requested number of alternative route candidates; only used by "
                                          "POST /api/route/alternatives, ignored by POST /api/route")
    # Self-contained runs (serverless hosting): the client sends the depths of the frame on screen, and for
    # /alternatives the run's per-segment series, so routing never needs the run from this server's memory.
    # Only wet segments need to be sent: a missing segment is dry.
    street_depth_m: Optional[dict[str, float]] = None
    series_t_min: Optional[list[float]] = None
    streets_cm: Optional[dict[str, list[float]]] = None
    data_mode: Optional[str] = None

    @field_validator("origin", "dest")
    @classmethod
    def _valid_lonlat(cls, v: list[float]) -> list[float]:
        lon, lat = v[0], v[1]
        if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            raise ValueError(f"coordinate out of range: lon={lon}, lat={lat} "
                              "(expected abs(lon)<=180, abs(lat)<=90)")
        return v


class AccessRouteRequest(BaseModel):
    """POST /api/access/route (MISC-04 Phase 1, docs/ROAD_ACCESS.md). Times are simulation minutes from the
    forecast start (T0). `events` are timestamped road events (floodnet/access/events.py); only those with
    received_t_min <= decision_t_min are used. Optional per event: `source_tier` (AUTHORITY | OPERATOR | FIELD_TEAM |
    PUBLIC, default OPERATOR) and `references` (explanatory ids); see DECISIONS D-32."""
    origin: list[float] = Field(min_length=2, max_length=2, description="[lon, lat]")
    dest: list[float] = Field(min_length=2, max_length=2, description="[lon, lat]")
    vehicle: str = "car"
    dispatch_t_min: float = Field(default=0.0, ge=0.0, description="when the vehicle leaves the origin")
    decision_t_min: Optional[float] = Field(default=None, ge=0.0,
                                            description="when the plan is made; default = dispatch_t_min")
    run_id: Optional[str] = None
    series_t_min: Optional[list[float]] = None
    streets_cm: Optional[dict[str, list[float]]] = None
    data_mode: Optional[str] = None
    events: list[dict] = Field(default_factory=list)

    @field_validator("origin", "dest")
    @classmethod
    def _valid_lonlat(cls, v: list[float]) -> list[float]:
        return RouteRequest._valid_lonlat(v)


class ResponsePlanRequest(BaseModel):
    """POST /api/response/plan (MISC-04 Phase 2, docs/RESPONSE_PLANNER.md). The client holds the ledger and the
    event logs and sends them every time; the server rebuilds the plan at sim time `now_t_min`. `plan_version` is
    the version the ledger was last written by: a ledger carrying a newer `plan_version` is refused (422, D-33)."""
    now_t_min: float = Field(default=0.0, ge=0.0, description="simulation minutes from T0 at which the plan is made")
    run_id: Optional[str] = None
    series_t_min: Optional[list[float]] = None
    streets_cm: Optional[dict[str, list[float]]] = None
    data_mode: Optional[str] = None
    road_events: list[dict] = Field(default_factory=list)
    response_events: list[dict] = Field(default_factory=list)
    ledger: list[dict] = Field(default_factory=list)
    plan_version: int = Field(default=0, ge=0)
    mode: str = Field(default="arrival", pattern="^(arrival|current)$",
                      description="routing method for every leg; 'current' is the baseline for evaluation")
    resources_source: str = Field(default="demo", pattern="^(demo|registry)$",
                                  description="'registry' uses the live-operations resource registry (D-43)")


class ResponseEvaluateRequest(BaseModel):
    """POST /api/response/evaluate (MISC-04 Phase 3, docs/RESPONSE_EVALUATION.md): scripted replay with both
    routing methods, judged against a separate outcome run. Both runs come as run ids (local server) or as
    series sent by the client; `outcome_scenario_id` lets a local server simulate the outcome itself."""
    run_id: Optional[str] = None
    series_t_min: Optional[list[float]] = None
    streets_cm: Optional[dict[str, list[float]]] = None
    data_mode: Optional[str] = None
    outcome_run_id: Optional[str] = None
    outcome_series_t_min: Optional[list[float]] = None
    outcome_streets_cm: Optional[dict[str, list[float]]] = None
    outcome_scenario_id: Optional[str] = None
    outcome_horizon_min: int = Field(default=180, ge=30, le=360)
    include_ledgers: bool = False
    # D-35 seeded scripts: `seed` replays one generated script (echoed in the response's script.seed); `seeds`
    # replays up to 5 and returns the per-seed + aggregate bundle. Neither given -> the hand-written DEMO_SCRIPT.
    seed: Optional[int] = None
    seeds: Optional[list[int]] = None

    @field_validator("seeds")
    @classmethod
    def _few_seeds(cls, v: Optional[list[int]]) -> Optional[list[int]]:
        if v is not None and not 1 <= len(v) <= 5:
            raise ValueError("seeds must hold 1 to 5 seeds (each seed replays the script twice); omit it for the default script")
        return v


class WhatsAppSendRequest(BaseModel):
    """POST /api/alerts/whatsapp/send. The client supplies the summary lines; the server always prepends the TEST
    ALERT banner and sends only to the numbers configured in WHATSAPP_TO (docs/ALERTS_WHATSAPP.md)."""
    kind: Literal["test", "forecast", "response"] = "test"
    lines: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("lines")
    @classmethod
    def _short_lines(cls, v: list[str]) -> list[str]:
        if any(len(s) > 240 for s in v):
            raise ValueError("each line must be at most 240 characters")
        return v


class TTSRequest(BaseModel):
    """Guided-briefing voice request. `text` is narration the frontend already built from real simulation
    state (lib/briefing/facts.js -> narration.js); this endpoint only converts it to audio and never edits,
    translates or generates narration of its own."""
    text: str = Field(min_length=1, max_length=1200)
    lang: str = Field(default="en", pattern="^(en|hi|mr)$",
                      description="briefing language; maps to en-IN / hi-IN / mr-IN server-side")
