"""EXPERIMENTAL radar-image nowcast: Lagrangian persistence (advection) of decoded IMD SRI rain fields.

Not an IMD nowcast. Not validated. It only ever runs on rain fields DECODED by `imd_sri_image` -- never on the
radar image itself, whose basemap, labels, range rings and legend would dominate any image-motion estimate.

WHY THIS METHOD
---------------
Operational radar nowcasting (e.g. the pysteps "extrapolation" nowcast, TREC/COTREC tracking) estimates an echo
motion field from consecutive rain fields and advects the latest field along it; skill decays quickly with
lead time for convective rain, which is why it is limited here to 30 minutes. This module uses the simplest
defensible member of that family: ONE domain-mean displacement from the peak of the cross-correlation between
two consecutive decoded fields (TREC with a single box), then a rigid shift of the latest field. No growth,
decay, rotation or deformation is modelled. A single vector is all the evidence supports: decoded values are
legend bins, the frames are 20-40 min apart, and there are usually only a few small echoes in range.

WHEN IT REFUSES (returns None -> caller keeps the single-frame estimate)
-----------------------------------------------------------------------
fewer than two distinct frames; frames too close/far apart in time; different window geometry; too few echo
pixels in either frame; a weak correlation peak; or an implied speed that is not physically plausible.

WHERE FRAMES COME FROM
----------------------
IMD publishes one current SRI frame (overwritten in place). Its 19-frame animation GIF
(Radar/animation/Converted/VRV_SRI.gif, linked from radar_animation.php?id=Mumbai-Veravali) carries a single
timestamp for the whole file -- per-frame times exist only as rendered text -- and was found (2026-09-21) to
contain 19 identical copies of a stale frame, so it is not used. Instead FloodNet keeps its own small history
of frames it has itself fetched, each with the verified GIF-comment timestamp.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

METHOD = "single-vector cross-correlation (TREC) + rigid advection of the decoded rain field"
LABEL = "Experimental radar-image nowcast"
MAX_LEAD_S = 30 * 60             # advection is not used beyond 30 min
MIN_GAP_MIN, MAX_GAP_MIN = 5.0, 45.0
MIN_ECHO_PX = 80                 # in EACH frame's window; fewer than this is not a trackable pattern
MIN_CORRELATION = 0.35
MAX_SPEED_KMH = 120.0
WINDOW_MARGIN_KM = 60.0          # echoes this far upstream can reach the pilot within the 30 min lead
MAX_FRAMES = 6


@dataclass
class Frame:
    observed_at: datetime
    rain: np.ndarray             # [h, w] mm/h, NaN = unknown
    rows: tuple[int, int]        # window slice in source-image pixels
    cols: tuple[int, int]
    lons: np.ndarray
    lats: np.ndarray
    pixel_km: tuple[float, float]

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.rain)

    @property
    def echo_px(self) -> int:
        return int(np.nansum(self.rain > 0))


@dataclass(frozen=True)
class Motion:
    dy_px_per_min: float         # +y = image rows downward (southward)
    dx_px_per_min: float         # +x = eastward
    correlation: float
    speed_kmh: float
    toward_deg: float            # compass bearing the echoes move TOWARD
    gap_min: float
    from_frames: tuple[str, str]

    def to_dict(self) -> dict:
        return {"method": METHOD, "speed_kmh": round(self.speed_kmh, 1), "toward_deg": round(self.toward_deg),
                "correlation": round(self.correlation, 3), "frame_gap_min": round(self.gap_min, 1),
                "from_frames_utc": list(self.from_frames)}


def frame_from_decoded(dec, bbox_lonlat, margin_km: float = WINDOW_MARGIN_KM) -> Frame:
    """Cut the regional window (pilot +/- margin) out of a DecodedSRI."""
    from .imd_sri_image import pilot_window
    px_km = dec.georef["pixel_km"]
    margin_px = int(round(margin_km / min(px_km)))
    rs, cs = pilot_window(dec, bbox_lonlat, margin_px=margin_px)
    return Frame(dec.observed_at_utc, dec.rain_mm_h[rs, cs].astype(np.float32).copy(),
                 (rs.start, rs.stop), (cs.start, cs.stop), dec.lon_of_x[cs].copy(), dec.lat_of_y[rs].copy(),
                 (float(px_km[0]), float(px_km[1])))


class FrameStore:
    """Small time-ordered history of decoded frames, de-duplicated on the verified observation time. Optionally
    persisted as .npz (decoded arrays only -- no IMD imagery) so history survives a backend restart."""

    def __init__(self, directory: Optional[Path] = None, max_frames: int = MAX_FRAMES):
        self.directory, self.max_frames = directory, max_frames
        self._frames: list[Frame] = []
        if directory is not None:
            self._load()

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def frames(self) -> list[Frame]:
        return list(self._frames)

    def add(self, frame: Frame) -> bool:
        """True if this observation time was new. Re-published copies of the same scan are ignored."""
        if any(f.observed_at == frame.observed_at for f in self._frames):
            return False
        self._frames.append(frame)
        self._frames.sort(key=lambda f: f.observed_at)
        dropped, self._frames = self._frames[:-self.max_frames], self._frames[-self.max_frames:]
        if self.directory is not None:
            self._save(frame)
            for f in dropped:
                self._path(f).unlink(missing_ok=True)
        return True

    def latest_pair(self) -> Optional[tuple[Frame, Frame]]:
        return (self._frames[-2], self._frames[-1]) if len(self._frames) >= 2 else None

    def clear(self) -> None:
        self._frames = []

    def _path(self, f: Frame) -> Path:
        return self.directory / f"sri_{f.observed_at:%Y%m%dT%H%M%SZ}.npz"

    def _save(self, f: Frame) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(self._path(f), rain=f.rain, rows=f.rows, cols=f.cols, lons=f.lons, lats=f.lats,
                                pixel_km=f.pixel_km, observed_at=f.observed_at.isoformat())
        except OSError as ex:            # history is an optimisation; never fail a run over it
            log.warning("could not persist radar frame: %s", ex)

    def _load(self) -> None:
        if not self.directory.is_dir():
            return
        for p in sorted(self.directory.glob("sri_*.npz"))[-self.max_frames:]:
            try:
                with np.load(p) as z:
                    self._frames.append(Frame(datetime.fromisoformat(str(z["observed_at"])), z["rain"],
                                              tuple(int(v) for v in z["rows"]), tuple(int(v) for v in z["cols"]),
                                              z["lons"], z["lats"], tuple(float(v) for v in z["pixel_km"])))
            except Exception as ex:  # noqa: BLE001 -- a corrupt cache file is skipped, never trusted
                log.warning("ignoring unreadable radar frame %s: %s", p.name, ex)
        self._frames.sort(key=lambda f: f.observed_at)


def _prep(rain: np.ndarray) -> np.ndarray:
    """Unknown -> 0, then log1p so one capped-intensity core does not dominate the correlation."""
    return np.log1p(np.nan_to_num(rain, nan=0.0).astype(np.float64))


def estimate_motion(prev: Frame, curr: Frame) -> Optional[Motion]:
    """One displacement vector prev -> curr from the cross-correlation peak of the decoded rain fields."""
    gap_min = (curr.observed_at - prev.observed_at).total_seconds() / 60.0
    if not (MIN_GAP_MIN <= gap_min <= MAX_GAP_MIN):
        return None
    if prev.rain.shape != curr.rain.shape or prev.rows != curr.rows or prev.cols != curr.cols:
        return None
    if prev.echo_px < MIN_ECHO_PX or curr.echo_px < MIN_ECHO_PX:
        return None

    a, b = _prep(prev.rain), _prep(curr.rain)
    h, w = a.shape
    corr = np.fft.ifft2(np.fft.fft2(b) * np.conj(np.fft.fft2(a))).real
    max_px = MAX_SPEED_KMH * (gap_min / 60.0) / min(curr.pixel_km)
    dy = np.fft.fftfreq(h, 1.0 / h)[:, None]
    dx = np.fft.fftfreq(w, 1.0 / w)[None, :]
    corr = np.where(np.hypot(dy, dx) <= max_px, corr, -np.inf)
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    sy, sx = int(dy[iy, 0]), int(dx[0, ix])

    # correlation coefficient of the overlap after applying the shift (the FFT product is un-normalised)
    a_s = np.roll(a, (sy, sx), axis=(0, 1))
    overlap = np.ones_like(a, bool)
    if sy > 0: overlap[:sy, :] = False
    elif sy < 0: overlap[sy:, :] = False
    if sx > 0: overlap[:, :sx] = False
    elif sx < 0: overlap[:, sx:] = False
    av, bv = a_s[overlap], b[overlap]
    denom = float(np.sqrt((av * av).sum() * (bv * bv).sum()))
    r = float((av * bv).sum() / denom) if denom > 0 else 0.0
    if r < MIN_CORRELATION:
        return None

    vy_km, vx_km = sy * curr.pixel_km[1], sx * curr.pixel_km[0]
    speed = float(np.hypot(vx_km, vy_km) / (gap_min / 60.0))
    if speed > MAX_SPEED_KMH:
        return None
    toward = float(np.degrees(np.arctan2(vx_km, -vy_km)) % 360.0)      # rows increase southward
    return Motion(sy / gap_min, sx / gap_min, r, speed, toward, gap_min,
                  (prev.observed_at.isoformat(), curr.observed_at.isoformat()))


def advect(frame: Frame, motion: Motion, lead_s: float) -> np.ndarray:
    """Latest frame shifted along `motion` for `lead_s`. Pixels advected in from outside the window, and
    pixels that were unknown, stay NaN -- they are never invented."""
    from scipy.ndimage import shift as nd_shift
    if lead_s <= 0:
        return frame.rain.copy()
    lead_min = min(lead_s, MAX_LEAD_S) / 60.0
    off = (motion.dy_px_per_min * lead_min, motion.dx_px_per_min * lead_min)
    ok = frame.valid.astype(np.float64)
    num = nd_shift(np.nan_to_num(frame.rain, nan=0.0).astype(np.float64) * ok, off, order=1, mode="constant", cval=0.0)
    den = nd_shift(ok, off, order=1, mode="constant", cval=0.0)
    out = np.full(frame.rain.shape, np.nan, np.float32)
    good = den > 0.5
    out[good] = np.maximum(num[good] / den[good], 0.0)
    return out
