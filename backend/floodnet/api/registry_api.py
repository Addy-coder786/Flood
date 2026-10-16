"""Registry API (2026-10-11): registration and import of authorised records, responder-only endpoints, responder
revocation, and the candidate-shelter workflow. Logic lives in floodnet/ops/registry.py; design in
docs/LIVE_OPERATIONS.md ("Registry and shelters").

Roles (floodnet/ops/auth.py, D-39): dispatcher endpoints use require_dispatcher. Responder endpoints need a registered
responder token and never fall back to the dispatcher, so an update is always attributed to the person who sent it.
Candidate shelters come from a volunteer token or from the dispatcher on a named volunteer's behalf; there is no
anonymous endpoint. Errors: store unavailable 503, bad input 422, unknown id 404, wrong role 401/403.

ops_api.py includes this router at its very end; this module imports ops_api lazily (inside functions) so that
either module can be imported first.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..ops import auth, registry as rg
from ..ops.store import utc_now

router = APIRouter()
P = "/api/ops/registry"


def _ops():
    from . import ops_api
    return ops_api


def _store():
    return _ops()._store()


def _errors(fn, *a, **kw):
    return _ops()._errors(fn, *a, **kw)


def _roads():
    """(roads, seg_ctx) of the loaded pilot, or (None, None): a missing road network makes the site check UNKNOWN."""
    try:
        roads, _, seg_ctx, _ = _ops()._ctx()
    except HTTPException:
        return None, None
    return roads, seg_ctx


def _responder(request: Request, store) -> dict:
    who = rg.responder_for_token(store, auth.presented_token(request))
    if who is None:
        raise HTTPException(401, "registered responder token required (it may have been revoked)")
    return who


# ------------------------------------------------------------------ models
class RecordReq(BaseModel):
    kind: str = Field(max_length=20)
    record: dict[str, Any]
    observed_at: Optional[str] = Field(default=None, max_length=40)
    client_ref: Optional[str] = Field(default=None, max_length=80)


class ImportReq(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    format: Literal["json", "csv"]
    text: str = Field(max_length=rg.IMPORT_MAX_CHARS)
    commit: bool = False


class ResponderUpdateReq(BaseModel):
    fields: dict[str, Any]
    observed_at: Optional[str] = Field(default=None, max_length=40)
    client_ref: Optional[str] = Field(default=None, max_length=80)


class CandidateReq(BaseModel):
    name: str = Field(default="", max_length=400)
    lonlat: Any = None
    address: str = Field(default="", max_length=800)
    description: str = Field(default="", max_length=2000)
    capacity_estimate: Optional[Any] = None
    observed_at: Optional[str] = Field(default=None, max_length=40)
    on_behalf_of: Optional[str] = Field(default=None, max_length=400)
    client_ref: Optional[str] = Field(default=None, max_length=80)


class PermissionReq(BaseModel):
    granted_by_role: str = Field(default="", max_length=400)
    granted_by_name: str = Field(default="", max_length=400)
    method: str = Field(default="", max_length=40)
    granted_at: Optional[str] = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=1200)


class SuitabilityReq(BaseModel):
    access: str = Field(default="", max_length=20)
    water_sanitation: str = Field(default="", max_length=20)
    accessibility: str = Field(default="", max_length=20)
    capacity_estimate: Optional[Any] = None
    notes: str = Field(default="", max_length=1200)


class ConfirmReq(BaseModel):
    total_capacity: Optional[int] = None
    occupancy: Optional[int] = None


class RejectReq(BaseModel):
    reason: str = Field(default="", max_length=1200)


# ------------------------------------------------------------------ dispatcher: vocabulary, records, import
@router.get(f"{P}/vocabulary")
def registry_vocabulary(_who: str = Depends(auth.require_dispatcher)):
    return rg.vocabulary()


@router.post(f"{P}/records")
def registry_register(req: RecordReq, _who: str = Depends(auth.require_dispatcher)):
    r = _errors(rg.register_resource, _store(), req.kind, req.record, actor="dispatcher", observed_at=req.observed_at,
                now_wall=utc_now(), client_ref=req.client_ref)
    return {"resource": r}


@router.post(f"{P}/import")
def registry_import(req: ImportReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    rows = _errors(rg.parse_import, req.text, req.format)
    return _errors(rg.import_records, s, rows, label=req.label, actor="dispatcher", commit=req.commit, now_wall=utc_now())


@router.post(f"{P}/responders/{{responder_id}}/revoke")
def registry_revoke(responder_id: str, _who: str = Depends(auth.require_dispatcher)):
    r = _errors(rg.revoke_responder, _store(), responder_id)
    return {"responder": rg.public_responder(r), "note": "this responder's token no longer works"}


# ------------------------------------------------------------------ responder (own token only)
@router.get(f"{P}/responder/me")
def registry_responder_me(request: Request):
    s = _store()
    who = _responder(request, s)
    actor = f"responder:{who['responder_id']}"
    res = [rg.get_resource(s, rid) for rid in who.get("scope", []) if s.get("resources", rid) is not None]
    out = {"responder": rg.public_responder(who), "resources": res, "stale_after_min": rg.STALE_AFTER_MIN,
           "pilot_bbox": list(rg.PILOT_BBOX),
           "allowed_fields": {k: sorted(v) for k, v in rg.ROLE_FIELDS.get(who["role"], {}).items()}}
    if who["role"] == "volunteer":
        out["candidates"] = [rg.volunteer_view(c) for c in rg.list_candidates(s, actor=actor)]
    return out


@router.post(f"{P}/responder/resources/{{resource_id}}/updates")
def registry_responder_update(resource_id: str, req: ResponderUpdateReq, request: Request):
    s = _store()
    who = _responder(request, s)
    rec = _errors(rg.submit_update, s, actor=f"responder:{who['responder_id']}", role=who["role"],
                  resource_id=resource_id, fields=req.fields, observed_at=req.observed_at, now_wall=utc_now(),
                  client_ref=req.client_ref)
    return {"update": rec, "resource": rg.get_resource(s, resource_id)}


# ------------------------------------------------------------------ candidate shelters
@router.post(f"{P}/candidates")
def registry_submit_candidate(req: CandidateReq, request: Request):
    s = _store()
    token = auth.presented_token(request)
    who = rg.responder_for_token(s, token)
    if who is not None:                           # a responder token is never treated as the dispatcher
        if who["role"] != "volunteer":
            raise HTTPException(403, "only a volunteer token or the dispatcher can submit candidate shelters")
        actor, by = f"responder:{who['responder_id']}", f"volunteer {who['name']} ({who['responder_id']})"
    elif auth.dispatcher_allowed(token, request.client.host if request.client else None, request.headers.get("host"),
                                 request.headers.get("origin")):
        on_behalf = _errors(rg.check_text, "on_behalf_of", req.on_behalf_of, 80)
        if not on_behalf:
            raise HTTPException(422, "on_behalf_of is required: name the volunteer this place comes from")
        actor, by = "dispatcher", f"dispatcher on behalf of {on_behalf}"
    else:
        raise HTTPException(401, "a volunteer token or the dispatcher is required; there is no anonymous submission")
    data = req.model_dump(exclude={"on_behalf_of", "client_ref"}, exclude_none=True)
    c = _errors(rg.submit_candidate, s, data, actor=actor, submitted_by=by, now_wall=utc_now(), client_ref=req.client_ref)
    return {"candidate": c if actor == "dispatcher" else rg.volunteer_view(c) | {"submitted_by": c["submitted_by"]}}


@router.get(f"{P}/candidates")
def registry_candidates(_who: str = Depends(auth.require_dispatcher)):
    return {"candidates": rg.list_candidates(_store()), "states": list(rg.CANDIDATE_STATES),
            "note": "candidates are not shelters: the planner never uses one before it is CONFIRMED"}


@router.get(f"{P}/candidates/{{cid}}/site-check")
def registry_site_check(cid: str, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    c = _errors(rg.get_candidate, s, cid)
    roads, seg_ctx = _roads()
    return rg.site_flood_check(s, c["lonlat"], roads, seg_ctx)


@router.post(f"{P}/candidates/{{cid}}/permission")
def registry_permission(cid: str, req: PermissionReq, _who: str = Depends(auth.require_dispatcher)):
    return {"candidate": _errors(rg.record_permission, _store(), cid, req.model_dump(), actor="dispatcher", now_wall=utc_now())}


@router.post(f"{P}/candidates/{{cid}}/suitability")
def registry_suitability(cid: str, req: SuitabilityReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    c = _errors(rg.get_candidate, s, cid)
    roads, seg_ctx = _roads()
    site = rg.site_flood_check(s, c["lonlat"], roads, seg_ctx)       # computed here, never taken from the client
    return {"candidate": _errors(rg.record_suitability, s, cid, req.model_dump(), actor="dispatcher", site_check=site,
                                 now_wall=utc_now())}


@router.post(f"{P}/candidates/{{cid}}/confirm")
def registry_confirm(cid: str, req: ConfirmReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    c = _errors(rg.confirm_candidate, s, cid, req.model_dump(exclude_none=True), actor="dispatcher", now_wall=utc_now())
    return {"candidate": c, "resource": rg.get_resource(s, c["shelter_id"])}


@router.post(f"{P}/candidates/{{cid}}/reject")
def registry_reject(cid: str, req: RejectReq, _who: str = Depends(auth.require_dispatcher)):
    return {"candidate": _errors(rg.reject_candidate, _store(), cid, req.reason, actor="dispatcher", now_wall=utc_now())}
