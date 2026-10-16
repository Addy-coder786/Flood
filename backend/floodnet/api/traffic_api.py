"""Live traffic API (TomTom Flow Segment Data, floodnet/traffic/tomtom.py): GET /api/traffic/status and
POST /api/traffic/segments. No auth: road ids are public map data and the TomTom key never leaves the server.
Live traffic is NOT a flood forecast; FloodNet never recommends a traffic-faster road its flood rules consider unsafe.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..traffic import tomtom

router = APIRouter()


class SegmentsRequest(BaseModel):
    seg_ids: list[str] = Field(min_length=1, max_length=tomtom.MAX_POINTS)
    refresh: bool = False


def _roads():
    """The loaded pilot's RoadGraph. Tests replace this function."""
    from . import state
    p = state.get_pilot()
    roads = p.get("roads")
    if roads is None:
        raise HTTPException(503, "roads not present in pilot bundle")
    return roads


def midpoint(lonlat) -> tuple[float, float]:
    """The middle vertex of a polyline; for an even vertex count the mean of the two middle vertices (so a two-vertex
    segment is sampled halfway along it, not at a junction)."""
    k = len(lonlat)
    if k % 2:
        p = lonlat[k // 2]
    else:
        p = (lonlat[k // 2 - 1] + lonlat[k // 2]) / 2
    return float(p[0]), float(p[1])


@router.get("/api/traffic/status")
def traffic_status():
    return tomtom.status()


@router.post("/api/traffic/segments")
async def traffic_segments(req: SegmentsRequest):
    by_id = {s.seg_id: s for s in _roads().segments}
    unknown = [sid for sid in dict.fromkeys(req.seg_ids) if sid not in by_id]
    if unknown:
        more = f" (+{len(unknown) - 5} more)" if len(unknown) > 5 else ""
        raise HTTPException(422, f"unknown road segment ids: {', '.join(unknown[:5])}{more}")
    points = [(sid, *midpoint(by_id[sid].lonlat)) for sid in dict.fromkeys(req.seg_ids)]
    return await run_in_threadpool(tomtom.sample, points, refresh=req.refresh)
