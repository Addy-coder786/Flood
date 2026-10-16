# Radar nowcast status (2026-09-21)

**Status: DISABLED BY DEFAULT (2026-09-21 final pass). EXPERIMENTAL, gated, logic-tested only.** Reliable sequential
frames could not be obtained from IMD (§1: the animation has one timestamp for 19 frames and was 19 identical copies;
the live feed re-published one stale scan for > 7 h across two checks), so the feature was not forced. Default radar
path = decoded SRI frame + persistence, then ECMWF/persistence. `FLOODNET_RADAR_NOWCAST=1` switches the advection step
on; frame history is kept either way so it can be evaluated once the radar reports consecutive scans. Label everywhere: "Experimental radar-image nowcast". It is
not an IMD nowcast, not validated, and **has not yet run on two genuinely consecutive IMD frames** (see §4).

Code: `backend/floodnet/rainfall/radar_nowcast.py`, wired into `IMDVeravaliSRIImageProvider` (scenario `imd_sri`).
Tests: `backend/tests/test_radar_nowcast.py` (12, incl. "disabled by default"). Decoder background: `docs/RADAR_SRI_IMAGE.md`.

## 1. Are multiple official frames available? (verified 2026-09-21 04:03 UTC)
| Source | Finding |
|---|---|
| `Radar/sri_vrv.gif` | One current frame, overwritten in place. Timestamp = GIF comment (IST). |
| `radar_animation.php?id=Mumbai-Veravali` → `Radar/animation/Converted/VRV_SRI.gif` | Reached by the page's own station link (no URL guessed). 19 frames, 3090×2488 each (same layout as the single frame), 500 ms/frame, **29.7 MB**. **One GIF comment for the whole file**; per-frame times exist only as rendered text. On inspection all 19 frames were **pixel-identical** copies of a stale scan (observed 20 Sep 22:48 UTC, >5 h old, re-published every ~30 min). |
| Archive | None at these URLs. |

Conclusion: the animation cannot supply timestamped frames without reading rendered text, and it is assembled from
periodic re-publications, not distinct scans. It is **not used**. FloodNet instead keeps its own history of frames
it has itself fetched (each with a verified timestamp), de-duplicated on observation time — so a re-published
stale scan never becomes "frame 2". History persists as decoded arrays (no IMD imagery) in
`data/interim/radar_frames/` (gitignored), max 6 frames. Geometry/legend stability: band positions and axis ticks
were identical in every frame decoded so far (18 and 21 Sep); each frame is re-validated and refused otherwise.
Permission for automated retrieval remains **unresolved**; fetches are on demand only, cached 5 min, no timer.

## 2. Method
Practice reviewed: operational extrapolation nowcasts (pysteps "extrapolation", TREC/COTREC) estimate echo motion
from consecutive rain fields and advect the latest field (Lagrangian persistence); skill for convective rain
falls off within tens of minutes. Implemented here: the simplest member of that family — **one** displacement
vector from the cross-correlation peak of two decoded fields (log-transformed, unknown → 0), then a rigid shift.
No growth/decay, rotation or deformation. A single vector is all the evidence supports (legend-bin values, frames
20–40 min apart, few small echoes). Dense optical flow (Farneback / Lucas-Kanade) was not used: it would add an
OpenCV dependency and imply a precision the input does not have.

Motion is computed **only on the decoded rain field** — never on the image, whose basemap, labels, range rings and
legend would dominate. Unknown pixels stay unknown; pixels advected in from outside the ±60 km window are unknown.

Refuses (→ no nowcast, single-frame behaviour kept) when: < 2 distinct frames; gap outside 5–45 min; window
geometry differs; < 80 echo pixels in either frame; correlation < 0.35; implied speed > 120 km/h.

## 3. Source-aware horizon (never silently blended)
| Model time | Driver | Tag |
|---|---|---|
| 0 min | decoded observed frame | RADAR_IMAGE_DERIVED_ESTIMATE |
| 5–30 min | advection nowcast **if** §2 accepts a frame pair; else the observed frame held | EXPERIMENTAL_NOWCAST / ESTIMATED |
| 30–180 min | ECMWF NWP (uniform over the pilot, aligned by wall-clock to the frame's observation time) if reachable; else persistence of the last radar-based field | NWP / ESTIMATED |

Each period is recorded in `rainfall_source.detail.forecast_segments`, in the provenance note, and shown in the
rainfall panel. Model t = 0 is the radar **observation** time, typically 30–50 min before "now".

## 4. What is and is not verified
- Verified on real IMD data: single-frame decode, timestamps, the stale-scan behaviour, the end-to-end `imd_sri` run.
- Verified on a generated stand-in only: history ordering/de-dup/persistence, motion recovery (2 px/min east
  recovered to ±0.1), masking (moving labels/white → no motion), refusals, advection, source transitions, labels.
- **Not verified:** motion from two real consecutive Veravali scans (the radar feed was stale during this work),
  and any skill of the nowcast. No accuracy is claimed.

## 5. To exercise it for real
Set `FLOODNET_RADAR_NOWCAST=1`, then run the `imd_sri` scenario twice, one radar scan apart (≈20–40 min) while the radar is reporting. The second run's
Sources entry lists `nowcast` (speed, bearing, correlation) when the pair is accepted, or says why not.
