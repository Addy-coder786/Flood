"""Deterministic, format-faithful stand-in for the IMD Mumbai-Veravali SRI GIF -- for decoder LOGIC tests only.

It is generated from the committed legend table (floodnet/rainfall/imd_vrv_sri_legend.json) and the layout
constants measured on the official image (imd_sri_image.py): same size, palette mode, legend band positions
and colours, legend/axis tick marks, crosshair and IST GIF comment. Its "basemap" is two flat colours and its
echoes are discs placed where the test asks. It contains NO IMD imagery and is NOT radar data; the real-image
integration tests (test_imd_sri_image.py, section B) are what check behaviour on the genuine product.
"""
from __future__ import annotations

import io
import json

import numpy as np
from PIL import Image

from floodnet.rainfall import imd_sri_image as sri

W, H = sri.IMAGE_SIZE
PAGE, INK, SEA, LAND = (254, 254, 254), (3, 4, 5), (201, 217, 225), (120, 160, 90)
GREY_TEXT = (40, 40, 40)


def legend_rgbs() -> list[int]:
    return [int(b["rgb"], 16) for b in json.loads(sri._REFERENCE_LEGEND.read_text(encoding="utf-8"))["bins"]]


def lonlat_to_px(lon: float, lat: float) -> tuple[int, int]:
    (lo0, x0), (lo1, x1) = sri.LON_TICKS[0], sri.LON_TICKS[-1]
    (la0, y0), (la1, y1) = sri.LAT_TICKS[0], sri.LAT_TICKS[-1]
    x = x0 + (lon - lo0) * (x1 - x0) / (lo1 - lo0)
    y = y0 + (lat - la0) * (y1 - y0) / (la1 - la0)
    return int(round(x)), int(round(y))


def make_sri_gif(echoes=(), comment: str = "2026-09-18T09:21:28", occluders=(), legend_override=None) -> bytes:
    """echoes: [(lon, lat, legend_band_index, radius_px)]; occluders: [(lon, lat, 'white'|'text', radius_px)]."""
    rgbs = legend_override or legend_rgbs()
    palette = [PAGE, INK, SEA, LAND, GREY_TEXT] + [((c >> 16) & 255, (c >> 8) & 255, c & 255) for c in rgbs]
    colour = {c: i for i, c in enumerate(palette)}
    band = lambda k: 5 + k  # noqa: E731
    idx = np.full((H, W), colour[PAGE], np.uint8)

    x0, x1, y0, y1 = sri.FRAME
    idx[y0:y1, x0:x1] = colour[SEA]
    idx[y0:y1, 1150:x1] = colour[LAND]                        # a coastline west of the radar
    idx[y0 - 3:y0, x0 - 3:x1 + 3] = idx[y1:y1 + 3, x0 - 3:x1 + 3] = colour[INK]
    idx[y0 - 3:y1 + 3, x0 - 3:x0] = idx[y0 - 3:y1 + 3, x1:x1 + 3] = colour[INK]
    cx, cy = lonlat_to_px(sri.SITE_LON, sri.SITE_LAT)
    idx[y0 + 20:y1 - 20, cx] = colour[INK]                    # crosshair through the radar site
    idx[cy, x0 + 20:x1 - 20] = colour[INK]
    for _, x in sri.LON_TICKS:                                # axis ticks, inside the frame edge
        idx[sri.LON_TICK_ROW - 6:y1, int(x) - 1:int(x) + 2] = colour[INK]
    for _, y in sri.LAT_TICKS:
        idx[int(y) - 1:int(y) + 2, x0:sri.LAT_TICK_COL + 6] = colour[INK]

    ref = json.loads(sri._REFERENCE_LEGEND.read_text(encoding="utf-8"))["bins"]
    for k, b in enumerate(ref):                               # legend bar and its tick marks
        ya, yb = b["legend_y"]
        idx[ya:yb + 1, 2672:2728] = band(k)
    idx[640:sri.LEGEND_Y0, 2690:2710] = band(0)               # the open-ended arrow
    for y in sri.LEGEND_TICKS_Y:
        idx[y - 1:y + 2, 2728:2746] = colour[INK]

    yy, xx = np.mgrid[0:H, 0:W]
    for lon, lat, k, r in echoes:
        ex, ey = lonlat_to_px(lon, lat)
        idx[(xx - ex) ** 2 + (yy - ey) ** 2 <= r * r] = band(k)
    for lon, lat, kind, r in occluders:
        ex, ey = lonlat_to_px(lon, lat)
        idx[(xx - ex) ** 2 + (yy - ey) ** 2 <= r * r] = colour[PAGE] if kind == "white" else colour[GREY_TEXT]

    img = Image.fromarray(idx, mode="P")
    flat = [v for c in palette for v in c]
    img.putpalette(flat + [0] * (768 - len(flat)))
    buf = io.BytesIO()
    img.save(buf, format="GIF", comment=comment.encode("ascii"))
    return buf.getvalue()
