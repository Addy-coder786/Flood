"""Minimal PNG encoder (zlib + struct only, no PIL). Grayscale (L) and RGBA arrays -> PNG bytes / base64."""
from __future__ import annotations

import base64
import struct
import zlib

import numpy as np


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(arr: np.ndarray) -> bytes:
    """arr: uint8 [H, W] (grayscale) or [H, W, 4] (RGBA). Row 0 is the TOP of the image."""
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    if arr.ndim == 2:
        h, w = arr.shape
        color_type = 0
        rows = arr.reshape(h, w)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        h, w = arr.shape[:2]
        color_type = 6
        rows = arr.reshape(h, w * 4)
    else:
        raise ValueError("encode_png expects [H,W] or [H,W,4] uint8")
    # filter byte 0 (None) prepended to each scanline
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), rows], axis=1).tobytes()
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))


def png_base64(arr: np.ndarray) -> str:
    return base64.b64encode(encode_png(arr)).decode("ascii")


def grayscale_png_base64(values: np.ndarray, vmin: float | None = None, vmax: float | None = None,
                         flip_vertical: bool = True) -> tuple[str, float, float]:
    """Scale float grid to 0..255 grayscale. flip_vertical=True because grid row 0 = SOUTH (image row 0 = top)."""
    v = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(v)
    if vmin is None:
        vmin = float(np.nanmin(v[finite])) if finite.any() else 0.0
    if vmax is None:
        vmax = float(np.nanmax(v[finite])) if finite.any() else 1.0
    span = (vmax - vmin) if vmax > vmin else 1.0
    g = np.clip((np.nan_to_num(v, nan=vmin) - vmin) / span, 0, 1) * 255.0
    g = g.astype(np.uint8)
    if flip_vertical:
        g = g[::-1, :]
    return png_base64(g), vmin, vmax


def depth_png_base64(depth_m: np.ndarray, max_depth_m: float | None = None, min_visible_m: float = 0.01,
                     flip_vertical: bool = True) -> tuple[str, float]:
    """Depth grid -> RGBA blue layer; alpha grows with depth (0 below min_visible_m, 255 at max)."""
    d = np.nan_to_num(np.asarray(depth_m, dtype=np.float64), nan=0.0)
    if max_depth_m is None or max_depth_m <= 0:
        max_depth_m = float(d.max()) if d.size else 0.0
    ny, nx = d.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    if max_depth_m > 0:
        frac = np.clip(d / max_depth_m, 0, 1)
        vis = d >= min_visible_m
        # blue ramp: light (120,180,255) shallow -> dark (0,40,160) deep
        rgba[..., 0] = np.where(vis, (120 * (1 - frac)).astype(np.uint8), 0)
        rgba[..., 1] = np.where(vis, (180 - 140 * frac).astype(np.uint8), 0)
        rgba[..., 2] = np.where(vis, (255 - 95 * frac).astype(np.uint8), 0)
        rgba[..., 3] = np.where(vis, (60 + 195 * frac).astype(np.uint8), 0)
    if flip_vertical:
        rgba = rgba[::-1, :, :]
    return png_base64(rgba), float(max_depth_m)
