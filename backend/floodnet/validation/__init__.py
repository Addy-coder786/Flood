"""Offline validation / cross-check tools. Nothing here is imported by the live solver."""
try:  # optional: pyswmm may be absent
    from .swmm_adapter import write_inp  # noqa: F401
except Exception:  # pragma: no cover
    pass
